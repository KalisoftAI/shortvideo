import os
import uuid
import json
import logging
import re
import threading
import glob
import webvtt
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode
import tempfile

from django.shortcuts import render, get_object_or_404
from django.conf import settings
from django.http import JsonResponse, HttpResponseRedirect, Http404
from django.core.cache import cache
from django.core.files import File
from django.core.files.storage import default_storage
from yt_dlp import YoutubeDL

from .models import DownloadedVideo, GeneratedShort
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect
from django.contrib import messages
from .forms import UserRegisterForm
import google.generativeai as genai
import ffmpeg
import httpx
import secrets

logger = logging.getLogger(__name__)

FREE_PLAN_VIDEO_LIMIT = 2

# --- Configure Gemini ---
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

# --- Cookie file configuration (cross-platform) ---
_cookie_file_path = os.environ.get('YOUTUBE_COOKIE_FILE', '')
YOUTUBE_COOKIE_FILE = ''
if _cookie_file_path:
    _resolved = Path(_cookie_file_path)
    if _resolved.is_file():
        YOUTUBE_COOKIE_FILE = str(_resolved)
    else:
        logger.warning(f"YouTube cookie file configured but not found: {_cookie_file_path}. Proceeding without cookies.")


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
                request.session.set_expiry(1209600)
            else:
                request.session.set_expiry(0)
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


# --- Google OAuth ---

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def google_login_view(request):
    state = secrets.token_urlsafe(32)
    request.session['google_oauth_state'] = state
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    logger.info("Initiating Google OAuth login flow.")
    return redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


def google_callback_view(request):
    if request.GET.get('error'):
        messages.error(request, "Google sign-in was cancelled.")
        return redirect('shorts_app:login')

    returned_state = request.GET.get('state')
    expected_state = request.session.pop('google_oauth_state', None)
    if not returned_state or returned_state != expected_state:
        messages.error(request, "Invalid OAuth state. Please try again.")
        return redirect('shorts_app:login')

    code = request.GET.get('code')
    if not code:
        messages.error(request, "Google sign-in failed. Please try again.")
        return redirect('shorts_app:login')

    try:
        token_response = httpx.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }, timeout=10)
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]

        userinfo_response = httpx.get(GOOGLE_USERINFO_URL, headers={
            "Authorization": f"Bearer {access_token}"
        }, timeout=10)
        userinfo_response.raise_for_status()
        userinfo = userinfo_response.json()
    except httpx.HTTPError as e:
        logger.error(f"Google OAuth error: {e}")
        messages.error(request, "Something went wrong signing in with Google. Please try again.")
        return redirect('shorts_app:login')

    email = userinfo.get("email")
    full_name = userinfo.get("name", "")
    if not email:
        messages.error(request, "Could not retrieve email from Google.")
        return redirect('shorts_app:login')

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        base_username = email.split("@")[0]
        username = base_username
        suffix = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{suffix}"
            suffix += 1
        user = User(username=username, email=email, first_name=full_name)
        user.set_unusable_password()
        user.save()

    login(request, user)
    messages.success(request, f"Welcome, {user.first_name or user.username}!")
    return redirect('shorts_app:index')


# --- AI Suggestion ---

