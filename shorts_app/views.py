import os
import uuid
import json
import logging
import re
import threading
import webvtt
from urllib.parse import urlparse, parse_qs
import tempfile # Import tempfile for temporary local storage

from django.shortcuts import render, get_object_or_404
from django.conf import settings
from django.http import JsonResponse, HttpResponseRedirect, Http404
from django.core.cache import cache
from django.core.files import File # To wrap local files for S3 upload
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip, CompositeVideoClip, ColorClip
from moviepy.video.fx.all import crop, resize
import boto3
from botocore.exceptions import ClientError  # Import boto3 for S3 interactions

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

# Initialize S3 client outside of functions for efficiency
s3_client = None
if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY and settings.AWS_STORAGE_BUCKET_NAME:
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME
        )
    except Exception as e:
        logger.error(f"Error initializing S3 client: {e}. S3 features disabled.")

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
    """
    Extracts the YouTube video ID from a YouTube URL.
    Handles standard (youtube.com/watch), short (youtu.be/), and embed URLs.
    """
    if not url: 
        return None
        
    query = urlparse(url)
    
    # Handle standard and mobile URLs (e.g., youtube.com/watch?v=...)
    if query.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        if query.path == '/watch':
            p = parse_qs(query.query)
            return p.get('v', [None])[0]
        if query.path.startswith(('/embed/', '/v/')):
            return query.path.split('/')[-1]
            
    # Handle short URLs (e.g., youtu.be/...)
    if query.hostname == 'youtu.be':
        return query.path[1:] # The ID is the path component
        
    return None

def index(request):
    processed_videos = DownloadedVideo.objects.all().order_by('-created_at')
    generated_shorts = GeneratedShort.objects.select_related('parent_video').order_by('-created_at')
    return render(request, 'shorts_app/index.html', {'videos': processed_videos, 'shorts': generated_shorts})


def check_progress(request, task_id):
    return JsonResponse(cache.get(task_id, {"status": "PENDING", "progress": 0, "message": "Initializing..."}))

# Or in views.py (e.g., at the start of process_video)
logger.info(f"DEBUG: Using S3 bucket: {settings.AWS_STORAGE_BUCKET_NAME}")

