# videocraft_app/views.py
import os
import uuid
import json
import logging
import tempfile
import threading

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt # Use carefully for API endpoints
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.conf import settings

import boto3
from botocore.exceptions import ClientError

# MoviePy imports - corrected
from moviepy.editor import ImageSequenceClip, AudioFileClip, CompositeAudioClip, TextClip, CompositeVideoClip, ColorClip, concatenate_videoclips
from moviepy.video.fx.all import fadein, fadeout # For transitions

from .models import VideoCraftProject, ProjectImage, GeneratedVideo

# Gemini API imports
import google.generativeai as genai

logger = logging.getLogger(__name__)

# --- Initialize S3 client ---
s3_client = None
try:
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY and settings.AWS_STORAGE_BUCKET_NAME:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME
        )
except Exception as e:
    logger.error(f"Error initializing S3 client in videocraft_app: {e}")

# --- Configure Gemini API (Moved to global scope) ---
genai_configured = False # Define genai_configured globally
try:
    if settings.GEMINI_API_KEY:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        genai_configured = True
        logger.info("Gemini API configured successfully for VideoCraft.")
    else:
        logger.warning("GEMINI_API_KEY not set. AI features disabled for VideoCraft.")
except Exception as e:
    logger.error(f"Error during Gemini API configuration in VideoCraft: {e}. AI features disabled.")
    genai = None # Ensure genai is None if configuration fails

# --- Helper functions ---
def _delete_files_s3(s3_keys):
    if not s3_client:
        logger.error("S3 client not initialized. Cannot delete files from S3.")
        return

    for key in s3_keys:
        if not key: continue
        try:
            s3_client.delete_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=key)
            logger.info(f"Deleted S3 object: {key}")
        except Exception as e:
            logger.error(f"Failed to delete S3 object {key}: {e}")

# This line imports the Celery task, assuming tasks.py exists in the same app
from .tasks import generate_video_task

# --- Core Views for VideoCraft App ---

def videocraft_index(request):
    """
    Displays all existing video projects.
    """
    projects = VideoCraftProject.objects.all().order_by('-created_at')
    return render(request, 'videocraft_app/index.html', {'projects': projects})

def create_project(request):
    """
    Handles creating a new video project.
    """
    if request.method == 'POST':
        title = request.POST.get('title', 'New Video Project')
        project = VideoCraftProject.objects.create(title=title)
        return JsonResponse({'status': 'success', 'project_id': str(project.id)})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

def project_detail(request, project_id):
    """
    Displays details of a specific video project, including uploaded images.
    """
    project = get_object_or_404(VideoCraftProject, id=project_id)
    images = project.images.all()
    try:
        generated_video = GeneratedVideo.objects.get(project=project)
    except GeneratedVideo.DoesNotExist:
        generated_video = None

    context = {
        'project': project,
        'images': images,
        'generated_video': generated_video,
    }
    return render(request, 'videocraft_app/project_detail.html', context)

