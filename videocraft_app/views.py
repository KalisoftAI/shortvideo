# videocraft_app/views.py
import os
import uuid
import json
import logging
import tempfile
import threading # Used for running video generation in a background thread

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.conf import settings
from django.db import transaction # For atomic operations

import boto3
from botocore.exceptions import ClientError

# MoviePy imports (used in the core generation function)
from moviepy.editor import ImageSequenceClip, AudioFileClip, CompositeAudioClip, TextClip, CompositeVideoClip, ColorClip, concatenate_videoclips
from moviepy.video.fx.all import fadein, fadeout

from .models import VideoCraftProject, ProjectImage, GeneratedVideo
from .video_processing_utils import generate_video_core # Import the core video generation function

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
    """Deletes files from S3 given a list of keys."""
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

def _upload_file_to_s3(file_obj, destination_prefix='videocraft_uploads'):
    """Uploads a Django InMemoryUploadedFile or TemporaryUploadedFile to S3."""
    if not s3_client:
        logger.error("S3 client not initialized. Cannot upload files to S3.")
        return None

    filename = file_obj.name
    # Generate a unique filename to prevent clashes
    unique_filename = f"{destination_prefix}/{uuid.uuid4()}_{filename}"
    
    try:
        # The S3Boto3Storage handles uploading directly using default_storage if configured
        # For direct boto3 client usage, we need to read content and upload
        file_obj.seek(0) # Ensure file pointer is at the beginning
        s3_client.upload_fileobj(file_obj, settings.AWS_STORAGE_BUCKET_NAME, unique_filename)
        logger.info(f"Uploaded {filename} to S3 as {unique_filename}")
        return unique_filename # Return the S3 key
    except ClientError as e:
        logger.error(f"Failed to upload {filename} to S3: {e}")
        return None
    except Exception as e:
        logger.error(f"Error during S3 upload of {filename}: {e}")
        return None

# --- Core Views for VideoCraft App ---

def videocraft_index(request):
    """
    Displays all existing video projects.
    """
    projects = VideoCraftProject.objects.all().order_by('-created_at')
    # Pre-fetch generated videos for efficiency
    for project in projects:
        try:
            project.generated_video = GeneratedVideo.objects.get(project=project)
        except GeneratedVideo.DoesNotExist:
            project.generated_video = None
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
        with transaction.atomic(): # Ensure all image saves are atomic
            for i, f in enumerate(uploaded_files):
                last_image = project.images.order_by('-order').first()
                new_order = (last_image.order + 1) if last_image else 0

                project_image = ProjectImage(project=project, image_file=f, order=new_order)
                project_image.save() # S3 storage handles the upload here

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
            _delete_files_s3([image.image_file.name]) # Delete from S3
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
        except Exception as e:
            logger.error(f"Error updating image details for {image_id}: {e}")
            return JsonResponse({'status': 'error', 'message': f'Failed to update image details: {e}'}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

@csrf_exempt
def generate_video(request, project_id):
    """
    Triggers the video generation process for a project.
    Now runs in a separate thread and updates database status.
    """
    if request.method == 'POST':
        project = get_object_or_404(VideoCraftProject, id=project_id)
        
        audio_file_s3_key = None
        external_audio_url = None

        if 'audio_file' in request.FILES: # Handle local audio file upload
            uploaded_audio_file = request.FILES['audio_file']
            audio_file_s3_key = _upload_file_to_s3(uploaded_audio_file, 'videocraft_audio')
            if not audio_file_s3_key:
                return JsonResponse({'status': 'error', 'message': 'Failed to upload audio file to S3.'}, status=500)
            
            # Store the uploaded audio file in GeneratedVideo model
            generated_video_obj, created = GeneratedVideo.objects.get_or_create(project=project)
            generated_video_obj.audio_file = uploaded_audio_file # Assign the file
            generated_video_obj.audio_file_url = generated_video_obj.audio_file.url # Get S3 URL
            generated_video_obj.save()

        else: # Try to get external audio URL from JSON body
            try:
                data = json.loads(request.body)
                external_audio_url = data.get('audio_url')
            except json.JSONDecodeError:
                pass # No JSON body or invalid JSON

        try:
            # Extract other parameters from JSON body if present, or use defaults
            data = json.loads(request.body) if request.body else {}
            transition_type = data.get('transition_type', 'fade')
            text_color = data.get('text_color', 'white')
            font_size = data.get('font_size', 50)
            font = data.get('font', 'sans')
        except json.JSONDecodeError:
            transition_type = 'fade'
            text_color = 'white'
            font_size = 50
            font = 'sans'

        # Set initial status for the generated video object
        generated_video_obj, created = GeneratedVideo.objects.get_or_create(project=project)
        generated_video_obj.status = 'processing'
        generated_video_obj.status_message = 'Video generation initiated...'
        # If a local audio file was uploaded, its 'audio_file' field is already set above
        # If only an external URL was provided, set that here
        if external_audio_url and not generated_video_obj.audio_file:
            generated_video_obj.audio_file_url = external_audio_url
        generated_video_obj.save()

        # Run video generation in a separate thread
        thread_args = (
            str(project.id),
            audio_file_s3_key, # Pass S3 key for locally uploaded audio
            external_audio_url, # Pass external URL
            transition_type,
            text_color,
            font_size,
            font
        )
        video_thread = threading.Thread(target=generate_video_core, args=thread_args)
        video_thread.start()

        return JsonResponse({'status': 'processing', 'project_id': str(project.id)})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

def check_video_generation_progress(request, project_id):
    """
    Checks the status of the video generation for a project by looking at the model.
    """
    try:
        generated_video = GeneratedVideo.objects.get(project_id=project_id)
        response_data = {
            "status": generated_video.status,
            "message": generated_video.status_message,
            "video_url": generated_video.video_file.url if generated_video.video_file else None
        }
        if generated_video.status == 'completed':
            response_data['progress'] = 100
        elif generated_video.status == 'processing':
            response_data['progress'] = -1 # Indeterminate progress
        else: # pending, failed, etc.
            response_data['progress'] = 0
        return JsonResponse(response_data)
    except GeneratedVideo.DoesNotExist:
        return JsonResponse({"status": "PENDING", "progress": 0, "message": "Video generation not yet started or project not found."}, status=404)
    except Exception as e:
        logger.error(f"Error checking video progress for project {project_id}: {e}")
        return JsonResponse({"status": "FAILED", "progress": 0, "message": f"Error checking progress: {e}"}, status=500)

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
            if generated_video.audio_file: # Also delete uploaded audio file
                s3_keys_to_delete.append(generated_video.audio_file.name)
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
            # Ensure response and its structure are valid
            if response and response.candidates and len(response.candidates) > 0 and response.candidates[0].content and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text'):
                        generated_text += part.text
            
            generated_text = generated_text.strip().replace('*', '').replace('"', '')
            
            # Simple check if the generated text is substantially empty after stripping
            if not generated_text:
                 raise ValueError("AI generated an empty or invalid text overlay.")

            return JsonResponse({'status': 'success', 'text_overlay': generated_text})
        except Exception as e:
            logger.error(f"Error generating text overlay for image {image_id}: {e}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': f'Failed to generate text overlay: {e}'}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)