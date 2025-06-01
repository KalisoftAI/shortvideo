# shorts_app/views.py
import os
import uuid
import json
import logging
import re
import threading
import webvtt
from urllib.parse import urlparse, parse_qs
import boto3 # Import boto3 for S3 interaction
from django.shortcuts import render
from django.conf import settings

from django.shortcuts import render, get_object_or_404, redirect # Import redirect
from django.conf import settings
from django.http import JsonResponse, FileResponse, Http404
from django.core.cache import cache
from django.views.decorators.csrf import csrf_exempt # For simplicity, add this decorator temporarily to generate_short
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip, CompositeVideoClip, ColorClip, TextClip # Add TextClip if not there
from moviepy.video.fx.all import crop, resize

from .models import DownloadedVideo, GeneratedShort
import google.generativeai as genai

logger = logging.getLogger(__name__)

# --- S3 Client Initialization ---
s3_client = None
if settings.AWS_S3_ENABLED:
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME
        )
        logger.info("S3 client initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing S3 client: {e}. S3 functionality may be limited.")
        s3_client = None # Ensure s3_client is None if initialization fails

# --- S3 Helper Functions ---
def upload_file_to_s3(file_path, s3_object_key, content_type=None):
    """Uploads a local file to an S3 bucket."""
    if not s3_client:
        logger.error("S3 client not initialized. Cannot upload file.")
        return False

    try:
        extra_args = {}
        if content_type:
            extra_args['ContentType'] = content_type

        s3_client.upload_file(
            file_path,
            settings.AWS_STORAGE_BUCKET_NAME,
            s3_object_key,
            ExtraArgs=extra_args
        )
        logger.info(f"File {file_path} uploaded to S3://{settings.AWS_STORAGE_BUCKET_NAME}/{s3_object_key}")
        return True
    except Exception as e:
        logger.error(f"Failed to upload {file_path} to S3 {s3_object_key}: {e}")
        return False

def delete_s3_object(s3_object_key):
    """Deletes an object from an S3 bucket."""
    if not s3_client:
        logger.error("S3 client not initialized. Cannot delete S3 object.")
        return False

    try:
        s3_client.delete_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_object_key
        )
        logger.info(f"S3 object s3://{settings.AWS_STORAGE_BUCKET_NAME}/{s3_object_key} deleted.")
        return True
    except Exception as e:
        logger.error(f"Failed to delete S3 object {s3_object_key}: {e}")
        return False

# --- Configure Gemini (Unchanged) ---
genai_configured = False
genai_configured = False
try:
    # This line correctly accesses the key from Django's settings
    if settings.GEMINI_API_KEY:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        genai_configured = True
    else:
        logger.error("GEMINI_API_KEY not set. AI features disabled.")
except Exception as e:
    logger.error(f"Error during Gemini API configuration: {e}. AI features disabled.")
    genai = None
# --- AI Suggestion, Get YouTube ID, Index, Check Progress ---

def get_ai_suggested_clips(transcript: str, video_duration: int):
    # This function remains mostly unchanged
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
    videos = DownloadedVideo.objects.all().order_by('-created_at')
    context = {
        'videos': videos,
        'aws_s3_enabled': settings.AWS_S3_ENABLED, 
    }
    return render(request, 'index.html', context)

def check_progress(request, task_id):
    return JsonResponse(cache.get(task_id, {"status": "PENDING", "progress": 0, "message": "Initializing..."}))


def _yt_dlp_progress_hook(d, task_id, message_prefix=""):
    """Custom progress hook for yt-dlp to update cache."""
    if d['status'] == 'downloading':
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
        downloaded_bytes = d.get('downloaded_bytes', 0)
        if total_bytes > 0:
            progress = int(downloaded_bytes / total_bytes * 70) # Max 70% for download
            cache.set(task_id, {'status': 'progress', 'message': f'{message_prefix} {d["_percent_str"]}...', 'progress': progress}, timeout=3600)
    elif d['status'] == 'finished':
        cache.set(task_id, {'status': 'progress', 'message': f'{message_prefix} completed.', 'progress': 70}, timeout=3600)

