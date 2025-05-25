import os
import uuid
import json
import logging
import re
import threading
import webvtt
from urllib.parse import urlparse, parse_qs

from django.shortcuts import render, get_object_or_404
from django.conf import settings
from django.http import JsonResponse, FileResponse, Http404
from django.core.cache import cache
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip, CompositeVideoClip, ColorClip
from moviepy.video.fx.all import crop, resize

from .models import DownloadedVideo, GeneratedShort
import google.generativeai as genai

logger = logging.getLogger(__name__)

# --- Configure Gemini (Unchanged) ---
genai_configured = False
try:
    if settings.GEMINI_API_KEY:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        genai_configured = True
    else:
        logger.error("GEMINI_API_KEY not set. AI features disabled.")
except Exception as e:
    logger.error(f"Error during Gemini API configuration: {e}. AI features disabled.")
    genai = None


# --- AI Suggestion, Get YouTube ID, Index, Check Progress (Unchanged) ---
def get_ai_suggested_clips(transcript: str, video_duration: int):
    # This function remains unchanged
    if not genai_configured or not genai: return []
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    You are an expert video editor and viral content strategist.
    Analyze the following transcript (total video duration: {video_duration} seconds) and identify up to 3 compelling segments for short videos (30-90 seconds).
    For each clip, provide:
    1.  `start_time`: In "MM:SS" format.
    2.  `end_time`: In "MM:SS" format.
    3.  `title`: A catchy, SEO-friendly title for the short video.
    4.  `description`: A brief, engaging description, including a call-to-action if appropriate.
    5.  `tags`: A JSON array of 5-7 relevant SEO keywords (strings).
    6.  `copyright_concern`: A boolean (true/false). Set to true if the text suggests copyrighted material.
    Your response MUST be a valid JSON array of objects. If no suitable clips are found, return an empty array [].
    """
    try:
        response = model.generate_content(prompt)
        json_response_text = response.text.strip().replace("```json", "").replace("```", "")
        raw_clips = json.loads(json_response_text)
        return [c for c in raw_clips if isinstance(c, dict) and all(k in c for k in ['start_time', 'title', 'tags'])]
    except Exception as e:
        logger.error(f"Error calling Gemini API: {e}", exc_info=True)
        return []


def get_youtube_id(url):
    # This function remains unchanged
    if not url: return None
    query = urlparse(url)
    if query.hostname in ('www.youtube.com', 'youtube.com'):
        if query.path == '/watch': return parse_qs(query.query).get('v', [None])[0]
        if query.path.startswith(('/embed/', '/v/')): return query.path.split('/')[2]
    if query.hostname == 'youtu.be': return query.path[1:]
    return None


def index(request):
    processed_videos = DownloadedVideo.objects.all().order_by('-created_at')
    generated_shorts = GeneratedShort.objects.select_related('parent_video').order_by('-created_at')
    return render(request, 'shorts_app/index.html', {'videos': processed_videos, 'shorts': generated_shorts})


def check_progress(request, task_id):
    return JsonResponse(cache.get(task_id, {"status": "PENDING", "progress": 0, "message": "Initializing..."}))


def process_video(request):
    if request.method != 'POST': return JsonResponse({'status': 'error', 'message': 'Invalid request.'})

    video_url = request.POST.get('video_url')
    video_id = request.POST.get('video_id') or get_youtube_id(video_url)
    if not video_id: return JsonResponse({'status': 'error', 'message': 'Valid YouTube URL or Video ID is required.'})

    task_id = str(uuid.uuid4())

    def long_running_task():
        # This nested function's logic is mostly the same, but the progress hook is updated.
        def progress_hook(d):
            if d['status'] == 'downloading':
                # Clean up the percentage string and send it back
                percent_str = d.get('_percent_str', '0.0%')
                cleaned_str = re.sub(r'\x1b\[[0-9;]*m', '', percent_str).replace('%', '').strip()
                try:
                    progress = float(cleaned_str)
                    cache.set(task_id,
                              {"status": "processing", "progress": progress, "message": "Downloading video..."})
                except ValueError:
                    pass  # Ignore if parsing fails
            elif d['status'] == 'finished':
                cache.set(task_id,
                          {"status": "processing", "progress": 100, "message": "Download complete. Analyzing..."})

        try:
            video_record = DownloadedVideo.objects.get(video_id=video_id)
            if not video_record.suggestions or not isinstance(video_record.suggestions, list):
                raise ValueError("Suggestions needed.")
            cache.set(task_id, {"status": "processing", "progress": 100,
                                "message": "Found existing video. Loading suggestions..."})
        except (DownloadedVideo.DoesNotExist, ValueError):
            try:
                video_full_path = os.path.join(settings.MEDIA_ROOT, 'videos', f'{video_id}.mp4')
                if not os.path.exists(video_full_path):
                    if not video_url:
                        cache.set(task_id, {'status': 'error',
                                            'message': f'Record for {video_id} not found and no URL provided.'})
                        return
                    output_dir = os.path.join(settings.MEDIA_ROOT, 'videos')
                    os.makedirs(output_dir, exist_ok=True)
                    ydl_opts = {
                        'format': 'best[height<=1080][ext=mp4]',
                        'outtmpl': os.path.join(output_dir, f'{video_id}.%(ext)s'),
                        'merge_output_format': 'mp4', 'noplaylist': True, 'writesubtitles': True,
                        'writeautomaticsub': True,
                        'subtitleslangs': ['en'], 'subtitlesformat': 'vtt', 'writethumbnail': True, 'nocolor': True,
                        'progress_hooks': [progress_hook],
                    }
                    with YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(video_url, download=True)
                    thumbnail_path = f'/media/videos/{video_id}.webp' if os.path.exists(
                        os.path.join(output_dir, f'{video_id}.webp')) else f'/media/videos/{video_id}.jpg'
                    video_record, _ = DownloadedVideo.objects.update_or_create(
                        video_id=video_id,
                        defaults={'title': info.get('title', 'N/A'), 'duration': info.get('duration', 0),
                                  'file_path': f'/media/videos/{video_id}.mp4', 'thumbnail_path': thumbnail_path}
                    )
                else:
                    video_record = get_object_or_404(DownloadedVideo, video_id=video_id)
                transcript_path = os.path.join(settings.MEDIA_ROOT, 'videos', f'{video_id}.en.vtt')
                suggested_clips = []
                if os.path.exists(transcript_path):
                    transcript = " ".join([c.text.strip().replace('\n', ' ') for c in webvtt.read(transcript_path)])
                    if transcript: suggested_clips = get_ai_suggested_clips(transcript, video_record.duration)
                video_record.suggestions = suggested_clips
                video_record.save()
            except Exception as e:
                cache.set(task_id, {'status': 'error', 'message': f'Processing failed: {e}'})
                return
        video_record = get_object_or_404(DownloadedVideo, video_id=video_id)
        cache.set(task_id, {'status': 'complete', 'result': {
            'video_id': video_id, 'video_title': video_record.title, 'suggested_clips': video_record.suggestions,
        }})

    threading.Thread(target=long_running_task).start()
    return JsonResponse({'status': 'processing', 'task_id': task_id})


def generate_short(request):
    if request.method != 'POST': return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})
    try:
        data = json.loads(request.body)
        video_id, clip_data, aspect_ratio = data.get('video_id'), data.get('clip_data', {}), data.get('aspect_ratio',
                                                                                                      '9:16')
        start_time_str, end_time_str = clip_data.get('start_time'), clip_data.get('end_time')
        parent_video = get_object_or_404(DownloadedVideo, video_id=video_id)
        video_full_path = os.path.join(settings.BASE_DIR, parent_video.file_path.lstrip('/'))

        def time_to_seconds(t):
            return sum(int(x) * 60 ** i for i, x in enumerate(reversed(t.split(':'))))

        start_s, end_s = time_to_seconds(start_time_str), time_to_seconds(end_time_str)

        with VideoFileClip(video_full_path) as video:
            subclip = video.subclip(start_s, min(end_s, video.duration))
            (w, h), final_clip = subclip.size, subclip

            if aspect_ratio == '9:16':
                target_w, target_h = 1080, 1920
                clip_resized = final_clip.resize(width=target_w)
                background = ColorClip(size=(target_w, target_h), color=(0, 0, 0))
                final_clip = CompositeVideoClip([background, clip_resized.set_position("center")], use_bgclip=True)
            elif aspect_ratio == '16:9':
                target_h = int(w * 9 / 16)
                if h > target_h: final_clip = crop(subclip, height=target_h, y_center=h / 2)

            shorts_dir = os.path.join(settings.MEDIA_ROOT, 'shorts')
            os.makedirs(shorts_dir, exist_ok=True)
            short_uuid = uuid.uuid4()

            # --- BUG FIX: SAVE AS PNG ---
            # Saving as PNG resolves the "cannot write mode RGBA as JPEG" error
            # because PNG supports the alpha (transparency) channel.
            short_filename = f'{short_uuid}.mp4'
            thumb_filename = f'{short_uuid}.png'
            short_path = os.path.join(shorts_dir, short_filename)
            thumb_path = os.path.join(shorts_dir, thumb_filename)

            final_clip.write_videofile(short_path, codec="libx264", audio_codec="aac", threads=os.cpu_count(),
                                       preset="medium")
            final_clip.save_frame(thumb_path, t=final_clip.duration / 2)

        GeneratedShort.objects.create(
            parent_video=parent_video, title=clip_data.get('title', 'Untitled Short'),
            description=clip_data.get('description', ''), tags=clip_data.get('tags', []),
            short_path=f'/media/shorts/{short_filename}', thumbnail_path=f'/media/shorts/{thumb_filename}',
            start_time=start_time_str, end_time=end_time_str
        )
        return JsonResponse({'status': 'success', 'message': 'Short created successfully!'})
    except Exception as e:
        logger.error(f"Error during short generation: {e}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': f'An unexpected error occurred: {e}'}, status=500)


# --- Deletion and Download views (Unchanged) ---
def _delete_files(paths):
    for rel_path in paths:
        if not rel_path: continue
        full_path = os.path.join(settings.BASE_DIR, rel_path.lstrip('/'))
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except OSError as e:
                logger.error(f"Failed to delete file {full_path}: {e}")


def delete_video(request, video_id):
    if request.method == 'POST':
        video = get_object_or_404(DownloadedVideo, video_id=video_id)
        _delete_files([video.file_path, video.thumbnail_path, f'/media/videos/{video.video_id}.en.vtt'])
        video.delete()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)


def delete_short(request, short_id):
    if request.method == 'POST':
        short = get_object_or_404(GeneratedShort, id=short_id)
        _delete_files([short.short_path, short.thumbnail_path])
        short.delete()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)


def download_short(request, filename):
    short_path = os.path.join(settings.MEDIA_ROOT, 'shorts', filename)
    if os.path.exists(short_path): return FileResponse(open(short_path, 'rb'), as_attachment=True, filename=filename)
    raise Http404("File not found")