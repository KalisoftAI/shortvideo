# video_project/views.py

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
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect
from django.contrib import messages
from .forms import UserRegisterForm
import google.generativeai as genai
import ffmpeg 

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

def home(request):
    return render(request, 'shorts_app/landing/home.html')

def pricing(request):
    return render(request, 'shorts_app/pricing.html')

# --- Authentication Views ---

def login_view(request):
    if request.user.is_authenticated:
        return redirect('shorts_app:index')
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            if request.POST.get('remember_me'):
                request.session.set_expiry(1209600)  # 2 weeks
            else:
                request.session.set_expiry(0)  # Until browser close
            return redirect('shorts_app:index')
        messages.error(request, 'Invalid username or password.')
    else:
        form = AuthenticationForm()
    return render(request, 'shorts_app/auth/login.html', {'form': form})

def register_view(request):
    if request.user.is_authenticated:
        return redirect('shorts_app:index')
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Welcome, {user.first_name or user.username}!')
            return redirect('shorts_app:index')
    else:
        form = UserRegisterForm()
    return render(request, 'shorts_app/auth/register.html', {'form': form})

def logout_view(request):
    logout(request)
    return redirect('shorts_app:home')

# # --- AI Suggestion, Get YouTube ID, Index, Check Progress (Unchanged) ---
# def get_ai_suggested_clips(transcript: str, video_duration: int):
#     if not genai_configured or not genai: return []
#     model = genai.GenerativeModel('gemini-1.5-flash-latest')
#     prompt = f"""
#             You are 'ClipGenius,' an AI expert specializing in identifying viral moments within long-form video content for platforms like YouTube Shorts, TikTok, and Reels. Your goal is to find the most compelling segments that can stand alone as engaging short videos.

#             Analyze the following transcript (total video duration: {video_duration} seconds).

#             **Your criteria for selecting a compelling segment are:**
#             1.  **Strong Hook:** The clip must start with a question, a surprising statement, or immediate action to grab the viewer's attention within the first 3 seconds.
#             2.  **Emotional Peak:** Look for moments of humor, excitement, controversy, strong opinion, or heartfelt emotion.
#             3.  **Clear Value:** The segment should offer a key takeaway, a useful tip, a satisfying conclusion, or a fascinating piece of information.
#             4.  **Ideal Length:** Aim for a duration between 30 and 90 seconds.

#             **Your Task:**
#             Return a valid JSON array of up to 3 clip objects. For each object, you MUST provide the following fields:

#             -   `start_time`: The start time in strict "MM:SS" format.
#             -   `end_time`: The end time in strict "MM:SS" format.
#             -   `title`: A catchy, SEO-friendly title under 70 characters.
#             -   `description`: A brief, engaging summary (1-2 sentences). Include 2-3 relevant hashtags.
#             -   `tags`: A JSON array of 5-7 relevant lowercase SEO keywords as strings.
#             -   `virality_score`: Your confidence in the clip's viral potential, rated on a scale of 1 to 10.
#             -   `reasoning`: A brief, one-sentence explanation for why you chose this specific clip based on the criteria above.
#             -   `copyright_concern`: A boolean (true/false). Set to true only if the text explicitly mentions copyrighted material like movie titles or song lyrics.

#             **Example of a perfect output object:**
#             ```json
#             [
#             {{
#                 "start_time": "10:25",
#                 "end_time": "11:15",
#                 "title": "The ONE Productivity Hack That Actually Works",
#                 "description": "Discover the simple trick that completely changed how I manage my day. You have to try this! #productivity #lifehack #efficiency",
#                 "tags": ["productivity", "self improvement", "focus", "work life", "time management", "habits"],
#                 "virality_score": 9,
#                 "reasoning": "This clip has a strong hook, offers clear actionable advice, and solves a common problem.",
#                 "copyright_concern": false
#             }}
#             ]
#             """
#     try:
#         response = model.generate_content(prompt)
#         json_response_text = response.text.strip().replace("```json", "").replace("```", "")
#         raw_clips = json.loads(json_response_text)
#         return [c for c in raw_clips if isinstance(c, dict) and all(k in c for k in ['start_time', 'title', 'tags'])]
#     except Exception as e:
#         logger.error(f"Error calling Gemini API: {e}", exc_info=True)
#         return []