def process_video(request):
    if request.method != 'POST': return JsonResponse({'status': 'error', 'message': 'Invalid request.'})

    video_url = request.POST.get('video_url')
    video_id = request.POST.get('video_id') or get_youtube_id(video_url)
    if not video_id: return JsonResponse({'status': 'error', 'message': 'Valid YouTube URL or Video ID is required.'})

    task_id = str(uuid.uuid4())

    def long_running_task():
        def progress_hook(d):
            if d['status'] == 'downloading':
                percent_str = d.get('_percent_str', '0.0%')
                cleaned_str = re.sub(r'\x1b\[[0-9;]*m', '', percent_str).replace('%', '').strip()
                try:
                    progress = float(cleaned_str)
                    cache.set(task_id,
                              {"status": "processing", "progress": progress, "message": "Downloading video..."})
                except ValueError:
                    pass
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
                # Use a temporary directory for local download
                with tempfile.TemporaryDirectory() as tmpdir:
                    output_template = os.path.join(tmpdir, f'{video_id}.%(ext)s')
                    ydl_opts = {
                        'format': 'best[height<=1080][ext=mp4]',
                        'outtmpl': output_template,
                        'merge_output_format': 'mp4',
                        'noplaylist': True,
                        'writesubtitles': True,
                        'writeautomaticsub': True,
                        'subtitleslangs': ['en'],
                        'subtitlesformat': 'vtt',
                        'writethumbnail': True,
                        'nocolor': True,
                        'progress_hooks': [progress_hook],
                    }
                    with YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(video_url, download=True)

                    temp_video_path = os.path.join(tmpdir, f'{video_id}.mp4')
                    temp_thumbnail_path_webp = os.path.join(tmpdir, f'{video_id}.webp')
                    temp_thumbnail_path_jpg = os.path.join(tmpdir, f'{video_id}.jpg')
                    temp_transcript_path = os.path.join(tmpdir, f'{video_id}.en.vtt')

                    if not os.path.exists(temp_video_path):
                        cache.set(task_id, {'status': 'error', 'message': f'Video file not found after download.'})
                        return

                    # Upload video to S3
                    # Removed YoutubeVideoStorage.location as it's no longer defined
                    video_s3_key = f'yt_video/{video_id}.mp4'
                    with open(temp_video_path, 'rb') as f:
                        DownloadedVideo.file_path.field.storage.save(video_s3_key, File(f))
                    video_s3_url = DownloadedVideo.file_path.field.storage.url(video_s3_key)

                    thumbnail_s3_url = None
                    thumbnail_s3_key = None
                    if os.path.exists(temp_thumbnail_path_webp):
                        # Removed YoutubeVideoStorage.location
                        thumbnail_s3_key = f'yt_video/{video_id}.webp'
                        with open(temp_thumbnail_path_webp, 'rb') as f:
                            DownloadedVideo.thumbnail_path.field.storage.save(thumbnail_s3_key, File(f))
                        thumbnail_s3_url = DownloadedVideo.thumbnail_path.field.storage.url(thumbnail_s3_key)
                    elif os.path.exists(temp_thumbnail_path_jpg):
                        # Removed YoutubeVideoStorage.location
                        thumbnail_s3_key = f'yt_video/{video_id}.jpg'
                        with open(temp_thumbnail_path_jpg, 'rb') as f:
                            DownloadedVideo.thumbnail_path.field.storage.save(thumbnail_s3_key, File(f))
                        thumbnail_s3_url = DownloadedVideo.thumbnail_path.field.storage.url(thumbnail_s3_key)

                    video_record, _ = DownloadedVideo.objects.update_or_create(
                        video_id=video_id,
                        defaults={
                            'title': info.get('title', 'N/A'),
                            'duration': info.get('duration', 0),
                            'file_path': video_s3_key, # Store the S3 key, not the full URL
                            'thumbnail_path': thumbnail_s3_key, # Store the S3 key
                        }
                    )

                    # Process transcript if exists
                    suggested_clips = []
                    if os.path.exists(temp_transcript_path):
                        transcript = " ".join([c.text.strip().replace('\n', ' ') for c in webvtt.read(temp_transcript_path)])
                        if transcript: suggested_clips = get_ai_suggested_clips(transcript, video_record.duration)
                    video_record.suggestions = suggested_clips
                    video_record.save()

            except Exception as e:
                cache.set(task_id, {'status': 'error', 'message': f'Processing failed: {e}'})
                logger.error(f"Error during video processing and upload: {e}", exc_info=True)
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
        video_id, clip_data, aspect_ratio = data.get('video_id'), data.get('clip_data', {}), data.get('aspect_ratio', '9:16')
        start_time_str, end_time_str = clip_data.get('start_time'), clip_data.get('end_time')
        parent_video = get_object_or_404(DownloadedVideo, video_id=video_id)

        # Get the S3 URL for the parent video
        video_s3_url = parent_video.file_path.url

        def time_to_seconds(t):
            parts = [int(x) for x in t.split(':')]
            if len(parts) == 2: # MM:SS
                return parts[0] * 60 + parts[1]
            elif len(parts) == 3: # HH:MM:SS
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            return 0 # Should not happen with validation

        start_s, end_s = time_to_seconds(start_time_str), time_to_seconds(end_time_str)

        # Download the video temporarily to local disk for MoviePy processing
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_video_file:
            try:
                s3_client.download_fileobj(settings.AWS_STORAGE_BUCKET_NAME, parent_video.file_path.name, temp_video_file)
                temp_video_path = temp_video_file.name
            except Exception as e:
                logger.error(f"Error downloading video from S3: {e}", exc_info=True)
                return JsonResponse({'status': 'error', 'message': f'Could not download video from S3: {e}'}, status=500)

        try:
            with VideoFileClip(temp_video_path) as video:
                subclip = video.subclip(start_s, min(end_s, video.duration))
                (w, h), final_clip = subclip.size, subclip

                if aspect_ratio == '9:16':
                    target_w, target_h = 1080, 1920
                    # Check if resize is needed before applying
                    if w / h < 9 / 16: # If video is wider than 9:16 (e.g., 16:9), resize by height
                        clip_resized = final_clip.resize(height=target_h)
                    else: # If video is taller or already 9:16, resize by width
                        clip_resized = final_clip.resize(width=target_w)

                    # Ensure the clip is cropped to the target aspect ratio if it's still not 9:16
                    # after initial resize (e.g. source is 4:3, resized to 1080px width, height will be too short)
                    if clip_resized.w / clip_resized.h != 9 / 16:
                        clip_resized = crop(clip_resized, width=target_w, height=target_h, x_center=clip_resized.w / 2, y_center=clip_resized.h / 2)

                    background = ColorClip(size=(target_w, target_h), color=(0, 0, 0))
                    final_clip = CompositeVideoClip([background.set_opacity(1), clip_resized.set_position("center")], use_bgclip=True)
                elif aspect_ratio == '16:9':
                    # If the source is not 16:9, crop or pad
                    if w / h > 16 / 9: # Wider than 16:9, crop width
                        final_clip = crop(subclip, width=subclip.h * 16 / 9, height=subclip.h, x_center=subclip.w / 2)
                    elif w / h < 16 / 9: # Taller than 16:9, crop height
                        final_clip = crop(subclip, width=subclip.w, height=subclip.w * 9 / 16, y_center=subclip.h / 2)
                # For 'original', no aspect ratio change is needed beyond subclip.

                short_uuid = uuid.uuid4()
                # Use temporary files for saving before uploading to S3
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_short_file, \
                     tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_thumb_file:

                    temp_short_path = temp_short_file.name
                    temp_thumb_path = temp_thumb_file.name

                    final_clip.write_videofile(temp_short_path, codec="libx264", audio_codec="aac", threads=os.cpu_count(), preset="medium")
                    final_clip.save_frame(temp_thumb_path, t=final_clip.duration / 2)

                    # Upload short and thumbnail to S3
                    short_s3_key = f'shorts/{short_uuid}.mp4'
                    thumbnail_s3_key = f'shorts/{short_uuid}.png'

                    with open(temp_short_path, 'rb') as f:
                        GeneratedShort.short_path.field.storage.save(short_s3_key, File(f))

                    with open(temp_thumb_path, 'rb') as f:
                        GeneratedShort.thumbnail_path.field.storage.save(thumbnail_s3_key, File(f))

        finally:
                        # Clean up temporary downloaded video and generated files
                        if 'temp_video_path' in locals() and os.path.exists(temp_video_path):
                            os.unlink(temp_video_path)
                        if 'temp_short_path' in locals() and os.path.exists(temp_short_path):
                            os.unlink(temp_short_path)
                        if 'temp_thumb_path' in locals() and os.path.exists(temp_thumb_path):
                            os.unlink(temp_thumb_path)

        GeneratedShort.objects.create(
            parent_video=parent_video,
            title=clip_data.get('title', 'Untitled Short'),
            description=clip_data.get('description', ''),
            tags=clip_data.get('tags', []),
            short_path=short_s3_key, # Store S3 key
            thumbnail_path=thumbnail_s3_key, # Store S3 key
            start_time=start_time_str,
            end_time=end_time_str
        )
        return JsonResponse({'status': 'success', 'message': 'Short created successfully!'})
    except Exception as e:
        logger.error(f"Error during short generation: {e}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': f'An unexpected error occurred: {e}'}, status=500)


# --- Deletion and Download views (Updated for S3) ---
def _delete_files_s3(s3_keys):
    if not s3_client:
        logger.error("S3 client not initialized. Cannot delete files from S3.")
        return

    for key in s3_keys:
        if not key: continue # Skip if key is None or empty
        try:
            s3_client.delete_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=key)
            logger.info(f"Deleted S3 object: {key}")
        except Exception as e:
            logger.error(f"Failed to delete S3 object {key}: {e}")


