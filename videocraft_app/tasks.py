# videocraft_app/tasks.py
import os
import tempfile
import logging

from celery import shared_task
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage

import boto3
from botocore.exceptions import ClientError

from moviepy.editor import ImageSequenceClip, AudioFileClip, CompositeAudioClip, TextClip, CompositeVideoClip, ColorClip, concatenate_videoclips
from moviepy.video.fx.all import fadein, fadeout # For transitions
from django.core.files.base import ContentFile



from .models import VideoCraftProject, ProjectImage, GeneratedVideo

logger = logging.getLogger(__name__)

# Initialize S3 client (same as in views.py)
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
    logger.error(f"Error initializing S3 client in videocraft_app tasks: {e}")


@shared_task(bind=True)
def generate_video_task(self, project_id, audio_url=None, transition_type='fade', text_color='white', font_size=50, font='sans'):
    """
    Celery task to generate a video from images for a given project.
    """
    try:
        project = VideoCraftProject.objects.get(id=project_id)
        images = project.images.all().order_by('order')

        if not images.exists():
            self.update_state(state='FAILURE', meta={'message': 'No images found for this project.'})
            logger.error(f"Project {project_id}: No images found.")
            return 'No images found.'

        # Create a temporary directory for all processing
        with tempfile.TemporaryDirectory() as tmpdir:
            image_paths = []
            clips = []
            total_duration = 0

            # Download images from S3 to local temp files
            for i, img_obj in enumerate(images):
                self.update_state(state='PROGRESS', meta={'progress': int((i / len(images)) * 50), 'message': f'Downloading image {i+1}/{len(images)}...'})
                temp_image_file_path = os.path.join(tmpdir, f'image_{img_obj.order}_{img_obj.id}.png')
                
                try:
                    s3_client.download_file(settings.AWS_STORAGE_BUCKET_NAME, img_obj.image_file.name, temp_image_file_path)
                    image_paths.append(temp_image_file_path)
                    
                    # Create ImageClip for each image
                    img_clip = ImageSequenceClip([temp_image_file_path], durations=[img_obj.duration])
                    
                    # Add text overlay if present
                    if img_obj.text_overlay:
                        # Determine appropriate font path (e.g., a system font or a provided path)
                        # For simplicity, using 'DejaVuSans' as a common default, or 'Arial.ttf' on Windows etc.
                        # You might need to provide a custom font path if 'font' is not a system-recognized name
                        text_clip = TextClip(
                            img_obj.text_overlay,
                            fontsize=font_size,
                            color=text_color,
                            font=font, # e.g., "sans", "serif", or path to a .ttf file
                            stroke_color='black', # Add stroke for better readability
                            stroke_width=1,
                            method='caption', # For multi-line text wrapping
                            size=(img_clip.w * 0.8, None) # Max width 80% of image width
                        ).set_position(('center', 'bottom')).set_duration(img_clip.duration) # Duration should match image clip
                        
                        img_clip = CompositeVideoClip([img_clip, text_clip])

                    clips.append(img_clip)
                    total_duration += img_obj.duration

                except ClientError as e:
                    logger.error(f"Failed to download image {img_obj.image_file.name} from S3: {e}")
                    self.update_state(state='FAILURE', meta={'message': f'Failed to download image: {e}'})
                    return f'Failed to download image: {e}'
                except Exception as e:
                    logger.error(f"Error processing image {img_obj.id}: {e}")
                    self.update_state(state='FAILURE', meta={'message': f'Error processing image: {e}'})
                    return f'Error processing image: {e}'

            # Add transitions between clips
            final_clips = []
            if transition_type == 'fade' and len(clips) > 1:
                # Apply fade transition between clips
                transition_duration = 0.5 # seconds
                for i, clip in enumerate(clips):
                    final_clips.append(clip)
                    if i < len(clips) - 1:
                        # Add a blank clip for the fade-in of the next clip if needed,
                        # or directly use crossfade methods.
                        # For simplicity with concatenate_videoclips, a common approach is to just concatenate
                        # and rely on MoviePy's default if it applies. For explicit crossfades,
                        # it often involves manually slicing and composing clips.
                        # For this example, let's keep simple concatenation and consider more advanced
                        # transitions as future enhancements if needed.
                        pass # No explicit MoviePy crossfade code added here for simplicity with current `concatenate_videoclips` setup.
            else:
                final_clips = clips # No transitions

            if final_clips: # Ensure there are clips to concatenate
                final_video_clip = concatenate_videoclips(final_clips, method="compose") # Default method
            else:
                self.update_state(state='FAILURE', meta={'message': 'No valid clips to create video.'})
                logger.error(f"Project {project_id}: No valid clips to create video.")
                return 'No valid clips to create video.'


            self.update_state(state='PROGRESS', meta={'progress': 60, 'message': 'Adding audio...'})
            # Add audio if provided
            if audio_url:
                try:
                    # Download audio temporarily if it's an S3 URL
                    if "s3.amazonaws.com" in audio_url: # Basic check for S3 URL
                        temp_audio_file_path = os.path.join(tmpdir, 'background_audio.mp3') # Or other format
                        # Extract S3 key from URL: assuming URL format like https://bucket.s3.region.amazonaws.com/key
                        s3_key_parts = audio_url.split(f".s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/")
                        if len(s3_key_parts) > 1:
                            s3_key = s3_key_parts[1]
                        else: # Fallback if URL format is different
                             s3_key = audio_url.split(f"/{settings.AWS_STORAGE_BUCKET_NAME}/")[-1] # Extract S3 key
                        
                        s3_client.download_file(settings.AWS_STORAGE_BUCKET_NAME, s3_key, temp_audio_file_path)
                        audio_clip = AudioFileClip(temp_audio_file_path)
                    else: # Assume it's a direct URL or local path
                        audio_clip = AudioFileClip(audio_url)
                    
                    # Loop audio if video is longer than audio
                    if audio_clip.duration < final_video_clip.duration:
                        audio_clip = audio_clip.fx(fadein, 1).fx(fadeout, 1) # Gentle fades
                        audio_clip = audio_clip.set_duration(final_video_clip.duration).set_loop(True)
                    else: # Cut audio if audio is longer than video
                        audio_clip = audio_clip.subclip(0, final_video_clip.duration)
                        audio_clip = audio_clip.fx(fadein, 1).fx(fadeout, 1) # Gentle fades
                    
                    final_video_clip = final_video_clip.set_audio(audio_clip)

                except Exception as e:
                    logger.error(f"Error adding audio: {e}")
                    self.update_state(state='PROGRESS', meta={'progress': 70, 'message': 'Audio error, continuing without audio...'})
            
            self.update_state(state='PROGRESS', meta={'progress': 80, 'message': 'Rendering video...'})
            # Define output path in temp directory
            output_video_filename = f'generated_video_{project_id}.mp4'
            output_video_path = os.path.join(tmpdir, output_video_filename)

            # Write the final video file
            final_video_clip.write_videofile(
                output_video_path,
                codec="libx264",
                audio_codec="aac",
                fps=24, # You can make this configurable
                preset="medium",
                threads=os.cpu_count()
            )

            self.update_state(state='PROGRESS', meta={'progress': 90, 'message': 'Uploading to S3...'})
            # Upload the generated video to S3
            generated_video_s3_key = f'videocraft_videos/{output_video_filename}'
            with open(output_video_path, 'rb') as f:
                # Using the default storage for the model field automatically handles the location
                generated_video_obj, created = GeneratedVideo.objects.update_or_create(
                    project=project,
                    defaults={
                        'video_file': ContentFile(f.read(), name=output_video_filename),
                        'audio_file_url': audio_url,
                        'status': 'completed'
                    }
                )
            
            # The URL will be constructed by the storage backend
            video_url = generated_video_obj.video_file.url

            self.update_state(state='SUCCESS', meta={'progress': 100, 'message': 'Video generated and uploaded successfully!'})
            return video_url

    except VideoCraftProject.DoesNotExist:
        self.update_state(state='FAILURE', meta={'message': 'Project not found.'})
        logger.error(f"generate_video_task: Project {project_id} not found.")
        return 'Project not found.'
    except Exception as e:
        logger.error(f"Error during video generation task for project {project_id}: {e}", exc_info=True)
        self.update_state(state='FAILURE', meta={'message': str(e)})
        return str(e)
