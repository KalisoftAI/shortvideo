import os
import uuid
import json
import logging
import re
from threading import Thread
from datetime import date

from django.shortcuts import render
from django.conf import settings
from django.http import JsonResponse
from django.core.cache import cache
from django.utils import timezone

from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip
from moviepy.video.fx.all import crop

from .models import DownloadedVideo, YouTubeUpload
import google.generativeai as genai

logger = logging.getLogger(__name__)

# --- Gemini AI Configuration ---
try:
    if settings.GEMINI_API_KEY:
        genai.configure(api_key=settings.GEMINI_API_KEY)
except (AttributeError, NameError):
    logger.error("GEMINI_API_KEY not found. AI features will be limited.")
    genai = None


# --- AI HELPER ---
def get_ai_suggested_clips(transcript: str, video_duration: int):
    # This function is unchanged
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
        json_response_text = response.text.strip().replace("```json", "").replace("```", "")
        suggested_clips = json.loads(json_response_text)
        final_clips = [
            {'start': clip.get('start_time'), 'end': clip.get('end_time'), 'description': clip.get('description')} for
            clip in suggested_clips]
        return final_clips
    except Exception as e:
        logger.error(f"Error calling Gemini API: {e}", exc_info=True)
        return []


# --- JOB & PROGRESS HELPERS ---
def update_progress(job_id, status, progress, message="", result=None):
    cache.set(job_id, {'status': status, 'progress': progress, 'message': message, 'result': result}, timeout=3600)


# --- VIEWS ---
def index(request):
    recent_videos = DownloadedVideo.objects.order_by('-downloaded_at')[:10]
    return render(request, 'shorts_app/index.html', {'recent_videos': recent_videos})


def start_download_job(request):
    if request.method != 'POST': return JsonResponse({'status': 'error'}, status=405)
    video_url = json.loads(request.body).get('video_url')
    job_id = f"download_{uuid.uuid4()}"
    thread = Thread(target=download_worker, args=(job_id, video_url))
    thread.start()
    return JsonResponse({'status': 'success', 'job_id': job_id})


def start_generate_job(request):
    if request.method != 'POST': return JsonResponse({'status': 'error'}, status=405)
    data = json.loads(request.body)
    job_id = f"generate_{uuid.uuid4()}"
    thread = Thread(target=generate_worker, args=(job_id, data))
    thread.start()
    return JsonResponse({'status': 'success', 'job_id': job_id})


def check_job_status(request):
    job_id = request.GET.get('job_id')
    if not job_id: return JsonResponse({'status': 'error'}, status=400)
    return JsonResponse(cache.get(job_id, {'status': 'not_found'}))


def get_quota_status(request):
    uploads_today = YouTubeUpload.objects.filter(uploaded_at__date=timezone.now().date()).count()
    return JsonResponse({'status': 'success', 'uploads_today': uploads_today, 'daily_limit': 7})


