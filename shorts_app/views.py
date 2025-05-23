import os
import uuid
import json
import logging
import re
from urllib.parse import urlparse, parse_qs

from django.shortcuts import render
from django.conf import settings
from django.http import JsonResponse, FileResponse, Http404
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip
from PIL import Image, ImageDraw, ImageFont

# Import the model from your models.py
from .models import DownloadedVideo

# --- NEW: Import Gemini ---
import google.generativeai as genai

logger = logging.getLogger(__name__)

# --- NEW: Configure Gemini at the module level ---
try:
    # Make sure GEMINI_API_KEY is set in your settings.py
    if settings.GEMINI_API_KEY:
        genai.configure(api_key=settings.GEMINI_API_KEY)
except (AttributeError, NameError):
    logger.error("GEMINI_API_KEY not found in Django settings. AI features will be disabled.")
    genai = None


# --- NEW: AI Function to get clips from a transcript ---
def get_ai_suggested_clips(transcript: str, video_duration: int):
    """
    Uses the Gemini API to find the best clips from a video transcript.
    """
    if not genai:
        logger.warning("Gemini API not configured. Skipping AI clip suggestion.")
        return []

    model = genai.GenerativeModel('gemini-1.5-flash')

    prompt = f"""
    You are an expert video editor specializing in creating viral short-form clips.
    Your task is to analyze the following video transcript and identify up to 3 of the most engaging, climactic, or interesting segments that would make a great short video. Each clip should ideally be between 30 and 90 seconds long.

    The total video duration is {video_duration} seconds. Use this to ensure your end times are valid and do not exceed the total length.

    Analyze the text for moments of high emotion, key revelations, surprising statements, or strong calls to action.

    Here is the full transcript:
    ---
    {transcript}
    ---

    Your response MUST be a valid JSON array of objects. Each object must contain 'start_time', 'end_time' (in "MM:SS" format), and a brief 'description' of why the clip is compelling. Do not include any text, notes, or explanations outside of the final JSON array.

    Example format:
    [
        {{"start_time": "02:15", "end_time": "03:05", "description": "The speaker reveals the main discovery of their research."}},
        {{"start_time": "10:30", "end_time": "11:15", "description": "A surprising and humorous anecdote about the project's beginning."}}
    ]
    """

    try:
        logger.info("Sending transcript to Gemini API for clip suggestions...")
        response = model.generate_content(prompt)

        # Clean the response to ensure it's valid JSON by removing markdown formatting
        json_response_text = response.text.strip().replace("```json", "").replace("```", "")

        suggested_clips = json.loads(json_response_text)
        logger.info(f"Received {len(suggested_clips)} clip suggestions from Gemini.")
        return suggested_clips
    except Exception as e:
        logger.error(f"Error calling Gemini API or parsing its response: {e}", exc_info=True)
        return []


# Helper function to extract YouTube video ID from URL
def get_youtube_id(url):
    """Extracts the YouTube video ID from a URL."""
    if url is None:
        return None
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