def _delete_local_files(paths):
    """Helper to delete local files."""
    for full_path in paths:
        if full_path and os.path.exists(full_path):
            try:
                os.remove(full_path)
                logger.info(f"Locally deleted: {full_path}")
            except OSError as e:
                logger.error(f"Failed to delete local file {full_path}: {e}")

def _process_video_task(task_id, video_url):
    """Background task to download video, upload to S3, generate suggestions, and clean up."""
    cache.set(task_id, {'status': 'progress', 'message': 'Starting video download...', 'progress': 0}, timeout=3600)
    video_id = get_youtube_id(video_url)
    if not video_id:
        cache.set(task_id, {'status': 'error', 'message': 'Invalid YouTube URL.'}, timeout=3600)
        return

    # Define local paths for temporary download
    temp_video_filename = f"{video_id}.mp4"
    temp_thumbnail_filename = f"{video_id}.webp"
    temp_vtt_filename = f"{video_id}.en.vtt" # For English subtitles
    local_video_path = os.path.join(settings.MEDIA_ROOT, 'videos', temp_video_filename)
    local_thumbnail_path = os.path.join(settings.MEDIA_ROOT, 'videos', temp_thumbnail_filename)
    local_vtt_path = os.path.join(settings.MEDIA_ROOT, 'videos', temp_vtt_filename)

    # Ensure local media directory exists
    os.makedirs(os.path.dirname(local_video_path), exist_ok=True)

    try:
        # Step 1: Download Video and Thumbnail Locally
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.splitext(local_video_path)[0], # yt-dlp adds extension
            'writesubtitles': True,
            'subtitleslangs': ['en'],
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }, {
                'key': 'FFmpegThumbnailsConvertor',
                'format': 'webp',
            }],
            'writethumbnail': True,
            'updatetime': False,
            'progress_hooks': [lambda d: _yt_dlp_progress_hook(d, task_id, 'Downloading video')],
        }

        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=True)
            video_title = info_dict.get('title', 'Unknown Title')
            video_duration = info_dict.get('duration', 0)
            
            # Determine the actual saved thumbnail path
            actual_thumbnail_path = os.path.splitext(local_video_path)[0] + '.webp'
            if not os.path.exists(actual_thumbnail_path):
                logger.warning(f"Thumbnail not found at expected path: {actual_thumbnail_path}. Attempting to find alternatives.")
                # Fallback: check if yt_dlp provided a direct thumbnail path
                if 'requested_downloads' in info_dict:
                    for dl in info_dict['requested_downloads']:
                        if dl.get('filepath', '').endswith('.webp'):
                            actual_thumbnail_path = dl['filepath']
                            break
            if not os.path.exists(actual_thumbnail_path):
                logger.error(f"Could not find thumbnail for {video_id}.")
                actual_thumbnail_path = None # Set to None if not found

            # Extract subtitles
            transcript = ""
            if os.path.exists(local_vtt_path):
                try:
                    for caption in webvtt.read(local_vtt_path):
                        transcript += caption.text + " "
                except Exception as e:
                    logger.error(f"Error reading VTT file {local_vtt_path}: {e}")
            
            # Step 2: Upload to S3 and Update Database
            video_s3_key = None
            thumbnail_s3_key = None
            
            if settings.AWS_S3_ENABLED and s3_client:
                cache.set(task_id, {'status': 'progress', 'message': 'Uploading video to S3...', 'progress': 80}, timeout=3600)
                # Upload video
                video_s3_key = f"{settings.AWS_S3_YOUTUBE_VIDEO_FOLDER}{temp_video_filename}"
                if upload_file_to_s3(local_video_path, video_s3_key, 'video/mp4'):
                    logger.info(f"Video uploaded to S3: {video_s3_key}")
                else:
                    raise Exception("Failed to upload video to S3.")

                # Upload thumbnail
                if actual_thumbnail_path and os.path.exists(actual_thumbnail_path):
                    thumbnail_s3_key = f"{settings.AWS_S3_YOUTUBE_VIDEO_FOLDER}{temp_thumbnail_filename}"
                    if upload_file_to_s3(actual_thumbnail_path, thumbnail_s3_key, 'image/webp'):
                        logger.info(f"Thumbnail uploaded to S3: {thumbnail_s3_key}")
                    else:
                        logger.warning("Failed to upload thumbnail to S3. Continuing without thumbnail.")
                        thumbnail_s3_key = None # Ensure it's None if upload fails
                else:
                    logger.warning("No thumbnail file to upload to S3.")
            
            # Generate AI suggestions
            suggestions = []
            if genai_configured and transcript:
                cache.set(task_id, {'status': 'progress', 'message': 'Generating AI suggestions...', 'progress': 90}, timeout=3600)
                suggestions = get_ai_suggested_clips(transcript, video_duration)

            video, created = DownloadedVideo.objects.update_or_create(
                video_id=video_id,
                defaults={
                    'title': video_title,
                    'duration': video_duration,
                    'file_path': video_s3_key if settings.AWS_S3_ENABLED else os.path.relpath(local_video_path, settings.BASE_DIR),
                    'thumbnail_path': thumbnail_s3_key if settings.AWS_S3_ENABLED else (os.path.relpath(actual_thumbnail_path, settings.BASE_DIR) if actual_thumbnail_path else None),
                    'suggestions': suggestions
                }
            )
            
            # Step 3: Clean up local temporary files
            _delete_local_files([local_video_path, actual_thumbnail_path, local_vtt_path])

            cache.set(task_id, {'status': 'complete', 'message': 'Video processed successfully.', 'progress': 100, 'video_id': video_id}, timeout=3600)

    except Exception as e:
        logger.error(f"Error processing video {video_id}: {e}", exc_info=True)
        # Attempt to clean up local files even on error
        _delete_local_files([local_video_path, actual_thumbnail_path, local_vtt_path])
        cache.set(task_id, {'status': 'error', 'message': f'Error processing video: {e}'}, timeout=3600)

