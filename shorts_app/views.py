

import os
import uuid
import json
from django.shortcuts import render, redirect
from django.conf import settings
from django.http import JsonResponse, FileResponse, Http404
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont

# Basic form for video URL input
def index(request):
    return render(request, 'shorts_app/index.html')

# Video download aur processing logic
def process_video(request):
    if request.method == 'POST':
        video_url = request.POST.get('video_url')
        if not video_url:
            return JsonResponse({'status': 'error', 'message': 'Video URL is required.'})

        output_dir = os.path.join(settings.MEDIA_ROOT, 'videos')
        os.makedirs(output_dir, exist_ok=True)

        video_id = str(uuid.uuid4()) # Unique ID for each video
        video_path = os.path.join(output_dir, f'{video_id}.mp4')

        # YouTubeDL options
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

            # Dummy "most viewed clips" for demonstration 
            # For a real application, you'd need advanced video analysis.
            
            suggested_clips = []
            if video_duration > 0:
                # Example: Suggest a 15-second clip from the middle
                start_middle = max(0, (video_duration // 2) - 7)
                end_middle = min(video_duration, (video_duration // 2) + 8)
                suggested_clips.append({
                    'start': f'{int(start_middle // 60):02d}:{int(start_middle % 60):02d}',
                    'end': f'{int(end_middle // 60):02d}:{int(end_middle % 60):02d}',
                    'description': 'Middle part (suggested)'
                })
                # Example: Suggest a 15-second clip from the beginning
                suggested_clips.append({
                    'start': '00:00',
                    'end': '00:15',
                    'description': 'Beginning part (suggested)'
                })


            return JsonResponse({
                'status': 'success',
                'message': 'Video downloaded and ready for trimming!',
                'video_path': f'/media/videos/{os.path.basename(video_path)}', # Relative path for frontend
                'video_title': video_title,
                'video_duration': video_duration, # Total duration in seconds
                'suggested_clips': suggested_clips # List of suggested clip timings
            })

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Error downloading video: {str(e)}'})

    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})


# Function to generate short (auto or manual)
def generate_short(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        video_relative_path = data.get('video_path')
        start_time_str = data.get('start_time') # e.g., "00:10"
        end_time_str = data.get('end_time')   # e.g., "00:25"
        add_thumbnail = data.get('add_thumbnail', False)
        tags_str = data.get('tags', '')

        if not video_relative_path or not start_time_str or not end_time_str:
            return JsonResponse({'status': 'error', 'message': 'Missing video path or trim times.'})

        video_full_path = os.path.join(settings.BASE_DIR, video_relative_path.lstrip('/'))

        if not os.path.exists(video_full_path):
            return JsonResponse({'status': 'error', 'message': 'Original video not found.'})

        try:
            # Convert HH:MM:SS or MM:SS to seconds
            def time_to_seconds(time_str):
                parts = [int(p) for p in time_str.split(':')]
                if len(parts) == 3: # HH:MM:SS
                    return parts[0] * 3600 + parts[1] * 60 + parts[2]
                elif len(parts) == 2: # MM:SS
                    return parts[0] * 60 + parts[1]
                return 0

            start_seconds = time_to_seconds(start_time_str)
            end_seconds = time_to_seconds(end_time_str)

            if start_seconds >= end_seconds:
                return JsonResponse({'status': 'error', 'message': 'Start time must be less than end time.'})

            clip = VideoFileClip(video_full_path).subclip(start_seconds, end_seconds)

            # Copyright Removal (Basic): Replace audio with generic background music
            # (You'd need a library of royalty-free music)
            # For simplicity, we'll just remove original audio and keep it silent for now.
            # To add music:
            # background_music_path = os.path.join(settings.BASE_DIR, 'static/music/background.mp3') # Pre-downloaded music
            # if os.path.exists(background_music_path):
            #     audio_clip = AudioFileClip(background_music_path).set_duration(clip.duration)
            #     clip = clip.set_audio(audio_clip.audio_fadein(1).audio_fadeout(1))
            # else:
            #     clip = clip.without_audio() # Remove original audio if no background music
            clip = clip.without_audio() # For now, remove audio

            # Save the short video
            shorts_output_dir = os.path.join(settings.MEDIA_ROOT, 'shorts')
            os.makedirs(shorts_output_dir, exist_ok=True)
            short_filename = f'short_{uuid.uuid4()}.mp4'
            short_path = os.path.join(shorts_output_dir, short_filename)
            clip.write_videofile(short_path, codec="libx264", audio_codec="aac")

            # Generate Thumbnail
            thumbnail_path = None
            if add_thumbnail:
                thumbnail_dir = os.path.join(settings.MEDIA_ROOT, 'thumbnails')
                os.makedirs(thumbnail_dir, exist_ok=True)
                thumbnail_filename = f'thumbnail_{uuid.uuid4()}.png'
                thumbnail_path = os.path.join(thumbnail_dir, thumbnail_filename)

                # Get a frame from the middle of the short clip for thumbnail
                frame_time = clip.duration / 2
                clip.save_frame(thumbnail_path, t=frame_time)

                # Optional: Add text overlay to thumbnail (e.g., tags)
                if tags_str:
                    img = Image.open(thumbnail_path)
                    draw = ImageDraw.Draw(img)
                    try:
                        font = ImageFont.truetype("arial.ttf", 40) # Adjust font and size as needed
                    except IOError:
                        font = ImageFont.load_default() # Fallback to default font
                    text_color = (255, 255, 255) # White
                    text_position = (10, 10) # Top-left
                    draw.text(text_position, f"Tags: {tags_str}", font=font, fill=text_color)
                    img.save(thumbnail_path)


            return JsonResponse({
                'status': 'success',
                'message': 'Short created successfully!',
                'short_url': f'/media/shorts/{short_filename}',
                'thumbnail_url': f'/media/thumbnails/{thumbnail_filename}' if thumbnail_path else None
            })

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Error processing video: {str(e)}'})

    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})


# Function to download the generated short
def download_short(request, filename):
    short_path = os.path.join(settings.MEDIA_ROOT, 'shorts', filename)
    if os.path.exists(short_path):
        response = FileResponse(open(short_path, 'rb'), content_type='video/mp4')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    else:
        raise Http404("Short not found")