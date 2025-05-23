import os
import uuid
import json
import logging
import re
from threading import Thread

from django.shortcuts import render, get_object_or_404, Http404
from django.conf import settings
from django.http import JsonResponse
from django.core.cache import cache
from django.utils import timezone
from django.views.decorators.http import require_POST

from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip
import moviepy.video.fx.all as vfx  # Import the effects module for the .fx() method

from .models import DownloadedVideo, YouTubeUpload
import google.generativeai as genai

logger = logging.getLogger(__name__)

# --- Gemini AI Configuration ---
try:
    if settings.GEMINI_API_KEY:
        genai.configure(api_key=settings.GEMINI_API_KEY)
except (AttributeError, NameError):
    logger.error("GEMINI_API_KEY not found in Django settings. AI features will be limited.")
    genai = None
except Exception as e:
    logger.error(f"An unexpected error occurred during Gemini configuration: {e}")
    genai = None


def get_ai_suggested_clips(transcript: str, video_duration: int):
    if not genai: return []
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    You are an expert video editor. Analyze the following transcript of a video that is {video_duration} seconds long.
    Identify up to 3 of the most engaging segments for a short-form video (30-90 seconds).
    Your response MUST be a valid JSON array of objects with keys 'start_time', 'end_time' (in "MM:SS" format), and a 'description'.
    Example: [{{"start_time": "02:15", "end_time": "03:05", "description": "The main discovery is revealed."}}]
    """
    try:
        response = model.generate_content(prompt)
        json_response_text = re.search(r'\[.*\]', response.text, re.DOTALL).group(0)
        suggested_clips_from_ai = json.loads(json_response_text)
        final_clips = [
            {'start': clip.get('start_time'), 'end': clip.get('end_time'), 'description': clip.get('description')} for
            clip in suggested_clips_from_ai]
        return final_clips
    except Exception as e:
        logger.error(f"Error calling Gemini API for suggestions: {e}")
        return []


def update_progress(job_id, status, progress, message="", result=None):
    cache.set(job_id, {'status': status, 'progress': progress, 'message': message, 'result': result}, timeout=3600)


def index(request):
    recent_videos = DownloadedVideo.objects.order_by('-downloaded_at')[:10]
    for video in recent_videos:
        video.suggestions_json = json.dumps(video.suggestions) if video.suggestions else '[]'
    return render(request, 'shorts_app/index.html', {'recent_videos': recent_videos})


def start_download_job(request):
    if request.method != 'POST': return JsonResponse({'status': 'error'}, status=405)
    try:
        video_url = json.loads(request.body).get('video_url')
        if not video_url: return JsonResponse({'status': 'error', 'message': 'Video URL required.'}, status=400)
        job_id = f"download_{uuid.uuid4()}"
        thread = Thread(target=download_worker, args=(job_id, video_url))
        thread.start()
        return JsonResponse({'status': 'success', 'job_id': job_id})
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON.'}, status=400)


def start_generate_job(request):
    if request.method != 'POST': return JsonResponse({'status': 'error'}, status=405)
    try:
        data = json.loads(request.body)
        job_id = f"generate_{uuid.uuid4()}"
        thread = Thread(target=generate_worker, args=(job_id, data))
        thread.start()
        return JsonResponse({'status': 'success', 'job_id': job_id})
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON.'}, status=400)


def check_job_status(request):
    job_id = request.GET.get('job_id')
    if not job_id: return JsonResponse({'status': 'error', 'message': 'job_id is required.'}, status=400)
    job_data = cache.get(job_id)
    if not job_data:
        return JsonResponse({'status': 'not_found', 'message': 'Job not found or expired.'})
    return JsonResponse(job_data)


def get_quota_status(request):
    uploads_today = YouTubeUpload.objects.filter(uploaded_at__date=timezone.now().date()).count()
    return JsonResponse({'status': 'success', 'uploads_today': uploads_today, 'daily_limit': 7})


@require_POST
def delete_video(request, video_id_str):
    logger.info(f"Request to delete video_id: {video_id_str}")
    try:
        video_record = get_object_or_404(DownloadedVideo, video_id=video_id_str)
        # Reconstruct path from MEDIA_ROOT and the file's basename for security
        if video_record.file_path:
            video_full_path = os.path.join(settings.MEDIA_ROOT, 'videos', os.path.basename(video_record.file_path))
            if os.path.exists(video_full_path):
                os.remove(video_full_path)
                logger.info(f"Deleted file: {video_full_path}")
        # Also delete the transcript
        vtt_full_path = os.path.join(settings.MEDIA_ROOT, 'videos', f"{video_record.video_id}.en.vtt")
        if os.path.exists(vtt_full_path):
            os.remove(vtt_full_path)
            logger.info(f"Deleted VTT: {vtt_full_path}")
        video_record.delete()
        logger.info(f"Deleted DB record for video_id: {video_id_str}")
        return JsonResponse({'status': 'success', 'message': f"Video '{video_record.title}' deleted."})
    except Http404:
        return JsonResponse({'status': 'error', 'message': 'Video not found.'}, status=404)
    except Exception as e:
        logger.error(f"Error deleting video {video_id_str}: {e}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': f"An unexpected error occurred: {str(e)}"}, status=500)


def download_worker(job_id, video_url):
    # This function is stable.
    update_progress(job_id, 'processing', 0, 'Starting download...')

    def ydl_progress_hook(d):
        if d['status'] == 'downloading':
            try:
                progress = float(d['_percent_str'].strip('% '))
                update_progress(job_id, 'processing', progress, f"Downloading: {int(progress)}%")
            except (ValueError, KeyError):
                pass

    ydl_opts = {
        'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
        'outtmpl': os.path.join(settings.MEDIA_ROOT, 'videos', '%(id)s.%(ext)s'),
        'merge_output_format': 'mp4', 'noplaylist': True, 'progress_hooks': [ydl_progress_hook],
        'writesubtitles': True, 'writeautomaticsub': True, 'subtitleslangs': ['en'], 'subtitlesformat': 'vtt',
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            video_id = info_dict['id']
            file_path_relative = f'videos/{video_id}.mp4'
            file_full_path = os.path.join(settings.MEDIA_ROOT, file_path_relative)
            video_record, created = DownloadedVideo.objects.update_or_create(
                video_id=video_id,
                defaults={
                    'title': info_dict.get('title', 'No Title'), 'duration': info_dict.get('duration', 0),
                    'file_path': file_path_relative, 'thumbnail_url': info_dict.get('thumbnail'),
                    'downloaded_at': timezone.now()
                })
            if created or not os.path.exists(file_full_path):
                ydl.download([video_url])
        update_progress(job_id, 'processing', 95, 'Analyzing transcript...')
        transcript_path = os.path.join(settings.MEDIA_ROOT, 'videos', f'{video_id}.en.vtt')
        suggested_clips = []
        if os.path.exists(transcript_path):
            with open(transcript_path, 'r', encoding='utf-8') as f:
                transcript = f.read()
            suggested_clips.extend(get_ai_suggested_clips(transcript, video_record.duration))
        video_record.suggestions = suggested_clips
        video_record.save()
        result_data = {
            'video_path': video_record.file_path, 'video_title': video_record.title,
            'video_duration': video_record.duration, 'suggested_clips': video_record.suggestions
        }
        update_progress(job_id, 'completed', 100, 'Analysis complete!', result=result_data)
    except Exception as e:
        logger.error(f"Download job {job_id} failed: {e}", exc_info=True)
        update_progress(job_id, 'failed', 0, f"Error: {str(e)}")


def generate_worker(job_id, data):
    update_progress(job_id, 'processing', 0, 'Initializing video generation...')
    logger.info(f"Generate job {job_id} started with data: {data}")

    original_clip = None
    final_clip = None

    try:
        video_relative_path = data.get('video_path')
        start_time_str = data.get('start_time')
        end_time_str = data.get('end_time')
        video_format = data.get('video_format', 'standard')

        def time_to_seconds(t_str):
            parts = list(map(int, t_str.split(':')))
            return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))

        start_seconds = time_to_seconds(start_time_str)
        end_seconds = time_to_seconds(end_time_str)

        if start_seconds >= end_seconds:
            raise ValueError(f"Start time ({start_time_str}) must be before end time ({end_time_str}).")

        full_path = os.path.join(settings.MEDIA_ROOT, video_relative_path)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Source video not found: {full_path}")

        update_progress(job_id, 'processing', 10, 'Loading and trimming video...')
        original_clip = VideoFileClip(full_path)
        sub_clip = original_clip.subclip(start_seconds, min(end_seconds, original_clip.duration))

        # --- Stable Processing Pipeline ---
        video_stream = sub_clip.without_audio()
        audio_stream = sub_clip.audio

        update_progress(job_id, 'processing', 30, 'Resizing and formatting video stream...')
        processed_video_stream = None

        if video_format == 'short':
            target_w, target_h = 1080, 1920
            resized_stream = video_stream.resize(
                height=target_h) if video_stream.w / video_stream.h > target_w / target_h else video_stream.resize(
                width=target_w)
            processed_video_stream = resized_stream.fx(vfx.crop,
                                                       width=target_w,
                                                       height=target_h,
                                                       x_center=resized_stream.w / 2,
                                                       y_center=resized_stream.h / 2)
        else:
            processed_video_stream = video_stream.resize(width=1920) if video_stream.w > 1920 else video_stream

        if processed_video_stream and audio_stream:
            final_clip = processed_video_stream.set_audio(audio_stream)
        else:
            final_clip = processed_video_stream

        if not final_clip:
            raise ValueError("Video processing failed, resulting in an empty clip.")

        output_dir = os.path.join(settings.MEDIA_ROOT, 'shorts')
        os.makedirs(output_dir, exist_ok=True)
        filename = f'generated_{uuid.uuid4()}.mp4'
        output_path = os.path.join(output_dir, filename)

        class MoviePyProgressLogger:
            def __init__(self, job_id):
                self.job_id = job_id

            def __call__(self, *args, **kwargs):
                pass

            def bars_callback(self, bar_name, current_index, total_count):
                if total_count > 0:
                    progress = 50 + (current_index / total_count) * 50
                    update_progress(self.job_id, 'processing', progress, f"Rendering: {int(progress)}%")

            def iter_bar(self, *args, **kwargs):
                iterable = kwargs.get('chunk', args[0] if args else [])
                for i in iterable: yield i

        update_progress(job_id, 'processing', 50, 'Rendering final video file (single-threaded)...')
        final_clip.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            # --- CHANGE: Disabled parallel processing for stability as requested ---
            threads=1,
            preset='medium',
            logger=MoviePyProgressLogger(job_id),
            ffmpeg_params=['-pix_fmt', 'yuv420p']
        )

        logger.info(f"Generate job {job_id} complete. Output: {output_path}")
        result_url = f'{settings.MEDIA_URL}shorts/{filename}'
        update_progress(job_id, 'completed', 100, 'Video generated!', result={'short_url': result_url})

    except Exception as e:
        logger.error(f"Generate job {job_id} CRITICAL FAIL: {e}", exc_info=True)
        update_progress(job_id, 'failed', 0, f"Error: {str(e)}")
    finally:
        # Gracefully close all clips to release file locks
        if sub_clip: sub_clip.close()
        if original_clip: original_clip.close()
        if final_clip: final_clip.close()