# --- WORKER FUNCTIONS ---
def download_worker(job_id, video_url):
    # This function is unchanged
    update_progress(job_id, 'processing', 0, 'Starting...')

    def ydl_progress_hook(d):
        if d['status'] == 'downloading':
            percent_str = d['_percent_str'].strip('% ')
            try:
                progress = float(percent_str)
                update_progress(job_id, 'processing', progress, f"Downloading: {int(progress)}%")
            except ValueError:
                pass

    ydl_opts = {
        'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
        'outtmpl': os.path.join(settings.MEDIA_ROOT, 'videos', '%(id)s.%(ext)s'),
        'merge_output_format': 'mp4', 'noplaylist': True, 'progress_hooks': [ydl_progress_hook],
        'writesubtitles': True, 'writeautomaticsub': True, 'subtitleslangs': ['en'], 'subtitlesformat': 'vtt',
        'no_color': True,
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            video_id, title, duration, thumbnail = info_dict.get('id'), info_dict.get('title'), info_dict.get(
                'duration'), info_dict.get('thumbnail')
            if not DownloadedVideo.objects.filter(video_id=video_id).exists():
                ydl.download([video_url])
                DownloadedVideo.objects.update_or_create(video_id=video_id,
                                                         defaults={'title': title, 'duration': duration,
                                                                   'file_path': f'/media/videos/{video_id}.mp4',
                                                                   'thumbnail_url': thumbnail})
        update_progress(job_id, 'processing', 95, 'Analyzing transcript...')
        suggested_clips = []
        transcript_path = os.path.join(settings.MEDIA_ROOT, 'videos', f'{video_id}.en.vtt')
        if os.path.exists(transcript_path):
            with open(transcript_path, 'r', encoding='utf-8') as f: transcript = f.read()
            suggested_clips.extend(get_ai_suggested_clips(transcript, duration))
        if duration > 60: suggested_clips.append({'start': '00:00', 'end': '01:00', 'description': 'First 60 Seconds'})
        start_middle = max(0, (duration // 2) - 30);
        end_middle = min(duration, start_middle + 60)
        suggested_clips.append({'start': f'{int(start_middle // 60):02d}:{int(start_middle % 60):02d}',
                                'end': f'{int(end_middle // 60):02d}:{int(end_middle % 60):02d}',
                                'description': 'Action-Packed Middle'})
        result_data = {'video_path': f'/media/videos/{video_id}.mp4', 'video_title': title, 'video_duration': duration,
                       'suggested_clips': suggested_clips}
        update_progress(job_id, 'completed', 100, 'Analysis complete!', result=result_data)
    except Exception as e:
        logger.error(f"Download job {job_id} failed: {e}", exc_info=True)
        update_progress(job_id, 'failed', 0, f"Error: {str(e)}")


def generate_worker(job_id, data):
    update_progress(job_id, 'processing', 0, 'Preparing video...')
    try:
        video_path, start, end, video_format = data.get('video_path'), data.get('start_time'), data.get(
            'end_time'), data.get('video_format')

        # --- BUG FIX: Corrected MoviePyProgressLogger ---
        class MoviePyProgressLogger:
            def __init__(self, job_id):
                self.job_id = job_id

            # This method handles text messages from moviepy and safely ignores them
            def __call__(self, *args, **kwargs):
                pass

            # This method handles the progress bar updates
            def bars_callback(self, bar_name, current_index, total_count):
                progress = (current_index / total_count) * 100
                update_progress(self.job_id, 'processing', progress, f"Rendering: {int(progress)}%")

        def time_to_seconds(t):
            parts = list(map(int, t.split(':')))
            return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))

        full_path = os.path.join(settings.BASE_DIR, video_path.lstrip('/'))

        with VideoFileClip(full_path).subclip(time_to_seconds(start), time_to_seconds(end)) as clip:
            if video_format == 'short':
                w, h = clip.size;
                target_w, target_h = 1080, 1920
                clip_resized = clip.resize(height=target_h) if (w / h) > (target_w / target_h) else clip.resize(
                    width=target_w)
                final_clip = crop(clip_resized, width=target_w, height=target_h, x_center=clip_resized.w / 2,
                                  y_center=clip_resized.h / 2)
            else:
                final_clip = clip.resize(width=1920) if clip.w > 1920 else clip

            output_dir = os.path.join(settings.MEDIA_ROOT, 'shorts')
            os.makedirs(output_dir, exist_ok=True)
            filename = f'generated_{uuid.uuid4()}.mp4'
            output_path = os.path.join(output_dir, filename)

            progress_logger = MoviePyProgressLogger(job_id)
            final_clip.write_videofile(output_path, codec="libx264", audio_codec="aac", logger=progress_logger)

        update_progress(job_id, 'completed', 100, 'Video generated!', result={'short_url': f'/media/shorts/{filename}'})
    except Exception as e:
        logger.error(f"Generate job {job_id} failed: {e}", exc_info=True)
        update_progress(job_id, 'failed', 0, f"Error: {str(e)}")