def get_ai_suggested_clips(transcript: str, video_duration: int):
    if not genai_configured or not genai: return []
    # Remove the "-latest" suffix
    model = genai.GenerativeModel('gemini-2.5-pro-preview-03-25')
    
    # --- MODIFIED PROMPT ---
    prompt = f"""
            You are 'ClipGenius,' an AI expert specializing in identifying viral moments within long-form video content for platforms like YouTube Shorts, TikTok, and Reels. Your goal is to find the most compelling segments that can stand alone as engaging short videos.

            Analyze the following transcript (total video duration: {video_duration} seconds).

            **Your criteria for selecting a compelling segment are:**
            1.  **Strong Hook:** The clip must start with a question, a surprising statement, or immediate action to grab the viewer's attention within the first 3 seconds.
            2.  **Emotional Peak:** Look for moments of humor, excitement, controversy, strong opinion, or heartfelt emotion.
            3.  **Clear Value:** The segment should offer a key takeaway, a useful tip, a satisfying conclusion, or a fascinating piece of information.
            4.  **Ideal Length:** Aim for a duration between 30 and 90 seconds.

            **Your Task:**
            Return a valid JSON array containing **2 to 3** of the best clip objects. For each object, you MUST provide all the specified fields.

            **Example of a perfect output object:**
            ```json
            [
              {{
                "start_time": "10:25",
                "end_time": "11:15",
                "title": "The ONE Productivity Hack That Actually Works",
                "description": "Discover the simple trick that completely changed how I manage my day. You have to try this! #productivity #lifehack #efficiency",
                "tags": ["productivity", "self improvement", "focus", "work life", "time management", "habits"],
                "virality_score": 9,
                "reasoning": "This clip has a strong hook, offers clear actionable advice, and solves a common problem.",
                "copyright_concern": false
              }},
              {{
                "start_time": "25:10",
                "end_time": "26:05",
                "title": "Why Most Side Hustles Fail (and how to succeed)",
                "description": "A surprisingly honest take on the realities of starting a side business. #entrepreneur #sidehustle #businessadvice",
                "tags": ["business", "motivation", "startup", "small business", "finance", "success"],
                "virality_score": 8,
                "reasoning": "This segment addresses a common pain point with a controversial and engaging perspective.",
                "copyright_concern": false
              }}
            ]
            ```
            """
    # --- END OF MODIFIED PROMPT ---
            
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
    Handles standard ([youtube.com/watch](https://youtube.com/watch)), short (youtu.be/), and embed URLs.
    """
    if not url:
        return None

    query = urlparse(url)

    # Handle standard and mobile URLs (e.g., [youtube.com/watch?v=](https://youtube.com/watch?v=)...)
    if query.hostname in ('youtube.com', '[www.youtube.com](https://www.youtube.com)', 'm.youtube.com'):
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
                # FIX: Reconstruct the URL if it's missing, using the video_id.
                url_to_process = video_url or f'https://www.youtube.com/watch?v={video_id}'
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
                        info = ydl.extract_info(url_to_process, download=True)

                    temp_video_path = os.path.join(tmpdir, f'{video_id}.mp4')
                    temp_thumbnail_path_webp = os.path.join(tmpdir, f'{video_id}.webp')
                    temp_thumbnail_path_jpg = os.path.join(tmpdir, f'{video_id}.jpg')
                    temp_transcript_path = os.path.join(tmpdir, f'{video_id}.en.vtt')

                    if not os.path.exists(temp_video_path):
                        cache.set(task_id, {'status': 'error', 'message': f'Video file not found after download.'})
                        return

                    # Upload video to S3
                    video_s3_key = f'yt_video/{video_id}.mp4'
                    with open(temp_video_path, 'rb') as f:
                        DownloadedVideo.file_path.field.storage.save(video_s3_key, File(f))

                    thumbnail_s3_key = None
                    if os.path.exists(temp_thumbnail_path_webp):
                        thumbnail_s3_key = f'yt_video/{video_id}.webp'
                        with open(temp_thumbnail_path_webp, 'rb') as f:
                            DownloadedVideo.thumbnail_path.field.storage.save(thumbnail_s3_key, File(f))
                    elif os.path.exists(temp_thumbnail_path_jpg):
                        thumbnail_s3_key = f'yt_video/{video_id}.jpg'
                        with open(temp_thumbnail_path_jpg, 'rb') as f:
                            DownloadedVideo.thumbnail_path.field.storage.save(thumbnail_s3_key, File(f))

                    video_record, _ = DownloadedVideo.objects.update_or_create(
                        video_id=video_id,
                        defaults={
                            'title': info.get('title', 'N/A'),
                            'duration': info.get('duration', 0),
                            'file_path': video_s3_key, # Store the S3 key
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

        def time_to_seconds(t):
            parts = [int(x) for x in t.split(':')]
            return parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]

        start_s, end_s = time_to_seconds(start_time_str), time_to_seconds(end_time_str)
        duration = end_s - start_s
        temp_video_path = None
        temp_short_path = None
        temp_thumb_path = None
        
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_video_file:
                s3_client.download_fileobj(settings.AWS_STORAGE_BUCKET_NAME, parent_video.file_path.name, temp_video_file)
                temp_video_path = temp_video_file.name

            # === MOVIEPY BLOCK REPLACED WITH FFMPEG (MUCH FASTER) ===
            short_uuid = uuid.uuid4()
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_short_file, \
                 tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_thumb_file:
                
                temp_short_path = temp_short_file.name
                temp_thumb_path = temp_thumb_file.name

                input_stream = ffmpeg.input(temp_video_path, ss=start_time_str, to=end_time_str)
                video_stream = input_stream.video
                audio_stream = input_stream.audio

                if aspect_ratio == '9:16':
                    # Vertical video crop and scale logic
                    video_stream = ffmpeg.filter(video_stream, 'scale', '-1', '1920')
                    video_stream = ffmpeg.filter(video_stream, 'crop', '1080', '1920')
                elif aspect_ratio == '16:9':
                    # Horizontal video crop logic
                    video_stream = ffmpeg.filter(video_stream, 'scale', '1920', '-1')
                    video_stream = ffmpeg.filter(video_stream, 'crop', '1920', '1080')
                
                # Create the short video
                stream = ffmpeg.output(video_stream, audio_stream, temp_short_path, vcodec='libx264', acodec='aac', preset='ultrafast', threads=os.cpu_count())
                ffmpeg.run(stream, overwrite_output=True, quiet=True)
                
                # Create the thumbnail
                (
                    ffmpeg
                    .input(temp_short_path, ss=duration / 2)
                    .output(temp_thumb_path, vframes=1)
                    .run(overwrite_output=True, quiet=True)
                )

                # Upload short and thumbnail to S3
                short_s3_key = f'shorts/{short_uuid}.mp4'
                thumbnail_s3_key = f'shorts/{short_uuid}.png'

                with open(temp_short_path, 'rb') as f:
                    GeneratedShort.short_path.field.storage.save(short_s3_key, File(f))
                with open(temp_thumb_path, 'rb') as f:
                    GeneratedShort.thumbnail_path.field.storage.save(thumbnail_s3_key, File(f))
            # === END OF FFMPEG BLOCK ===

            GeneratedShort.objects.create(
                parent_video=parent_video,
                title=clip_data.get('title', 'Untitled Short'),
                description=clip_data.get('description', ''),
                tags=clip_data.get('tags', []),
                short_path=short_s3_key,
                thumbnail_path=thumbnail_s3_key,
                start_time=start_time_str,
                end_time=end_time_str
            )
            return JsonResponse({'status': 'success', 'message': 'Short created successfully!'})
        
        finally:
            if temp_video_path and os.path.exists(temp_video_path): os.unlink(temp_video_path)
            if temp_short_path and os.path.exists(temp_short_path): os.unlink(temp_short_path)
            if temp_thumb_path and os.path.exists(temp_thumb_path): os.unlink(temp_thumb_path)
                
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
        keys_to_delete = [video.file_path.name]
        if video.thumbnail_path:
            keys_to_delete.append(video.thumbnail_path.name)
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
            ExpiresIn=3600 # URL is valid for 1 hour
        )
        return HttpResponseRedirect(url)
    except ClientError as e:
        logger.error(f"Error generating pre-signed URL for {s3_key}: {e}")
        raise Http404("File not found or access denied.")
    except Exception as e:
        logger.error(f"Unexpected error in download_short: {e}")
        raise Http404("An unexpected error occurred during download preparation.")