def process_video(request):
    """Initiates the video download and processing in a background thread."""
    if request.method != 'POST': return JsonResponse({'status': 'error', 'message': 'Invalid request.'})

    video_url = request.POST.get('video_url')
    video_id = request.POST.get('video_id') or get_youtube_id(video_url)
    if not video_id: return JsonResponse({'status': 'error', 'message': 'Valid YouTube URL or Video ID is required.'})

    task_id = str(uuid.uuid4())
    threading.Thread(target=_process_video_task, args=(task_id, video_url)).start()
    return JsonResponse({'status': 'processing', 'task_id': task_id})


@csrf_exempt # Consider adding proper CSRF protection in production
def generate_short(request):
    """Generates a short, uploads to S3, and updates the database."""
    if request.method != 'POST': return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)
    try:
        data = json.loads(request.body)
        video_id = data.get('video_id')
        start_time_str = data.get('start_time')
        end_time_str = data.get('end_time')
        title = data.get('title', f"Short from {video_id}")
        description = data.get('description', '')
        tags_str = data.get('tags', '')

        # Parse tags
        tags = [tag.strip() for tag in tags_str.split(',') if tag.strip()] if tags_str else []

        parent_video = get_object_or_404(DownloadedVideo, video_id=video_id)
        
        # Create a UUID for the short
        short_uuid = uuid.uuid4()
        short_filename = f"{short_uuid}.mp4"
        short_thumbnail_filename = f"{short_uuid}.webp" # Changed to webp for consistency

        # Define local temporary paths for short generation
        local_short_path = os.path.join(settings.MEDIA_ROOT, 'shorts', short_filename)
        local_short_thumbnail_path = os.path.join(settings.MEDIA_ROOT, 'shorts', short_thumbnail_filename)
        # Temp path for original video downloaded from S3
        local_original_video_path = os.path.join(settings.MEDIA_ROOT, 'temp', f"{video_id}_temp.mp4")

        # Ensure local media directories exist
        os.makedirs(os.path.dirname(local_short_path), exist_ok=True)
        os.makedirs(os.path.dirname(local_original_video_path), exist_ok=True)

        # --- Download the original video from S3 to local temp if S3 is enabled ---
        if settings.AWS_S3_ENABLED and s3_client and parent_video.file_path:
            try:
                s3_client.download_file(
                    settings.AWS_STORAGE_BUCKET_NAME,
                    parent_video.file_path,
                    local_original_video_path
                )
                logger.info(f"Downloaded {parent_video.file_path} from S3 to {local_original_video_path}")
            except Exception as e:
                logger.error(f"Failed to download original video from S3: {e}")
                return JsonResponse({'status': 'error', 'message': 'Failed to download original video from S3.'}, status=500)
        else:
            # Fallback to local path if S3 not enabled or key missing
            local_original_video_path = os.path.join(settings.BASE_DIR, parent_video.file_path.lstrip('/'))
            if not os.path.exists(local_original_video_path):
                 logger.error(f"Local original video path does not exist: {local_original_video_path}")
                 return JsonResponse({'status': 'error', 'message': 'Original video file not found locally.'}, status=404)

        def time_to_seconds(t):
            """Converts MM:SS or HH:MM:SS to seconds."""
            parts = [int(x) for x in t.split(':')]
            if len(parts) == 3: # HH:MM:SS
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            elif len(parts) == 2: # MM:SS
                return parts[0] * 60 + parts[1]
            return 0 # Should not happen with valid input

        start_s, end_s = time_to_seconds(start_time_str), time_to_seconds(end_time_str)

        # --- MoviePy Short Generation ---
        clip = VideoFileClip(local_original_video_path)

        # Calculate target dimensions for vertical short (9:16 aspect ratio)
        target_w, target_h = 1080, 1920 # Full HD short dimensions

        # Crop and resize logic to fit 9:16 aspect ratio (vertical video)
        final_clip = clip.subclip(start_s, min(end_s, clip.duration)) # First cut the subclip
        
        # Calculate optimal crop for 9:16 ratio
        original_aspect = final_clip.w / final_clip.h
        desired_aspect = target_w / target_h # 1080/1920 = 0.5625

        if original_aspect > desired_aspect:
            # Original is wider than 9:16, crop width
            cropped_width = int(final_clip.h * desired_aspect)
            final_clip = crop(final_clip, width=cropped_width, height=final_clip.h, x_center=final_clip.w/2, y_center=final_clip.h/2)
        else:
            # Original is taller/thinner than 9:16, crop height
            cropped_height = int(final_clip.w / desired_aspect)
            final_clip = crop(final_clip, width=final_clip.w, height=cropped_height, x_center=final_clip.w/2, y_center=final_clip.h/2)
        
        # Resize the cropped clip to the target 1080x1920 dimensions
        final_clip = final_clip.resize(newsize=(target_w, target_h))

        # Write the final short to a temporary local file
        final_clip.write_videofile(
            local_short_path,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=f"{short_uuid}_audio.m4a",
            remove_temp=True,
            fps=24, # Standard frame rate for many platforms
            threads=4 # Use more threads for faster processing if available
        )
        # Generate thumbnail
        final_clip.save_frame(local_short_thumbnail_path, t=0.0) # Save frame at 0 seconds

        final_clip.close() # Close the moviepy clip to release resources
        clip.close() # Close the original clip

        # --- S3 Upload and Database Update ---
        short_s3_key = None
        thumbnail_s3_key = None

        if settings.AWS_S3_ENABLED and s3_client:
            # Upload generated short
            short_s3_key = f"{settings.AWS_S3_GENERATED_SHORTS_FOLDER}{short_filename}"
            if upload_file_to_s3(local_short_path, short_s3_key, 'video/mp4'):
                logger.info(f"Short uploaded to S3: {short_s3_key}")
            else:
                raise Exception("Failed to upload generated short to S3.")
            
            # Upload short thumbnail
            if os.path.exists(local_short_thumbnail_path):
                thumbnail_s3_key = f"{settings.AWS_S3_GENERATED_SHORTS_FOLDER}{short_thumbnail_filename}"
                if upload_file_to_s3(local_short_thumbnail_path, thumbnail_s3_key, 'image/webp'):
                    logger.info(f"Short thumbnail uploaded to S3: {thumbnail_s3_key}")
                else:
                    logger.warning("Failed to upload short thumbnail to S3. Continuing without thumbnail.")
                    thumbnail_s3_key = None
            else:
                logger.warning("No short thumbnail file to upload to S3.")
        
        # Clean up local temporary files for the short and the downloaded original video
        _delete_local_files([local_short_path, local_short_thumbnail_path, local_original_video_path])

        # Save short details to database
        short_obj = GeneratedShort.objects.create(
            id=short_uuid,
            parent_video=parent_video,
            title=title,
            description=description,
            tags=tags,
            start_time=start_s, # Store in seconds
            end_time=end_s,     # Store in seconds
            short_path=short_s3_key if settings.AWS_S3_ENABLED else os.path.relpath(local_short_path, settings.BASE_DIR),
            thumbnail_path=thumbnail_s3_key if settings.AWS_S3_ENABLED else (os.path.relpath(local_short_thumbnail_path, settings.BASE_DIR) if os.path.exists(local_short_thumbnail_path) else None),
            status='completed',
            progress=100
        )

        return JsonResponse({'status': 'success', 'message': 'Short generated and saved.', 'short_id': short_obj.id})

    except Exception as e:
        logger.error(f"Error during short generation: {e}", exc_info=True)
        # Attempt to clean up local files even on error
        _delete_local_files([local_short_path, local_short_thumbnail_path, local_original_video_path])
        return JsonResponse({'status': 'error', 'message': f"An unexpected error occurred: {e}"}, status=500)