@csrf_exempt
def upload_image(request, project_id):
    """
    Handles uploading images for a specific project.
    """
    if request.method == 'POST' and request.FILES.getlist('images'):
        project = get_object_or_404(VideoCraftProject, id=project_id)
        
        uploaded_files = request.FILES.getlist('images')
        for i, f in enumerate(uploaded_files):
            last_image = project.images.order_by('-order').first()
            new_order = (last_image.order + 1) if last_image else 0

            project_image = ProjectImage(project=project, image_file=f, order=new_order)
            project_image.save()

        return JsonResponse({'status': 'success', 'message': 'Images uploaded successfully.'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request or no images uploaded.'}, status=400)

@csrf_exempt
def delete_image(request, image_id):
    """
    Deletes an image from a project.
    """
    if request.method == 'POST':
        image = get_object_or_404(ProjectImage, id=image_id)
        if image.image_file:
            _delete_files_s3([image.image_file.name])
        image.delete()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

@csrf_exempt
def update_image_details(request, image_id):
    """
    Updates details (order, text overlay, duration) for a specific image.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            image = get_object_or_404(ProjectImage, id=image_id)

            if 'order' in data:
                image.order = data['order']
            if 'text_overlay' in data:
                image.text_overlay = data['text_overlay']
            if 'duration' in data:
                image.duration = float(data['duration'])
            
            image.save()
            return JsonResponse({'status': 'success'})
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON.'}, status=400)
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

@csrf_exempt
def generate_video(request, project_id):
    """
    Triggers the video generation process for a project.
    """
    if request.method == 'POST':
        project = get_object_or_404(VideoCraftProject, id=project_id)
        
        try:
            data = json.loads(request.body)
            audio_url = data.get('audio_url')
            transition_type = data.get('transition_type', 'fade')
            text_color = data.get('text_color', 'white')
            font_size = data.get('font_size', 50)
            font = data.get('font', 'sans')
        except json.JSONDecodeError:
            audio_url = None
            transition_type = 'fade'
            text_color = 'white'
            font_size = 50
            font = 'sans'

        task = generate_video_task.delay(
            str(project.id), 
            audio_url, 
            transition_type, 
            text_color, 
            font_size, 
            font
        )
        return JsonResponse({'status': 'processing', 'task_id': task.id})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

def check_video_generation_progress(request, task_id):
    """
    Checks the status of the video generation task.
    """
    from celery.result import AsyncResult
    task = AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {"status": "PENDING", "progress": 0, "message": "Task is waiting to be processed."}
    elif task.state == 'PROGRESS':
        response = task.info
    elif task.state == 'SUCCESS':
        response = {"status": "SUCCESS", "progress": 100, "message": "Video generation complete!", "video_url": task.result}
    elif task.state == 'FAILURE':
        response = {"status": "FAILED", "progress": 0, "message": f"Video generation failed: {task.info}"}
    else:
        response = {"status": task.state, "progress": 0, "message": "Unknown status."}
    return JsonResponse(response)

@csrf_exempt
def delete_project(request, project_id):
    """
    Deletes an entire project, including its images and generated video from S3.
    """
    if request.method == 'POST':
        project = get_object_or_404(VideoCraftProject, id=project_id)
        
        s3_keys_to_delete = []
        for img in project.images.all():
            if img.image_file:
                s3_keys_to_delete.append(img.image_file.name)
        
        try:
            generated_video = GeneratedVideo.objects.get(project=project)
            if generated_video.video_file:
                s3_keys_to_delete.append(generated_video.video_file.name)
            generated_video.delete()
        except GeneratedVideo.DoesNotExist:
            pass

        _delete_files_s3(s3_keys_to_delete)
        project.delete()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

@csrf_exempt
def generate_text_overlay(request, image_id):
    """
    Generates text overlay suggestion for a given image using Gemini API.
    """
    if request.method == 'POST':
        # Check if genai_configured is True at the beginning of the function
        if not genai_configured:
            return JsonResponse({'status': 'error', 'message': 'AI features are not configured. Please set GEMINI_API_KEY.'}, status=503)

        image = get_object_or_404(ProjectImage, id=image_id)
        
        try:
            prompt = f"Suggest a concise and creative text overlay for an image that is part of a video project. The image is currently associated with a video. Keep it short, no more than 10-15 words. Example: 'Amazing sunset view' or 'Exploring new paths'. Provide only the text, no extra sentences or formatting."
            
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content(prompt)
            
            generated_text = ""
            if response and response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text'):
                        generated_text += part.text
            
            generated_text = generated_text.strip().replace('*', '').replace('"', '')
            
            return JsonResponse({'status': 'success', 'text_overlay': generated_text})
        except Exception as e:
            logger.error(f"Error generating text overlay for image {image_id}: {e}")
            return JsonResponse({'status': 'error', 'message': f'Failed to generate text overlay: {e}'}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

