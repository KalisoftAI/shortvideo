# videocraft_app/video_processing_utils.py
import os
import tempfile
import logging

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction

import boto3
from botocore.exceptions import ClientError

from moviepy.editor import ImageSequenceClip, AudioFileClip, CompositeAudioClip, TextClip, CompositeVideoClip, ColorClip, concatenate_videoclips, concatenate_audioclips
from moviepy.video.fx.all import fadein, fadeout, crop, resize
from moviepy.video.tools.cuts import find_video_period

from .models import VideoCraftProject, ProjectImage, GeneratedVideo, VideoCraftImageStorage

logger = logging.getLogger(__name__)

# Initialize S3 client
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
    logger.error(f"Error initializing S3 client in videocraft_app video_processing_utils: {e}")

def apply_transition(clip1, clip2, transition_type, duration=0.5):
    """
    Applies a transition effect between two MoviePy clips.
    """
    if transition_type == 'fade':
        return CompositeVideoClip([
            clip1.set_duration(clip1.duration - duration),
            clip2.set_start(clip1.duration - duration).set_duration(clip2.duration).fx(fadein, duration)
        ])
    elif transition_type == 'crossfade':
        return concatenate_videoclips([clip1, clip2], transition=fadein(duration))
    elif transition_type == 'wipe_right':
        logger.warning(f"Transition '{transition_type}' not fully implemented. Using fade.")
        return CompositeVideoClip([
            clip1.set_duration(clip1.duration - duration),
            clip2.set_start(clip1.duration - duration).set_duration(clip2.duration).fx(fadein, duration)
        ])
    elif transition_type == 'slide_left':
        logger.warning(f"Transition '{transition_type}' not fully implemented. Using fade.")
        return CompositeVideoClip([
            clip1.set_duration(clip1.duration - duration),
            clip2.set_start(clip1.duration - duration).set_duration(clip2.duration).fx(fadein, duration)
        ])
    else:
        return concatenate_videoclips([clip1, clip2], method="compose")