# --- REWRITTEN VIDEO PROCESSING LOGIC WITH AI INTEGRATION ---
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

    try:
        video_record = DownloadedVideo.objects.get(video_id=video_id)
        video_full_path = os.path.join(settings.BASE_DIR, video_record.file_path.lstrip('/'))
        if not os.path.exists(video_full_path):
            logger.warning(f"Found DB record for {video_id} but file was missing. Deleting record and re-downloading.")
            video_record.delete()
            raise DownloadedVideo.DoesNotExist
        logger.info(f"Video '{video_id}' found in cache. Generating new clip suggestions.")

    except DownloadedVideo.DoesNotExist:
        logger.info(f"Video '{video_id}' not in cache. Starting download.")
        output_dir = os.path.join(settings.MEDIA_ROOT, 'videos')
        os.makedirs(output_dir, exist_ok=True)
        video_path_template = os.path.join(output_dir, video_id)

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
            'outtmpl': f'{video_path_template}.%(ext)s',
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en'],
            'subtitlesformat': 'vtt',
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(video_url, download=True)
                video_title = info_dict.get('title', 'No Title')
                video_duration = info_dict.get('duration', 0)

            relative_path = f'/media/videos/{video_id}.mp4'
            video_record, created = DownloadedVideo.objects.update_or_create(
                video_id=video_id,
                defaults={'title': video_title, 'duration': video_duration, 'file_path': relative_path}
            )
            logger.info(f"Saved/Updated video record for '{video_id}' in the database.")

        except Exception as e:
            logger.error(f"Error downloading video from URL {video_url}.", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'Error downloading video: {str(e)}'})

    suggested_clips = []
    video_duration = video_record.duration
    transcript_path = os.path.join(settings.MEDIA_ROOT, 'videos', f'{video_id}.en.vtt')

    if os.path.exists(transcript_path):
        logger.info(f"Found transcript file: {transcript_path}")
        with open(transcript_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            text_lines = [re.sub(r'<[^>]+>', '', line).strip() for line in lines if
                          not re.match(r'^\d{2}:\d{2}:\d{2}\.\d{3}', line) and 'WEBVTT' not in line and line.strip()]
            transcript = "\n".join(text_lines)

        suggested_clips = get_ai_suggested_clips(transcript, video_duration)

    if not suggested_clips:
        logger.warning("Could not get AI suggestions. Falling back to basic suggestions.")
        if video_duration > 0:
            start_middle = max(0, (video_duration // 2) - 15)
            end_middle = min(video_duration, (video_duration // 2) + 15)
            suggested_clips.append({'start_time': f'{int(start_middle // 60):02d}:{int(start_middle % 60):02d}',
                                    'end_time': f'{int(end_middle // 60):02d}:{int(end_middle % 60):02d}',
                                    'description': 'Middle of the video (fallback)'})
            suggested_clips.append(
                {'start_time': '00:00', 'end_time': '00:30', 'description': 'Beginning of the video (fallback)'})

    # Rename keys from 'start_time'/'end_time' to 'start'/'end' if needed by frontend
    final_clips = [
        {'start': clip.get('start_time'), 'end': clip.get('end_time'), 'description': clip.get('description')} for clip
        in suggested_clips]

    return JsonResponse({
        'status': 'success',
        'message': 'Video processed successfully!',
        'video_path': video_record.file_path,
        'video_title': video_record.title,
        'video_duration': video_record.duration,
        'suggested_clips': final_clips
    })


def generate_short(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            video_relative_path = data.get('video_path')
            start_time_str = data.get('start_time')
            end_time_str = data.get('end_time')
            add_thumbnail = data.get('add_thumbnail', False)
            tags_str = data.get('tags', '')

            if not all([video_relative_path, start_time_str, end_time_str]):
                return JsonResponse({'status': 'error', 'message': 'Missing video path or trim times.'})

            video_full_path = os.path.join(settings.BASE_DIR, video_relative_path.lstrip('/'))

            if not os.path.exists(video_full_path):
                return JsonResponse({'status': 'error', 'message': 'Original video not found.'})

            def time_to_seconds(time_str):
                parts = [int(p) for p in time_str.split(':')]
                if len(parts) == 3: return parts[0] * 3600 + parts[1] * 60 + parts[2]
                if len(parts) == 2: return parts[0] * 60 + parts[1]
                return 0

            start_seconds = time_to_seconds(start_time_str)
            end_seconds = time_to_seconds(end_time_str)

            if start_seconds >= end_seconds:
                return JsonResponse({'status': 'error', 'message': 'Start time must be less than end time.'})

            with VideoFileClip(video_full_path).subclip(start_seconds, end_seconds) as clip:
                shorts_output_dir = os.path.join(settings.MEDIA_ROOT, 'shorts')
                os.makedirs(shorts_output_dir, exist_ok=True)
                short_filename = f'short_{uuid.uuid4()}.mp4'
                short_path = os.path.join(shorts_output_dir, short_filename)

                logger.info(f"Writing final short video (with audio) to: {short_path}")
                clip.write_videofile(short_path, codec="libx264", audio_codec="aac")

                thumbnail_path = None
                thumbnail_filename = None
                if add_thumbnail:
                    thumbnail_dir = os.path.join(settings.MEDIA_ROOT, 'thumbnails')
                    os.makedirs(thumbnail_dir, exist_ok=True)
                    thumbnail_filename = f'thumbnail_{uuid.uuid4()}.png'
                    thumbnail_path = os.path.join(thumbnail_dir, thumbnail_filename)
                    clip.save_frame(thumbnail_path, t=clip.duration / 2)

                    if tags_str:
                        img = Image.open(thumbnail_path)
                        draw = ImageDraw.Draw(img)
                        try:
                            font = ImageFont.truetype("arial.ttf", 40)
                        except IOError:
                            font = ImageFont.load_default()
                        draw.text((10, 10), f"Tags: {tags_str}", font=font, fill=(255, 255, 255))
                        img.save(thumbnail_path)

            return JsonResponse({
                'status': 'success',
                'message': 'Short created successfully!',
                'short_url': f'/media/shorts/{short_filename}',
                'thumbnail_url': f'/media/thumbnails/{thumbnail_filename}' if thumbnail_path else None
            })

        except Exception as e:
            logger.error("An unexpected error occurred during short generation.", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'Error processing video: {str(e)}'})

    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})


def download_short(request, filename):
    short_path = os.path.join(settings.MEDIA_ROOT, 'shorts', filename)

    if os.path.exists(short_path):
        response = FileResponse(open(short_path, 'rb'), as_attachment=True, filename=filename)
        return response
    else:
        raise Http404("Short not found")