def delete_video(request, video_id):
    if request.method == 'POST':
        video = get_object_or_404(DownloadedVideo, video_id=video_id)
        # Collect all S3 keys to delete
        keys_to_delete = [video.file_path.name] # .name gives the S3 key/path
        if video.thumbnail_path:
            keys_to_delete.append(video.thumbnail_path.name)
        # Add transcript path if it exists (assuming it's named consistently with video_id in S3)
        # Note: YouTubeDL doesn't upload VTT directly to S3 with its default hook.
        # If you were uploading VTTs, you'd need to add that logic here.
        # For now, we'll assume VTT is not uploaded to S3 or is handled separately.
        _delete_files_s3(keys_to_delete)
        video.delete()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)


def delete_short(request, short_id):
    if request.method == 'POST':
        short = get_object_or_404(GeneratedShort, id=short_id)
        _delete_files_s3([short.short_path.name, short.thumbnail_path.name])
        short.delete()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)


def download_short(request, short_id): 
    if not s3_client:
        raise Http404("S3 client not configured.")

    short = get_object_or_404(GeneratedShort, id=short_id)
    s3_key = short.short_path.name

    try:
        # Generate a pre-signed URL for the short video
        url = s3_client.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': settings.AWS_STORAGE_BUCKET_NAME, 'Key': s3_key},
            ExpiresIn=3600 # URL 1 ghante ke liye valid
        )
        
        return HttpResponseRedirect(url) 
    except ClientError as e:
        logger.error(f"Error generating pre-signed URL for {s3_key}: {e}")
        # Agar koi error aaye to 404 dikha sakte hain ya proper error message
        raise Http404("File not found or access denied.")
    except Exception as e:
        logger.error(f"Unexpected error in download_short: {e}")
        raise Http404("An unexpected error occurred during download preparation.")