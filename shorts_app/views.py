import os
import uuid
import json
import logging
import re
import threading
import webvtt  # For robust VTT parsing
from urllib.parse import urlparse, parse_qs

from django.shortcuts import render
from django.conf import settings
from django.http import JsonResponse, FileResponse, Http404
from django.core.cache import cache
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip
from moviepy.video.fx.all import crop

from .models import DownloadedVideo
from . import youtube_uploader  # For YouTube uploads
import google.generativeai as genai

logger = logging.getLogger(__name__)

# --- Configure Gemini ---
genai_configured = False
try:
    if settings.GEMINI_API_KEY:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        genai_configured = True
        logger.info("Gemini API configured successfully.")
    else:
        logger.error("GEMINI_API_KEY is not set in Django settings. AI features will be disabled.")
except Exception as e:
    logger.error(f"Error during Gemini API configuration: {e}. AI features will be disabled.")
    genai = None


def get_ai_suggested_clips(transcript: str, video_duration: int):
    if not genai_configured or not genai:
        logger.warning("Gemini API not configured or key missing. Skipping AI clip suggestion.")
        return []

    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    You are an expert video editor and SEO specialist, adept at creating viral short-form clips.
    Your task is to analyze the following video transcript and identify up to 3 of the most engaging, climactic, or interesting segments that would make a great short video. Each clip should ideally be between 30 and 90 seconds long.

    The total video duration is {video_duration} seconds. This is a STRICT LIMIT.

    For each suggested clip, provide:
    1. 'start_time' (in "MM:SS" format)
    2. 'end_time' (in "MM:SS" format)
    3. A brief 'description' of why the clip is compelling.
    4. An array of 3-5 relevant 'keywords' (as strings) for SEO, tailored to the content of that specific clip.

    Here is the full transcript:
    ---
    {transcript}
    ---

    Your response MUST be a valid JSON array of objects. 
    **CRITICAL INSTRUCTION:** Before you output the JSON, you MUST verify that the 'start_time' and 'end_time' for every clip are LESS THAN the total video duration of {video_duration} seconds.
    Convert all timestamps to seconds to verify before including them in your response. Do not suggest clips that are out of bounds.
    If you cannot find suitable clips meeting all criteria, return an empty array.

    Example format for a single clip object:
    {{
        "start_time": "02:15",
        "end_time": "03:05",
        "description": "The speaker reveals the main discovery of their research.",
        "keywords": ["research discovery", "science breakthrough", "key finding", "scientific revelation"]
    }}
    """
    try:
        logger.info("Sending transcript to Gemini API for clip suggestions and keywords...")
        response = model.generate_content(prompt)
        json_response_text = response.text.strip().replace("```json", "").replace("```", "")
        raw_clips = json.loads(json_response_text)

        valid_clips = []
        if isinstance(raw_clips, list):
            for clip_data in raw_clips:
                if (isinstance(clip_data, dict) and
                        'start_time' in clip_data and
                        'end_time' in clip_data and
                        'description' in clip_data and
                        'keywords' in clip_data and isinstance(clip_data['keywords'], list)):
                    valid_clips.append(clip_data)
                else:
                    logger.warning(f"Malformed clip suggestion from AI discarded: {clip_data}")
        logger.info(f"Received and validated {len(valid_clips)} clip suggestions from Gemini.")
        return valid_clips
    except Exception as e:
        logger.error(f"Error calling Gemini API or parsing its response: {e}", exc_info=True)
        return []


def get_youtube_id(url):
    if url is None: return None
    query = urlparse(url)
    if query.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):  # youtube.com and m.youtube.com
        if query.path == '/watch':
            p = parse_qs(query.query)
            return p.get('v', [None])[0]
        if query.path.startswith('/embed/'):
            return query.path.split('/embed/')[1].split('?')[0]
        if query.path.startswith('/v/'):
            return query.path.split('/v/')[1].split('?')[0]
    if query.hostname == 'youtu.be':  # youtu.be
        return query.path[1:].split('?')[0]
    return None


def index(request):
    downloaded_videos = DownloadedVideo.objects.all().order_by('-created_at')
    return render(request, 'shorts_app/index.html', {'videos': downloaded_videos})


def check_progress(request, task_id):
    progress = cache.get(task_id, {"status": "PENDING", "progress": 0, "message": "Starting..."})
    return JsonResponse(progress)


def stop_task_view(request, task_id):
    if request.method == 'POST':
        logger.info(f"Received stop signal for task: {task_id}")
        cache.set(f"stop_task_{task_id}", True, timeout=600)
        return JsonResponse({'status': 'success', 'message': 'Stop signal sent.'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})


def process_video(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})

    video_url = request.POST.get('video_url')
    video_id_from_post = request.POST.get('video_id')
    video_id = video_id_from_post or (get_youtube_id(video_url) if video_url else None)

    if not video_id:
        return JsonResponse({'status': 'error', 'message': 'A valid YouTube URL or Video ID is required.'})

    task_id = str(uuid.uuid4())
    cache.set(f"stop_task_{task_id}", False, timeout=600)

    def long_running_video_processing():
        video_record_thread = None

        def check_stop_signal():
            return cache.get(f"stop_task_{task_id}", False)

        def progress_hook(d):
            if check_stop_signal():
                raise Exception("STOP_PROCESSING_USER_REQUEST")

            if d['status'] == 'downloading':
                raw_percent_str = d.get('_percent_str', '0.0%')
                ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
                cleaned_percent_str = ansi_escape.sub('', raw_percent_str)
                percentage_str = cleaned_percent_str.replace('%', '').strip()
                try:
                    progress_float = float(percentage_str)
                except ValueError:
                    progress_float = 0.0
                cache.set(task_id, {"status": "downloading", "progress": progress_float,
                                    "message": f"Downloading: {d.get('_total_bytes_str', 'N/A')} at {d.get('_speed_str', 'N/A')}"},
                          timeout=600)
            elif d['status'] == 'finished':
                cache.set(task_id,
                          {"status": "processing", "progress": 100, "message": "Download finished, processing..."},
                          timeout=600)

        try:
            video_record_thread = DownloadedVideo.objects.get(video_id=video_id)
            if video_record_thread.suggestions and isinstance(video_record_thread.suggestions, list) and len(
                    video_record_thread.suggestions) > 0:
                logger.info(f"Found valid stored suggestions for video {video_id}.")
            else:
                logger.info(f"No valid stored suggestions for {video_id}. Generating new ones.")
                raise ValueError("Suggestions need to be generated.")

        except (DownloadedVideo.DoesNotExist, ValueError):
            try:
                try:
                    video_record_thread = DownloadedVideo.objects.get(video_id=video_id)
                    video_full_path = os.path.join(settings.BASE_DIR, video_record_thread.file_path.lstrip('/'))
                    if not os.path.exists(video_full_path):
                        raise DownloadedVideo.DoesNotExist
                except DownloadedVideo.DoesNotExist:
                    if not video_url:
                        cache.set(task_id, {'status': 'error',
                                            'message': f'Record for {video_id} not found and no URL to download.'},
                                  timeout=600)
                        return

                    logger.info(f"Downloading video {video_id} from URL: {video_url}")
                    output_dir = os.path.join(settings.MEDIA_ROOT, 'videos')
                    os.makedirs(output_dir, exist_ok=True)
                    video_path_template = os.path.join(output_dir, video_id)

                    ydl_opts = {
                        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
                        'outtmpl': f'{video_path_template}.%(ext)s', 'merge_output_format': 'mp4',
                        'noplaylist': True, 'writesubtitles': True, 'writeautomaticsub': True,
                        'subtitleslangs': ['en'], 'subtitlesformat': 'vtt', 'writethumbnail': True,
                        'progress_hooks': [progress_hook], 'nocolor': True,
                    }

                    if check_stop_signal():
                        cache.set(task_id, {'status': 'stopped', 'message': 'Processing stopped by user.'}, timeout=600)
                        return

                    with YoutubeDL(ydl_opts) as ydl:
                        info_dict = ydl.extract_info(video_url, download=True)

                    video_title = info_dict.get('title', 'No Title')
                    video_duration = info_dict.get('duration', 0)
                    relative_video_path = f'/media/videos/{video_id}.mp4'
                    thumb_fs_path = next((os.path.join(output_dir, f) for f in os.listdir(output_dir) if
                                          f.startswith(video_id) and f.lower().endswith(
                                              ('.webp', '.jpg', '.png')) and not f.endswith('.mp4') and not f.endswith(
                                              '.vtt')), None)
                    relative_thumb_path = f'/media/videos/{os.path.basename(thumb_fs_path)}' if thumb_fs_path else None

                    video_record_thread, _ = DownloadedVideo.objects.update_or_create(
                        video_id=video_id,
                        defaults={'title': video_title, 'duration': video_duration, 'file_path': relative_video_path,
                                  'thumbnail_path': relative_thumb_path, 'suggestions': None}
                    )

                if check_stop_signal():
                    cache.set(task_id, {'status': 'stopped', 'message': 'Processing stopped by user.'}, timeout=600)
                    return

                cache.set(task_id, {"status": "processing", "progress": 100, "message": "Analyzing transcript..."},
                          timeout=600)
                transcript_path = os.path.join(settings.MEDIA_ROOT, 'videos', f'{video_id}.en.vtt')
                suggested_clips = []

                if os.path.exists(transcript_path):
                    try:
                        captions = webvtt.read(transcript_path)
                        transcript_texts = [c.text.strip().replace('\n', ' ') for c in captions if c.text.strip()]
                        transcript = "\n".join(transcript_texts)
                        if transcript:
                            video_record_thread = DownloadedVideo.objects.get(video_id=video_id)
                            ai_clips_from_gemini = get_ai_suggested_clips(transcript, video_record_thread.duration)
                            if ai_clips_from_gemini:
                                suggested_clips = ai_clips_from_gemini
                            if not suggested_clips:
                                logger.warning(f"AI returned no valid clips for {video_id}.")
                        else:
                            logger.warning(f"Transcript for {video_id} was empty after parsing with webvtt.")
                    except Exception as e:
                        logger.error(f"Failed to parse VTT file {transcript_path} or call AI: {e}", exc_info=True)

                if check_stop_signal():
                    cache.set(task_id, {'status': 'stopped', 'message': 'Processing stopped by user.'}, timeout=600)
                    return

                if not suggested_clips:
                    logger.warning(f"No valid AI suggestions for {video_id}. Generating fallback clips.")
                    if not video_record_thread: video_record_thread = DownloadedVideo.objects.get(video_id=video_id)
                    vid_duration = video_record_thread.duration
                    if vid_duration >= 15:
                        suggested_clips.append({'start_time': '00:00', 'end_time': '00:15',
                                                'description': 'Fallback: The first 15 seconds.',
                                                'keywords': ['intro', 'beginning']})
                    if vid_duration >= 30:
                        mid_point = vid_duration // 2
                        start_middle = max(0, mid_point - 10)
                        end_middle = min(vid_duration, mid_point + 10)
                        if end_middle > start_middle:
                            start_str = f'{int(start_middle // 60):02d}:{int(start_middle % 60):02d}'
                            end_str = f'{int(end_middle // 60):02d}:{int(end_middle % 60):02d}'
                            suggested_clips.append({'start_time': start_str, 'end_time': end_str,
                                                    'description': 'Fallback: A clip from the middle.',
                                                    'keywords': ['middle clip', 'highlight']})

                if not video_record_thread: video_record_thread = DownloadedVideo.objects.get(video_id=video_id)
                video_record_thread.suggestions = suggested_clips
                video_record_thread.save()
                logger.info(f"Saved {len(suggested_clips)} suggestions for {video_id} to database.")

            except Exception as e:
                if str(e) == "STOP_PROCESSING_USER_REQUEST":
                    logger.info(f"Task {task_id} download stopped by user.")
                    cache.set(task_id, {'status': 'stopped', 'message': 'Download stopped by user.'}, timeout=600)
                    return
                logger.error(f"Error during suggestion generation for {video_id}: {e}", exc_info=True)
                cache.set(task_id, {'status': 'error', 'message': f'Processing failed: {str(e)}'}, timeout=600)
                return

        if check_stop_signal():
            cache.set(task_id, {'status': 'stopped', 'message': 'Processing stopped by user.'}, timeout=600)
            return

        if not video_record_thread:  # Should always have a record by now if successful
            try:
                video_record_thread = DownloadedVideo.objects.get(video_id=video_id)
            except DownloadedVideo.DoesNotExist:
                cache.set(task_id, {'status': 'error', 'message': f'Critical error: Video record for {video_id} lost.'},
                          timeout=600)
                return

        success_data = {
            'status': 'complete', 'progress': 100, 'message': 'Processing Complete!',
            'result': {
                'status': 'success', 'video_id': video_id,
                'video_path': video_record_thread.file_path, 'video_title': video_record_thread.title,
                'video_duration': video_record_thread.duration, 'suggested_clips': video_record_thread.suggestions,
                'thumbnail_url': video_record_thread.thumbnail_path
            }
        }
        cache.set(task_id, success_data, timeout=600)

    thread = threading.Thread(target=long_running_video_processing)
    thread.start()
    return JsonResponse({'status': 'processing', 'task_id': task_id})


def generate_short(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            video_relative_path = data.get('video_path')
            start_time_str = data.get('start_time')
            end_time_str = data.get('end_time')
            aspect_ratio = data.get('aspect_ratio', '9:16')
            if not all([video_relative_path, start_time_str, end_time_str]):
                return JsonResponse({'status': 'error', 'message': 'Missing video path or trim times.'})

            video_full_path = os.path.join(settings.BASE_DIR, video_relative_path.lstrip('/'))
            if not os.path.exists(video_full_path):
                return JsonResponse({'status': 'error', 'message': 'Original video file not found.'})

            def time_to_seconds(time_str):
                if not time_str or time_str.lower() == "undefined":
                    raise ValueError(f"Invalid time string received: '{time_str}'")
                parts = [int(p) for p in time_str.split(':')]
                if len(parts) == 3: return parts[0] * 3600 + parts[1] * 60 + parts[2]
                if len(parts) == 2: return parts[0] * 60 + parts[1]
                raise ValueError(f"Time string '{time_str}' is not in MM:SS or HH:MM:SS format.")

            start_seconds = time_to_seconds(start_time_str)
            end_seconds = time_to_seconds(end_time_str)

            with VideoFileClip(video_full_path) as original_clip:
                clip_duration = original_clip.duration
                if start_seconds >= clip_duration:
                    return JsonResponse({'status': 'error',
                                         'message': f'Start time ({start_seconds}s) is after video ends ({clip_duration:.2f}s).'})
                if start_seconds >= end_seconds:
                    return JsonResponse({'status': 'error', 'message': 'Start time must be less than end time.'})
                end_seconds = min(end_seconds, clip_duration)
                if end_seconds <= start_seconds:
                    return JsonResponse({'status': 'error', 'message': 'Calculated clip duration is zero or negative.'})

                clip_to_render = original_clip.subclip(start_seconds, end_seconds)
                (w, h) = clip_to_render.size
                if aspect_ratio == '9:16':
                    target_w = int(h * 9 / 16)
                    if w > target_w: clip_to_render = crop(clip_to_render, width=target_w, x_center=w / 2)
                elif aspect_ratio == '16:9':
                    target_h = int(w * 9 / 16)
                    if h > target_h: clip_to_render = crop(clip_to_render, height=target_h, y_center=h / 2)

                shorts_output_dir = os.path.join(settings.MEDIA_ROOT, 'shorts')
                os.makedirs(shorts_output_dir, exist_ok=True)

                base_filename = f'short_{uuid.uuid4()}'
                short_filename_mp4 = f'{base_filename}.mp4'
                short_thumbnail_filename_png = f'{base_filename}_thumb.png'

                short_path_fs = os.path.join(shorts_output_dir, short_filename_mp4)
                short_thumbnail_path_fs = os.path.join(shorts_output_dir, short_thumbnail_filename_png)

                # Use more threads for faster encoding if available, and a faster preset
                clip_to_render.write_videofile(short_path_fs, codec="libx264", audio_codec="aac",
                                               threads=os.cpu_count(), preset="ultrafast")
                clip_to_render.save_frame(short_thumbnail_path_fs, t=max(0,
                                                                         clip_to_render.duration / 2))  # Ensure t is not negative for very short clips

            return JsonResponse({
                'status': 'success', 'message': 'Short created successfully!',
                'short_url': f'/media/shorts/{short_filename_mp4}',
                'short_thumbnail_url': f'/media/shorts/{short_thumbnail_filename_png}',
            })
        except ValueError as ve:
            logger.error(f"ValueError during short generation: {ve}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'Invalid time data: {str(ve)}'})
        except Exception as e:
            logger.error(f"Unexpected error during short generation: {e}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'Error generating short: {str(e)}'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})


def delete_video(request, video_id):
    if request.method == 'POST':
        try:
            video_record = DownloadedVideo.objects.get(video_id=video_id)
            paths_to_delete = []
            if video_record.file_path: paths_to_delete.append(video_record.file_path)
            if video_record.thumbnail_path: paths_to_delete.append(video_record.thumbnail_path)
            # Transcript file has a predictable name structure
            transcript_rel_path = f'media/videos/{video_id}.en.vtt'
            paths_to_delete.append(transcript_rel_path)

            for rel_path in paths_to_delete:
                if rel_path:
                    # Construct full path relative to BASE_DIR
                    full_path = os.path.join(settings.BASE_DIR, rel_path.lstrip('/'))
                    if os.path.exists(full_path):
                        try:
                            os.remove(full_path)
                            logger.info(f"Deleted file: {full_path}")
                        except Exception as e:
                            logger.error(f"Failed to delete file {full_path}: {e}")
            video_record.delete()
            return JsonResponse({'status': 'success', 'message': 'Video deleted.'})
        except DownloadedVideo.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Video not found.'}, status=404)
        except Exception as e:
            logger.error(f"Error deleting video {video_id}: {e}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'Error: {str(e)}'}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})


def download_short(request, filename):
    short_path = os.path.join(settings.MEDIA_ROOT, 'shorts', filename)
    if os.path.exists(short_path):
        return FileResponse(open(short_path, 'rb'), as_attachment=True, filename=filename)
    else:
        raise Http404("Short not found")


def upload_to_youtube_view(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            short_relative_path = data.get('short_path', '').lstrip('/')
            title = data.get('title')
            description = data.get('description')
            tags_str = data.get('tags', '')
            tags = [tag.strip() for tag in tags_str.split(',') if tag.strip()]

            if not all([short_relative_path, title, description]):
                return JsonResponse({'status': 'error', 'message': 'Missing title, description, or file path.'})

            short_full_path = os.path.join(settings.BASE_DIR, short_relative_path)

            if not os.path.exists(short_full_path):
                return JsonResponse({'status': 'error', 'message': 'Generated short video file not found.'})

            result = youtube_uploader.upload_video(
                file_path=short_full_path,
                title=title,
                description=description,
                tags=tags
            )
            return JsonResponse(result)
        except Exception as e:
            logger.error(f"Error in upload_to_youtube_view: {e}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'An unexpected error occurred: {str(e)}'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})