def generate_video_core(project_id, audio_file_s3_key=None, external_audio_url=None, transition_type='fade', text_color='white', font_size=50, font='sans'):
    """
    Core function to generate a video from images for a given project.
    This function is run in a separate thread, not as a Celery task.
    """
    try:
        project = VideoCraftProject.objects.get(id=project_id)
        generated_video_obj, created = GeneratedVideo.objects.update_or_create(
            project=project,
            defaults={'status': 'processing', 'status_message': 'Starting video generation...'}
        )
        logger.info(f"Video generation started for project {project_id}.")

        images = project.images.all().order_by('order')

        if not images.exists():
            generated_video_obj.status = 'failed'
            generated_video_obj.status_message = 'No images found for this project.'
            generated_video_obj.save()
            logger.error(f"Project {project_id}: No images found.")
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            clips = []
            max_clip_width = 0
            max_clip_height = 0

            for i, img_obj in enumerate(images):
                generated_video_obj.status_message = f'Downloading image {i+1}/{len(images)}...'
                generated_video_obj.save()
                temp_image_file_path = os.path.join(tmpdir, f'image_{img_obj.order}_{img_obj.id}.png')
                
                s3_image_key = img_obj.image_file.name
                s3_image_key = s3_image_key.replace('\\', '/')
                if not s3_image_key.startswith(VideoCraftImageStorage.location + '/'):
                    s3_image_key = f"{VideoCraftImageStorage.location}/{s3_image_key}"

                logger.debug(f"Attempting to download S3 object: Bucket='{settings.AWS_STORAGE_BUCKET_NAME}', Key='{s3_image_key}'")

                try:
                    s3_client.download_file(settings.AWS_STORAGE_BUCKET_NAME, s3_image_key, temp_image_file_path)
                    
                    # Ensure img_obj.duration is explicitly converted to float with robust error handling
                    try:
                        image_duration = float(img_obj.duration)
                    except (ValueError, TypeError) as e:
                        # Log a more specific error if duration cannot be converted
                        error_message = f"Invalid duration value for image {img_obj.id} ('{img_obj.duration}'). Expected a number, got {type(img_obj.duration).__name__}. Original error: {e}"
                        logger.error(error_message)
                        generated_video_obj.status = 'failed'
                        generated_video_obj.status_message = f'Error processing image duration: {error_message}'
                        generated_video_obj.save()
                        return # Exit the function as duration is critical

                    img_clip = ImageSequenceClip([temp_image_file_path], durations=[image_duration])
                    
                    # Validate clip dimensions immediately after creation
                    if not isinstance(img_clip.w, (int, float)) or not isinstance(img_clip.h, (int, float)):
                        error_message = f"Image dimensions (width/height) are not valid numbers for image {img_obj.id}. w={img_clip.w}, h={img_clip.h}. This may indicate a corrupted image file or MoviePy issue."
                        logger.error(error_message)
                        generated_video_obj.status = 'failed'
                        generated_video_obj.status_message = f'Error processing image dimensions: {error_message}'
                        generated_video_obj.save()
                        return

                    logger.debug(f"Image clip dimensions for {img_obj.id}: w={img_clip.w}, h={img_clip.h}, duration={img_clip.duration}")
                    
                    # Find max dimensions for consistent video size
                    if img_clip.w > max_clip_width:
                        max_clip_width = img_clip.w
                    if img_clip.h > max_clip_height:
                        max_clip_height = img_clip.h
                    
                    # Add text overlay if present AND text overlays are enabled in settings
                    if img_obj.text_overlay and settings.ENABLE_TEXT_OVERLAYS:
                        try:
                            text_clip = TextClip(
                                img_obj.text_overlay,
                                fontsize=int(font_size), # Ensure fontsize is an integer
                                color=text_color,
                                font=font,
                                stroke_color='black',
                                stroke_width=1,
                                method='caption',
                                size=(int(img_clip.w * 0.8), None) # Ensure width is an integer for size calculation
                            ).set_position(('center', 'bottom')).set_duration(image_duration)
                            
                            img_clip = CompositeVideoClip([img_clip, text_clip])
                        except Exception as text_clip_error:
                            logger.warning(f"Failed to create TextClip for image {img_obj.id} due to: {text_clip_error}. Skipping text overlay. This often indicates ImageMagick is not installed or configured correctly.")
                            # Continue without text overlay for this image. img_clip remains the original image clip.
                    elif img_obj.text_overlay and not settings.ENABLE_TEXT_OVERLAYS:
                        logger.info(f"Text overlay for image {img_obj.id} skipped as ENABLE_TEXT_OVERLAYS is False in settings.")

                    clips.append(img_clip)

                except ClientError as e:
                    generated_video_obj.status = 'failed'
                    generated_video_obj.status_message = f'Failed to download image: {e}'
                    generated_video_obj.save()
                    logger.error(f"Failed to download image {s3_image_key} from S3: {e}", exc_info=True)
                    return
                except Exception as e:
                    generated_video_obj.status = 'failed'
                    generated_video_obj.status_message = f'Error processing image {img_obj.id}: {e}'
                    generated_video_obj.save()
                    logger.error(f"Error processing image {img_obj.id}: {e}", exc_info=True) # Log full traceback for unexpected errors
                    return

            if not clips:
                generated_video_obj.status = 'failed'
                generated_video_obj.status_message = 'No valid clips to create video.'
                generated_video_obj.save()
                logger.error(f"Project {project_id}: No valid clips to create video.")
                return

            # --- NEW ADDITION: Debugging and Validation for max_clip_width/height ---
            logger.debug(f"Calculated max_clip_width: {max_clip_width}, max_clip_height: {max_clip_height} before resizing clips.")

            # Ensure max dimensions are valid before resizing
            if max_clip_width <= 0 or max_clip_height <= 0:
                error_message = f"Calculated max video dimensions are invalid: width={max_clip_width}, height={max_clip_height}. This typically means that even after processing, no image provided valid positive dimensions."
                logger.error(f"Project {project_id}: {error_message}")
                generated_video_obj.status = 'failed'
                generated_video_obj.status_message = f'Error calculating video dimensions: {error_message}'
                generated_video_obj.save()
                return
            # --- END NEW ADDITION ---

            processed_clips = []
            for clip in clips:
                # Explicitly cast to int just to be absolutely certain for MoviePy's resize
                processed_clips.append(resize(clip, newsize=(int(max_clip_width), int(max_clip_height))))

            generated_video_obj.status_message = 'Applying transitions...'
            generated_video_obj.save()

            if len(processed_clips) > 1 and transition_type != 'none':
                transition_duration = 0.5
                final_video_clip = processed_clips[0]
                for i in range(1, len(processed_clips)):
                    if transition_type == 'fade':
                        final_video_clip = concatenate_videoclips([
                            final_video_clip.fx(fadeout, transition_duration),
                            processed_clips[i].fx(fadein, transition_duration)
                        ], method="compose")
                    elif transition_type == 'wipe_right':
                        logger.warning(f"Transition '{transition_type}' not fully implemented. Using fade.")
                        final_video_clip = concatenate_videoclips([
                            final_video_clip.fx(fadeout, transition_duration),
                            processed_clips[i].fx(fadein, transition_duration)
                        ], method="compose")
                    elif transition_type == 'slide_left':
                        logger.warning(f"Transition '{transition_type}' not fully implemented. Using fade.")
                        final_video_clip = concatenate_videoclips([
                            final_video_clip.fx(fadeout, transition_duration),
                            processed_clips[i].fx(fadein, transition_duration)
                        ], method="compose")
                    else:
                        final_video_clip = concatenate_videoclips([final_video_clip, processed_clips[i]], method="compose")
            else:
                final_video_clip = concatenate_videoclips(processed_clips, method="compose")

            generated_video_obj.status_message = 'Adding audio...'
            generated_video_obj.save()
            audio_source_url = None
            if audio_file_s3_key:
                temp_audio_file_path = os.path.join(tmpdir, 'background_audio.mp3')
                logger.debug(f"Attempting to download S3 audio object: Bucket='{settings.AWS_STORAGE_BUCKET_NAME}', Key='{audio_file_s3_key}'")
                try:
                    s3_client.download_file(settings.AWS_STORAGE_BUCKET_NAME, audio_file_s3_key, temp_audio_file_path)
                    audio_clip = AudioFileClip(temp_audio_file_path)
                    audio_source_url = generated_video_obj.audio_file.url
                except ClientError as e:
                    logger.error(f"Failed to download audio from S3 ({audio_file_s3_key}): {e}", exc_info=True)
                    generated_video_obj.status_message = 'Audio download failed, continuing without audio.'
                    generated_video_obj.save()
                    audio_clip = None
                except Exception as e:
                    logger.error(f"Error loading uploaded audio: {e}", exc_info=True)
                    generated_video_obj.status_message = 'Error loading uploaded audio, continuing without audio.'
                    generated_video_obj.save()
                    audio_clip = None
            elif external_audio_url:
                try:
                    audio_clip = AudioFileClip(external_audio_url)
                    audio_source_url = external_audio_url
                except Exception as e:
                    logger.error(f"Error adding external audio: {e}", exc_info=True)
                    generated_video_obj.status_message = 'External audio error, continuing without audio.'
                    generated_video_obj.save()
                    audio_clip = None
            else:
                audio_clip = None
            
            if audio_clip:
                if audio_clip.duration < final_video_clip.duration:
                    num_loops = int(final_video_clip.duration / audio_clip.duration) + 1
                    looped_audio_clips = [audio_clip] * num_loops
                    combined_looped_audio = concatenate_audioclips(looped_audio_clips)
                    audio_clip = combined_looped_audio.set_duration(final_video_clip.duration).fx(fadein, 1).fx(fadeout, 1)
                else:
                    audio_clip = audio_clip.subclip(0, final_video_clip.duration).fx(fadein, 1).fx(fadeout, 1)
                
                final_video_clip = final_video_clip.set_audio(audio_clip)
            
            generated_video_obj.status_message = 'Rendering video...'
            generated_video_obj.save()
            output_video_filename = f'generated_video_{project_id}.mp4'
            output_video_path = os.path.join(tmpdir, output_video_filename)

            final_video_clip.write_videofile(
                output_video_path,
                codec="libx264",
                audio_codec="aac",
                fps=24,
                preset="medium",
                threads=os.cpu_count(),
                logger=None,  # Use the logger to capture MoviePy logs
            )

            generated_video_obj.status_message = 'Uploading to S3...'
            generated_video_obj.save()

            with open(output_video_path, 'rb') as f:
                with transaction.atomic():
                    generated_video_obj = GeneratedVideo.objects.get(project=project)
                    # OLD (Remove this line): generated_video_obj.video_file = ContentFile(f.read(), name=output_video_filename)
                    # NEW (Add this line instead):
                    generated_video_obj.video_file.save(output_video_filename, f, save=False) # FileField ke .save() method ka seedha use karein
                    generated_video_obj.audio_file_url = audio_source_url
                    generated_video_obj.status = 'completed'
                    generated_video_obj.status_message = 'Video generated and uploaded successfully!'
                    generated_video_obj.save()
            
            logger.info(f"Video generation completed for project {project_id}. Video URL: {generated_video_obj.video_file.url}")

    except VideoCraftProject.DoesNotExist:
        logger.error(f"generate_video_core: Project {project_id} not found.")
        try:
            generated_video_obj = GeneratedVideo.objects.get(project_id=project_id)
            generated_video_obj.status = 'failed'
            generated_video_obj.status_message = 'Project not found.'
            generated_video_obj.save()
        except GeneratedVideo.DoesNotExist:
            pass
    except Exception as e:
        logger.error(f"Error during video generation for project {project_id}: {e}", exc_info=True)
        try:
            if 'generated_video_obj' in locals() and generated_video_obj:
                generated_video_obj.status = 'failed'
                generated_video_obj.status_message = f'Video generation failed: {e}'
                generated_video_obj.save()
        except Exception as update_error:
            logger.error(f"Error updating generated_video_obj status: {update_error}", exc_info=True)
    finally:
        pass