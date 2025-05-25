# shorts_app/views.py

import os
import uuid
import json
import logging
import re
from urllib.parse import urlparse, parse_qs

from django.shortcuts import render
from django.conf import settings
from django.http import JsonResponse, FileResponse, Http404
from django.core.cache import cache
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip
from moviepy.video.fx.all import crop
from PIL import Image, ImageDraw, ImageFont

from .models import DownloadedVideo
import google.generativeai as genai

logger = logging.getLogger(__name__)

# --- Configure Gemini at the module level ---
try:
    if settings.GEMINI_API_KEY:
        genai.configure(api_key=settings.GEMINI_API_KEY)
except (AttributeError, NameError):
    logger.error("GEMINI_API_KEY not found in Django settings. AI features will be disabled.")
    genai = None


def get_ai_suggested_clips(transcript: str, video_duration: int):
    """
    Uses the Gemini API to find the best clips from a video transcript,
    with stricter validation instructions.
    """
    if not genai:
        logger.warning("Gemini API not configured. Skipping AI clip suggestion.")
        return []

    model = genai.GenerativeModel('gemini-1.5-flash')

    # This new prompt is much more direct and adds multiple checks to prevent the AI
    # from suggesting times that are out of the video's bounds.
    prompt = f"""
    You are an expert video editor specializing in creating viral short-form clips.
    Your task is to analyze the following video transcript and identify up to 3 of the most engaging, climactic, or interesting segments that would make a great short video. Each clip should ideally be between 30 and 90 seconds long.

    The total video duration is {video_duration} seconds. This is a STRICT LIMIT.

    Analyze the text for moments of high emotion, key revelations, surprising statements, or strong calls to action.

    Here is the full transcript:
    ---
    {transcript}
    ---

    Your response MUST be a valid JSON array of objects. Each object must contain 'start_time', 'end_time' (in "MM:SS" format), and a brief 'description'.

    **CRITICAL INSTRUCTION:** Before you output the JSON, you MUST verify that the 'start_time' and 'end_time' for every clip are LESS THAN the total video duration of {video_duration} seconds.

    NEVER suggest a clip where the start time or end time exceeds the video's total length. This is your most important constraint.
    """

    try:
        logger.info("Sending transcript to Gemini API with stricter prompt for clip suggestions...")
        response = model.generate_content(prompt)

        # Clean the response to ensure it's valid JSON by removing markdown formatting
        json_response_text = response.text.strip().replace("```json", "").replace("```", "")

        suggested_clips = json.loads(json_response_text)
        logger.info(f"Received {len(suggested_clips)} clip suggestions from Gemini.")
        return suggested_clips
    except Exception as e:
        logger.error(f"Error calling Gemini API or parsing its response: {e}", exc_info=True)
        return []

# --- Helper function to extract YouTube video ID (Unchanged) ---
def get_youtube_id(url):
    if url is None: return None
    query = urlparse(url)
    if query.hostname == 'youtu.be': return query.path[1:]
    if query.hostname in ('www.youtube.com', 'youtube.com'):
        if query.path == '/watch':
            p = parse_qs(query.query)
            return p.get('v', [None])[0]
        if query.path[:7] == '/embed/': return query.path.split('/')[2]
        if query.path[:3] == '/v/': return query.path.split('/')[2]
    return None


# --- NEW: View to list videos and show the main page ---
def index(request):
    downloaded_videos = DownloadedVideo.objects.all().order_by('-created_at')
    return render(request, 'shorts_app/index.html', {'videos': downloaded_videos})


# --- NEW: View to check the progress of a task ---
def check_progress(request, task_id):
    progress = cache.get(task_id, {"status": "PENDING", "progress": 0, "message": "Starting..."})
    return JsonResponse(progress)


# Full corrected function to copy-paste into views.py

# shorts_app/views.py

# Replace your entire process_video function with this one.