def delete_video(request, video_id):
    """Deletes a downloaded video and its associated shorts from S3 (or locally) and the database."""
    if request.method == 'POST':
        video = get_object_or_404(DownloadedVideo, video_id=video_id)
        
        if settings.AWS_S3_ENABLED and s3_client:
            # Delete video and thumbnail from S3
            if video.file_path:
                delete_s3_object(video.file_path)
            if video.thumbnail_path:
                delete_s3_object(video.thumbnail_path)
            
            # Delete all associated shorts and their thumbnails from S3
            for short in video.shorts.all():
                if short.short_path:
                    delete_s3_object(short.short_path)
                if short.thumbnail_path:
                    delete_s3_object(short.thumbnail_path)
        else:
            # Fallback to local deletion if S3 is not enabled
            _delete_local_files([
                os.path.join(settings.BASE_DIR, video.file_path.lstrip('/')),
                os.path.join(settings.BASE_DIR, video.thumbnail_path.lstrip('/')) if video.thumbnail_path else None,
                os.path.join(settings.MEDIA_ROOT, 'videos', f'{video.video_id}.en.vtt') # Assuming VTT is always local if no S3
            ])
            for short in video.shorts.all():
                 _delete_local_files([
                    os.path.join(settings.BASE_DIR, short.short_path.lstrip('/')),
                    os.path.join(settings.BASE_DIR, short.thumbnail_path.lstrip('/')) if short.thumbnail_path else None
                ])

        video.delete() # This will also delete associated shorts due to CASCADE
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)