def get_ai_suggested_clips(transcript: str, video_duration: int):
    if not genai_configured or not genai:
        return []
    if not transcript:
        logger.warning("Empty transcript provided to Gemini")
        return []

    model_names = ['gemini-2.5-flash', 'gemini-1.5-flash', 'gemini-2.0-flash']
    last_error = None

    prompt = f"""
            You are 'ClipGenius,' an AI expert specializing in identifying viral moments within long-form video content for platforms like YouTube Shorts, TikTok, and Reels. Your goal is to find the most compelling segments that can stand alone as engaging short videos.

            Analyze the following transcript (total video duration: {video_duration} seconds).

            **Transcript:**
            {transcript}

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

    for model_name in model_names:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            json_response_text = response.text.strip().replace("```json", "").replace("```", "")
            raw_clips = json.loads(json_response_text)
            clips = [c for c in raw_clips if isinstance(c, dict) and all(k in c for k in ['start_time', 'title', 'tags'])]
            if clips:
                logger.info(f"Gemini model '{model_name}' returned {len(clips)} clips")
                return clips
            logger.warning(f"Gemini model '{model_name}' returned empty clip list, trying next model...")
        except Exception as e:
            last_error = e
            logger.warning(f"Gemini model '{model_name}' failed: {e}")
            continue

    logger.error(f"All Gemini models failed. Last error: {last_error}", exc_info=True)
    return []


def get_youtube_id(url):
    if not url:
        return None

    parsed = urlparse(url)

    if parsed.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            p = parse_qs(parsed.query)
            return p.get('v', [None])[0]
        if parsed.path.startswith(('/embed/', '/v/')):
            return parsed.path.split('/')[-1]

    if parsed.hostname == 'youtu.be':
        return parsed.path[1:]

    return None


@login_required
def index(request):
    processed_videos = DownloadedVideo.objects.filter(user=request.user).order_by('-created_at')
    generated_shorts = GeneratedShort.objects.filter(user=request.user).select_related('parent_video').order_by('-created_at')
    video_count = DownloadedVideo.objects.filter(user=request.user).count()
    return render(request, 'shorts_app/index.html', {
        'videos': processed_videos,
        'shorts': generated_shorts,
        'video_count': video_count,
        'video_limit': FREE_PLAN_VIDEO_LIMIT,
    })


@login_required
def check_progress(request, task_id):
    return JsonResponse(cache.get(task_id, {"status": "PENDING", "progress": 0, "message": "Initializing..."}))


@login_required
def process_video(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request.'})

    video_url = request.POST.get('video_url')
    video_id = request.POST.get('video_id') or get_youtube_id(video_url)
    if not video_id:
        return JsonResponse({'status': 'error', 'message': 'Valid YouTube URL or Video ID is required.'})

    task_id = str(uuid.uuid4())
    user_id = request.user.id

    def long_running_task():
        task_user = User.objects.get(pk=user_id)

        def progress_hook(d):
            if d['status'] == 'downloading':
                percent_str = d.get('_percent_str', '0.0%')
                cleaned_str = re.sub(r'\x1b\[[0-9;]*m', '', percent_str).replace('%', '').strip()
                try:
                    progress = float(cleaned_str)
                    cache.set(task_id, {"status": "processing", "progress": progress, "message": "Downloading video..."})
                except ValueError:
                    pass
            elif d['status'] == 'finished':
                cache.set(task_id, {"status": "processing", "progress": 100, "message": "Download complete. Analyzing..."})

        try:
            video_record = DownloadedVideo.objects.get(video_id=video_id, user=task_user)
            if not video_record.suggestions or not isinstance(video_record.suggestions, list):
                raise ValueError("Suggestions needed.")
            cache.set(task_id, {"status": "processing", "progress": 100,
                                "message": "Found existing video. Loading suggestions..."})
        except (DownloadedVideo.DoesNotExist, ValueError):
            try:
                url_to_process = video_url or f'https://www.youtube.com/watch?v={video_id}'
                with tempfile.TemporaryDirectory() as tmpdir:
                    output_template = os.path.join(tmpdir, f'{video_id}.%(ext)s')
                    ydl_opts = {
                        'format': 'best[height<=1080][ext=mp4]',
                        'outtmpl': output_template,
                        'merge_output_format': 'mp4',
                        'noplaylist': True,
                        'writesubtitles': True,
                        'writeautomaticsub': True,
                        'subtitleslangs': ['en', 'en-US', 'en-GB', 'hi', 'mr'],
                        'subtitlesformat': 'vtt',
                        'writethumbnail': True,
                        'nocolor': True,
                        'progress_hooks': [progress_hook],
                        'ignoreerrors': True,
                        'socket_timeout': 30,
                    }
                    if YOUTUBE_COOKIE_FILE:
                        ydl_opts['cookiefile'] = YOUTUBE_COOKIE_FILE

                    with YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url_to_process, download=True)

                    temp_video_path = os.path.join(tmpdir, f'{video_id}.mp4')
                    temp_thumbnail_path_webp = os.path.join(tmpdir, f'{video_id}.webp')
                    temp_thumbnail_path_jpg = os.path.join(tmpdir, f'{video_id}.jpg')
                    vtt_files = glob.glob(os.path.join(tmpdir, f'{video_id}.*.vtt'))
                    temp_transcript_path = vtt_files[0] if vtt_files else None

                    if not os.path.exists(temp_video_path):
                        cache.set(task_id, {'status': 'error', 'message': 'Video file not found after download.'})
                        return

                    video_storage_key = f'videos/{video_id}.mp4'
                    with open(temp_video_path, 'rb') as f:
                        default_storage.save(video_storage_key, File(f))

                    thumbnail_key = None
                    if os.path.exists(temp_thumbnail_path_webp):
                        thumbnail_key = f'thumbnails/{video_id}.webp'
                        with open(temp_thumbnail_path_webp, 'rb') as f:
                            default_storage.save(thumbnail_key, File(f))
                    elif os.path.exists(temp_thumbnail_path_jpg):
                        thumbnail_key = f'thumbnails/{video_id}.jpg'
                        with open(temp_thumbnail_path_jpg, 'rb') as f:
                            default_storage.save(thumbnail_key, File(f))

                    video_record, created = DownloadedVideo.objects.get_or_create(
                        video_id=video_id,
                        defaults={
                            'user': task_user,
                            'title': info.get('title', 'N/A'),
                            'duration': info.get('duration', 0),
                            'file_path': video_storage_key,
                            'thumbnail_path': thumbnail_key,
                        }
                    )
                    if not created:
                        for attr, val in [('title', info.get('title', 'N/A')), ('duration', info.get('duration', 0)),
                                          ('file_path', video_storage_key), ('thumbnail_path', thumbnail_key)]:
                            setattr(video_record, attr, val)
                        video_record.save()

                    suggested_clips = []
                    if temp_transcript_path:
                        transcript = " ".join([c.text.strip().replace('\n', ' ') for c in webvtt.read(temp_transcript_path)])
                        if transcript:
                            suggested_clips = get_ai_suggested_clips(transcript, video_record.duration)
                    video_record.suggestions = suggested_clips
                    video_record.save()

            except Exception as e:
                cache.set(task_id, {'status': 'error', 'message': f'Processing failed: {e}'})
                logger.error(f"Error during video processing and upload: {e}", exc_info=True)
                return

        video_record = get_object_or_404(DownloadedVideo, video_id=video_id, user=task_user)
        result = {
            'video_id': video_id,
            'video_title': video_record.title,
            'suggested_clips': video_record.suggestions,
        }
        if not video_record.suggestions:
            result['warning'] = "Transcript not available for this video (no subtitles found in supported languages). You can still select clips manually."
        cache.set(task_id, {'status': 'complete', 'result': result})

    if not DownloadedVideo.objects.filter(video_id=video_id, user=request.user).exists():
        existing_count = DownloadedVideo.objects.filter(user=request.user).count()
        if existing_count >= FREE_PLAN_VIDEO_LIMIT:
            return JsonResponse({
                'status': 'error',
                'message': f'Free plan allows only {FREE_PLAN_VIDEO_LIMIT} videos. Upgrade your plan to process more.',
                'limit_reached': True,
            }, status=403)

    threading.Thread(target=long_running_task).start()
    return JsonResponse({'status': 'processing', 'task_id': task_id})


@login_required
def generate_short(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})
    try:
        data = json.loads(request.body)
        video_id = data.get('video_id')
        clip_data = data.get('clip_data', {})
        aspect_ratio = data.get('aspect_ratio', '9:16')
        start_time_str = clip_data.get('start_time')
        end_time_str = clip_data.get('end_time')
        parent_video = get_object_or_404(DownloadedVideo, video_id=video_id, user=request.user)

        def time_to_seconds(t):
            parts = [int(x) for x in t.split(':')]
            return parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]

        start_s = time_to_seconds(start_time_str)
        end_s = time_to_seconds(end_time_str)
        duration = end_s - start_s
        temp_video_path = None
        temp_short_path = None
        temp_thumb_path = None

        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_video_file:
                with default_storage.open(parent_video.file_path.name, 'rb') as src:
                    temp_video_file.write(src.read())
                temp_video_path = temp_video_file.name

            short_uuid = uuid.uuid4()
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_short_file, \
                 tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_thumb_file:

                temp_short_path = temp_short_file.name
                temp_thumb_path = temp_thumb_file.name

                input_stream = ffmpeg.input(temp_video_path, ss=start_time_str, to=end_time_str)
                video_stream = input_stream.video
                audio_stream = input_stream.audio

                if aspect_ratio == '9:16':
                    video_stream = ffmpeg.filter(video_stream, 'scale', '-1', '1920')
                    video_stream = ffmpeg.filter(video_stream, 'crop', 'ih*9/16', '1920')
                elif aspect_ratio == '16:9':
                    video_stream = ffmpeg.filter(video_stream, 'scale', '1920', '-1')
                    video_stream = ffmpeg.filter(video_stream, 'crop', '1920', 'ih*16/9')

                stream = ffmpeg.output(video_stream, audio_stream, temp_short_path,
                                       vcodec='libx264', acodec='aac', preset='ultrafast',
                                       threads=os.cpu_count() or 4)
                ffmpeg.run(stream, overwrite_output=True, quiet=True)

                (
                    ffmpeg
                    .input(temp_short_path, ss=duration / 2)
                    .output(temp_thumb_path, vframes=1)
                    .run(overwrite_output=True, quiet=True)
                )

                short_storage_key = f'shorts/{short_uuid}.mp4'
                thumbnail_storage_key = f'shorts/{short_uuid}.png'

                with open(temp_short_path, 'rb') as f:
                    default_storage.save(short_storage_key, File(f))
                with open(temp_thumb_path, 'rb') as f:
                    default_storage.save(thumbnail_storage_key, File(f))

            GeneratedShort.objects.create(
                parent_video=parent_video,
                user=request.user,
                title=clip_data.get('title', 'Untitled Short'),
                description=clip_data.get('description', ''),
                tags=clip_data.get('tags', []),
                short_path=short_storage_key,
                thumbnail_path=thumbnail_storage_key,
                start_time=start_time_str,
                end_time=end_time_str
            )
            return JsonResponse({'status': 'success', 'message': 'Short created successfully!'})

        finally:
            if temp_video_path and os.path.exists(temp_video_path):
                os.unlink(temp_video_path)
            if temp_short_path and os.path.exists(temp_short_path):
                os.unlink(temp_short_path)
            if temp_thumb_path and os.path.exists(temp_thumb_path):
                os.unlink(temp_thumb_path)

    except Http404:
        raise
    except Exception as e:
        logger.error(f"Error during short generation: {e}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': 'An unexpected error occurred during short generation.'}, status=500)


# --- Deletion and Download views ---

@login_required
def delete_video(request, video_id):
    if request.method == 'POST':
        video = get_object_or_404(DownloadedVideo, video_id=video_id, user=request.user)
        keys_to_delete = [video.file_path.name]
        if video.thumbnail_path:
            keys_to_delete.append(video.thumbnail_path.name)
        for key in keys_to_delete:
            if key:
                try:
                    default_storage.delete(key)
                    logger.info(f"Deleted storage object: {key}")
                except Exception as e:
                    logger.error(f"Failed to delete storage object {key}: {e}")
        video.delete()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)


@login_required
def delete_short(request, short_id):
    if request.method == 'POST':
        short = get_object_or_404(GeneratedShort, id=short_id, user=request.user)
        keys_to_delete = []
        if short.short_path:
            keys_to_delete.append(short.short_path.name)
        if short.thumbnail_path:
            keys_to_delete.append(short.thumbnail_path.name)
        for key in keys_to_delete:
            if key:
                try:
                    default_storage.delete(key)
                    logger.info(f"Deleted storage object: {key}")
                except Exception as e:
                    logger.error(f"Failed to delete storage object {key}: {e}")
        short.delete()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)


@login_required
def download_short(request, short_id):
    short = get_object_or_404(GeneratedShort, id=short_id, user=request.user)
    storage_key = short.short_path.name

    try:
        url = default_storage.url(storage_key)
        return HttpResponseRedirect(url)
    except Exception as e:
        logger.error(f"Error generating download URL for {storage_key}: {e}")
        raise Http404("File not found or access denied.")