def process_video(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})

    video_url = request.POST.get('video_url')
    if not video_url:
        return JsonResponse({'status': 'error', 'message': 'Video URL is required.'})

    video_id = get_youtube_id(video_url)
    if not video_id:
        return JsonResponse({'status': 'error', 'message': 'Invalid YouTube URL provided.'})

    task_id = str(uuid.uuid4())

    # --- NEW, MORE ROBUST progress_hook ---
    def progress_hook(d):
        if d['status'] == 'downloading':
            # This is the raw text from yt-dlp, e.g., '\x1b[0;94m  0.0\x1b[0m%'
            raw_percent_str = d.get('_percent_str', '0.0%')

            # Use a regular expression to remove all ANSI color codes.
            ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
            cleaned_percent_str = ansi_escape.sub('', raw_percent_str)

            # Now, clean and convert the string that has no color codes.
            percentage = cleaned_percent_str.replace('%', '').strip()

            try:
                progress_float = float(percentage)
            except ValueError:
                progress_float = 0.0  # Default to 0 if conversion fails for any reason

            progress = {
                "status": "downloading",
                "progress": progress_float,
                "message": f"Downloading: {d.get('_total_bytes_str', 'N/A')} at {d.get('_speed_str', 'N/A')}"
            }
            cache.set(task_id, progress, timeout=300)

        elif d['status'] == 'finished':
            progress = {"status": "processing", "progress": 100, "message": "Download finished, processing video..."}
            cache.set(task_id, progress, timeout=300)

    try:
        video_record = DownloadedVideo.objects.get(video_id=video_id)
        video_full_path = os.path.join(settings.BASE_DIR, video_record.file_path.lstrip('/'))
        if not os.path.exists(video_full_path):
            video_record.delete()
            raise DownloadedVideo.DoesNotExist
    except DownloadedVideo.DoesNotExist:
        output_dir = os.path.join(settings.MEDIA_ROOT, 'videos')
        os.makedirs(output_dir, exist_ok=True)
        video_path_template = os.path.join(output_dir, video_id)

        # We keep the 'nocolor' option just in case, but our new code doesn't rely on it.
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
            'outtmpl': f'{video_path_template}.%(ext)s',
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en'],
            'subtitlesformat': 'vtt',
            'writethumbnail': True,
            'progress_hooks': [progress_hook],
            'nocolor': True,
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(video_url, download=True)
                video_title = info_dict.get('title', 'No Title')
                video_duration = info_dict.get('duration', 0)

            relative_video_path = f'/media/videos/{video_id}.mp4'
            # yt-dlp can save thumbnails as webp, jpg, etc. Let's find what it saved.
            thumb_path = next((os.path.join(output_dir, f) for f in os.listdir(output_dir) if
                               f.startswith(video_id) and f != f'{video_id}.mp4'), None)
            relative_thumb_path = f'/media/videos/{os.path.basename(thumb_path)}' if thumb_path else None

            video_record, created = DownloadedVideo.objects.update_or_create(
                video_id=video_id,
                defaults={
                    'title': video_title,
                    'duration': video_duration,
                    'file_path': relative_video_path,
                    'thumbnail_path': relative_thumb_path
                }
            )
        except Exception as e:
            logger.error(f"Error during video download or processing for URL {video_url}: {e}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'Error during download: {str(e)}', 'task_id': task_id})

    # AI Suggestions
    cache.set(task_id, {"status": "processing", "progress": 100, "message": "Analyzing transcript for clips..."},
              timeout=300)
    transcript_path = os.path.join(settings.MEDIA_ROOT, 'videos', f'{video_id}.en.vtt')
    suggested_clips = []

    if os.path.exists(transcript_path):
        with open(transcript_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            text_lines = [re.sub(r'<[^>]+>', '', line).strip() for line in lines if
                          not re.match(r'^\d{2}:\d{2}:\d{2}\.\d{3}', line) and 'WEBVTT' not in line and line.strip()]
            transcript = "\n".join(text_lines)
        suggested_clips = get_ai_suggested_clips(transcript, video_record.duration)

    cache.set(task_id, {"status": "complete", "message": "Processing complete!"}, timeout=300)

    final_clips = [
        {'start': clip.get('start_time'), 'end': clip.get('end_time'), 'description': clip.get('description')} for clip
        in suggested_clips]

    return JsonResponse({
        'status': 'success',
        'task_id': task_id,
        'video_id': video_id,
        'video_path': video_record.file_path,
        'video_title': video_record.title,
        'video_duration': video_record.duration,
        'suggested_clips': final_clips,
        'thumbnail_url': video_record.thumbnail_path
    })

# --- REWRITTEN: generate_short with Aspect Ratio and Progress ---
# --- REWRITTEN generate_short function with validation ---
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
                parts = [int(p) for p in time_str.split(':')]
                if len(parts) == 3: return parts[0] * 3600 + parts[1] * 60 + parts[2]
                if len(parts) == 2: return parts[0] * 60 + parts[1]
                return 0

            start_seconds = time_to_seconds(start_time_str)
            end_seconds = time_to_seconds(end_time_str)

            # --- NEW VALIDATION LOGIC ---
            with VideoFileClip(video_full_path) as original_clip:
                clip_duration = original_clip.duration

                if start_seconds >= clip_duration:
                    return JsonResponse({'status': 'error',
                                         'message': f'Invalid start time. Start time ({start_seconds}s) is after the video ends ({clip_duration}s).'})

                if start_seconds >= end_seconds:
                    return JsonResponse({'status': 'error', 'message': 'Start time must be less than end time.'})

                # Cap the end time to the actual duration of the clip to prevent errors
                end_seconds = min(end_seconds, clip_duration)

                # Create the subclip using the validated times
                clip = original_clip.subclip(start_seconds, end_seconds)

                (w, h) = clip.size

                # Aspect Ratio Logic
                if aspect_ratio == '9:16':
                    target_w = int(h * 9 / 16)
                    if w > target_w:
                        clip = crop(clip, width=target_w, x_center=w / 2)
                elif aspect_ratio == '16:9':
                    target_h = int(w * 9 / 16)
                    if h > target_h:
                        clip = crop(clip, height=target_h, y_center=h / 2)

                shorts_output_dir = os.path.join(settings.MEDIA_ROOT, 'shorts')
                os.makedirs(shorts_output_dir, exist_ok=True)
                short_filename = f'short_{uuid.uuid4()}.mp4'
                short_path = os.path.join(shorts_output_dir, short_filename)

                logger.info(f"Writing final short to: {short_path}")
                # Use the new, safely-created 'clip' object
                clip.write_videofile(short_path, codec="libx264", audio_codec="aac")

            return JsonResponse({
                'status': 'success',
                'message': 'Short created successfully!',
                'short_url': f'/media/shorts/{short_filename}',
            })
        except Exception as e:
            logger.error(f"An unexpected error occurred during short generation: {e}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'Error generating short: {str(e)}'})

    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})

# --- NEW: View to delete a video and its files ---
def delete_video(request, video_id):
    if request.method == 'POST':
        try:
            video_record = DownloadedVideo.objects.get(video_id=video_id)

            # Delete associated files
            paths_to_delete = [
                video_record.file_path,
                video_record.thumbnail_path,
                f'media/videos/{video_id}.en.vtt'  # Transcript
            ]
            for rel_path in paths_to_delete:
                if rel_path:
                    full_path = os.path.join(settings.BASE_DIR, rel_path.lstrip('/'))
                    if os.path.exists(full_path):
                        os.remove(full_path)
                        logger.info(f"Deleted file: {full_path}")

            video_record.delete()
            return JsonResponse({'status': 'success', 'message': 'Video deleted successfully.'})
        except DownloadedVideo.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Video not found.'}, status=404)
        except Exception as e:
            logger.error(f"Error deleting video {video_id}: {e}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'Error: {str(e)}'}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})


# --- download_short view (Unchanged) ---
def download_short(request, filename):
    short_path = os.path.join(settings.MEDIA_ROOT, 'shorts', filename)
    if os.path.exists(short_path):
        return FileResponse(open(short_path, 'rb'), as_attachment=True, filename=filename)
    else:
        raise Http404("Short not found")