def delete_short(request, short_id):
    """Deletes a generated short from S3 (or locally) and the database."""
    if request.method == 'POST':
        short = get_object_or_404(GeneratedShort, id=short_id)
        
        if settings.AWS_S3_ENABLED and s3_client:
            # Delete from S3
            if short.short_path:
                delete_s3_object(short.short_path)
            if short.thumbnail_path:
                delete_s3_object(short.thumbnail_path)
        else:
            # Fallback to local deletion
            _delete_local_files([
                os.path.join(settings.BASE_DIR, short.short_path.lstrip('/')),
                os.path.join(settings.BASE_DIR, short.thumbnail_path.lstrip('/')) if short.thumbnail_path else None
            ])

        short.delete()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)


def download_short(request, filename):
    """Redirects to the S3 URL of the short for download, or serves locally as fallback."""
    if settings.AWS_S3_ENABLED:
        # Assuming filename directly corresponds to the short's object key within the shorts folder
        s3_object_key = f"{settings.AWS_S3_GENERATED_SHORTS_FOLDER}{filename}"
        s3_url = f"https://{settings.AWS_S3_CUSTOM_DOMAIN}/{s3_object_key}"
        return redirect(s3_url) # Redirect to the S3 URL
    else:
        # Fallback to local file serving if S3 is not enabled
        file_path = os.path.join(settings.MEDIA_ROOT, 'shorts', filename)
        if os.path.exists(file_path):
            return FileResponse(open(file_path, 'rb'), content_type='video/mp4', as_attachment=True, filename=filename)
        else:
            raise Http404("Short not found.")