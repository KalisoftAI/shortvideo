import os
import uuid
import json
import logging
from urllib.parse import urlparse, parse_qs

from django.shortcuts import render
from django.conf import settings
from django.http import JsonResponse, FileResponse, Http404
from django.core.files.base import ContentFile
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip
from PIL import Image, ImageDraw, ImageFont

# Import the model from your models.py
from .models import DownloadedVideo

logger = logging.getLogger(__name__)


# Helper function to extract YouTube video ID from a URL
def get_youtube_id(url):
    """Extracts the YouTube video ID from a URL."""
    if url is None:
        return None
    # Handles standard, shortened, and embed URLs
    query = urlparse(url)
    if query.hostname == 'youtu.be':
        return query.path[1:]
    if query.hostname in ('www.youtube.com', 'youtube.com'):
        if query.path == '/watch':
            p = parse_qs(query.query)
            return p.get('v', [None])[0]
        if query.path[:7] == '/embed/':
            return query.path.split('/')[2]
        if query.path[:3] == '/v/':
            return query.path.split('/')[2]
    return None


def index(request):
    return render(request, 'shorts_app/index.html')


# --- REWRITTEN VIDEO PROCESSING LOGIC ---
def process_video(request):
    if request.method != 'POST':
        logger.warning(f"Invalid request method ({request.method}) for process_video view.")
        return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})

    video_url = request.POST.get('video_url')
    logger.info(f"Received request to process video URL: {video_url}")

    if not video_url:
        logger.warning("Video URL not provided in the request.")
        return JsonResponse({'status': 'error', 'message': 'Video URL is required.'})

    video_id = get_youtube_id(video_url)
    if not video_id:
        logger.warning(f"Could not extract YouTube video ID from URL: {video_url}")
        return JsonResponse({'status': 'error', 'message': 'Invalid YouTube URL provided.'})

    # 1. CHECK THE DATABASE FIRST
    try:
        video_record = DownloadedVideo.objects.get(video_id=video_id)

        # Verify the file still exists on disk, otherwise the record is stale
        video_full_path = os.path.join(settings.BASE_DIR, video_record.file_path.lstrip('/'))
        if not os.path.exists(video_full_path):
            logger.warning(f"Found DB record for {video_id} but file was missing. Deleting record and re-downloading.")
            video_record.delete()
            raise DownloadedVideo.DoesNotExist  # Force a re-download

        logger.info(f"Video '{video_id}' found in cache. Skipping download.")
        # Create the same response structure as a fresh download
        suggested_clips = []
        if video_record.duration > 0:
            start_middle = max(0, (video_record.duration // 2) - 7)
            end_middle = min(video_record.duration, (video_record.duration // 2) + 8)
            suggested_clips.append({'start': f'{int(start_middle // 60):02d}:{int(start_middle % 60):02d}',
                                    'end': f'{int(end_middle // 60):02d}:{int(end_middle % 60):02d}',
                                    'description': 'Middle part (suggested)'})
            suggested_clips.append({'start': '00:00', 'end': '00:15', 'description': 'Beginning part (suggested)'})

        return JsonResponse({
            'status': 'success',
            'message': 'Video loaded from cache!',
            'video_path': video_record.file_path,
            'video_title': video_record.title,
            'video_duration': video_record.duration,
            'suggested_clips': suggested_clips
        })

    except DownloadedVideo.DoesNotExist:
        # 2. IF NOT IN DATABASE, DOWNLOAD IT
        logger.info(f"Video '{video_id}' not in cache. Starting download.")

        output_dir = os.path.join(settings.MEDIA_ROOT, 'videos')
        os.makedirs(output_dir, exist_ok=True)
        # Use the YouTube ID as the filename for consistency
        video_path = os.path.join(output_dir, f'{video_id}.mp4')

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
            'outtmpl': video_path,
            'merge_output_format': 'mp4',
            'noplaylist': True,
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(video_url, download=True)
                video_title = info_dict.get('title', 'No Title')
                video_duration = info_dict.get('duration', 0)

            logger.info(f"Successfully downloaded video '{video_title}' (Duration: {video_duration}s).")

            # 3. SAVE THE NEW VIDEO RECORD TO THE DATABASE
            relative_path = f'/media/videos/{os.path.basename(video_path)}'
            DownloadedVideo.objects.create(
                video_id=video_id,
                title=video_title,
                duration=video_duration,
                file_path=relative_path
            )
            logger.info(f"Saved new video record for '{video_id}' to the database.")

            suggested_clips = []
            if video_duration > 0:
                start_middle = max(0, (video_duration // 2) - 7)
                end_middle = min(video_duration, (video_duration // 2) + 8)
                suggested_clips.append({'start': f'{int(start_middle // 60):02d}:{int(start_middle % 60):02d}',
                                        'end': f'{int(end_middle // 60):02d}:{int(end_middle % 60):02d}',
                                        'description': 'Middle part (suggested)'})
                suggested_clips.append({'start': '00:00', 'end': '00:15', 'description': 'Beginning part (suggested)'})

            return JsonResponse({
                'status': 'success',
                'message': 'Video downloaded and ready for trimming!',
                'video_path': relative_path,
                'video_title': video_title,
                'video_duration': video_duration,
                'suggested_clips': suggested_clips
            })

        except Exception as e:
            logger.error(f"Error downloading video from URL {video_url}.", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'Error downloading video: {str(e)}'})


# --- GENERATE SHORT FUNCTION (UNCHANGED) ---
def generate_short(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            video_relative_path = data.get('video_path')
            start_time_str = data.get('start_time')
            end_time_str = data.get('end_time')
            add_thumbnail = data.get('add_thumbnail', False)
            tags_str = data.get('tags', '')

            logger.info(f"Received request to generate short for video: {video_relative_path}")
            logger.debug(f"Request data: {data}")

            if not video_relative_path or not start_time_str or not end_time_str:
                logger.warning("Missing required data (video_path, start_time, end_time) for short generation.")
                return JsonResponse({'status': 'error', 'message': 'Missing video path or trim times.'})

            video_full_path = os.path.join(settings.BASE_DIR, video_relative_path.lstrip('/'))

            if not os.path.exists(video_full_path):
                logger.error(f"Original video file not found at path: {video_full_path}")
                return JsonResponse({'status': 'error', 'message': 'Original video not found.'})

            def time_to_seconds(time_str):
                parts = [int(p) for p in time_str.split(':')]
                if len(parts) == 3: return parts[0] * 3600 + parts[1] * 60 + parts[2]
                if len(parts) == 2: return parts[0] * 60 + parts[1]
                return 0

            start_seconds = time_to_seconds(start_time_str)
            end_seconds = time_to_seconds(end_time_str)
            logger.debug(f"Trimming video from {start_seconds}s to {end_seconds}s.")

            if start_seconds >= end_seconds:
                logger.warning(
                    f"Invalid trim times: Start time ({start_seconds}s) is not less than end time ({end_seconds}s).")
                return JsonResponse({'status': 'error', 'message': 'Start time must be less than end time.'})

            clip = VideoFileClip(video_full_path).subclip(start_seconds, end_seconds)

            logger.info("Original audio is being kept for the short clip.")

            shorts_output_dir = os.path.join(settings.MEDIA_ROOT, 'shorts')
            os.makedirs(shorts_output_dir, exist_ok=True)
            short_filename = f'short_{uuid.uuid4()}.mp4'
            short_path = os.path.join(shorts_output_dir, short_filename)

            logger.info(f"Writing final short video (with audio) to: {short_path}")
            clip.write_videofile(short_path, codec="libx264", audio_codec="aac")

            thumbnail_path = None
            if add_thumbnail:
                logger.info("Thumbnail generation requested.")
                thumbnail_dir = os.path.join(settings.MEDIA_ROOT, 'thumbnails')
                os.makedirs(thumbnail_dir, exist_ok=True)
                thumbnail_filename = f'thumbnail_{uuid.uuid4()}.png'
                thumbnail_path = os.path.join(thumbnail_dir, thumbnail_filename)

                frame_time = clip.duration / 2
                clip.save_frame(thumbnail_path, t=frame_time)
                logger.debug(f"Saved thumbnail frame to: {thumbnail_path}")

                if tags_str:
                    logger.debug(f"Adding tags to thumbnail: {tags_str}")
                    img = Image.open(thumbnail_path)
                    draw = ImageDraw.Draw(img)
                    try:
                        font = ImageFont.truetype("arial.ttf", 40)
                    except IOError:
                        logger.warning("arial.ttf font not found. Falling back to default font.")
                        font = ImageFont.load_default()
                    text_color = (255, 255, 255)
                    text_position = (10, 10)
                    draw.text(text_position, f"Tags: {tags_str}", font=font, fill=text_color)
                    img.save(thumbnail_path)

            logger.info(f"Short video created successfully: /media/shorts/{short_filename}")
            return JsonResponse({
                'status': 'success',
                'message': 'Short created successfully!',
                'short_url': f'/media/shorts/{short_filename}',
                'thumbnail_url': f'/media/thumbnails/{thumbnail_filename}' if thumbnail_path else None
            })

        except Exception as e:
            logger.error("An unexpected error occurred during short generation.", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'Error processing video: {str(e)}'})

    logger.warning(f"Invalid request method ({request.method}) for generate_short view.")
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})


# --- DOWNLOAD SHORT FUNCTION (UNCHANGED) ---
def download_short(request, filename):
    logger.info(f"Download request received for short: {filename}")
    short_path = os.path.join(settings.MEDIA_ROOT, 'shorts', filename)

    if os.path.exists(short_path):
        logger.debug(f"File found at {short_path}. Serving file.")
        response = FileResponse(open(short_path, 'rb'), content_type='video/mp4')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    else:
        logger.error(f"Short not found at path: {short_path}. Raising Http404.")
        raise Http404("Short not found")