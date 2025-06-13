# videocraft_app/models.py
import uuid
from django.db import models
from storages.backends.s3boto3 import S3Boto3Storage

# Custom storage classes for videocraft_app
# These will save files into 'videocraft_images/' and 'videocraft_videos/' folders in your S3 bucket
class VideoCraftImageStorage(S3Boto3Storage):
    location = 'videocraft_images' # This defines the folder in S3
    file_overwrite = False

class VideoCraftVideoStorage(S3Boto3Storage):
    location = 'videocraft_videos' # This defines the folder in S3
    file_overwrite = False
    # Removed default_acl = 'public-read' - permissions handled by bucket policy

class VideoCraftAudioStorage(S3Boto3Storage):
    location = 'videocraft_audio' # New storage for audio files
    file_overwrite = False
    # Removed default_acl = 'public-read' - permissions handled by bucket policy

class VideoCraftProject(models.Model):
    """
    Represents a user's video project.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # You might want to add a ForeignKey to User model if you implement authentication
    # user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='videocraft_projects')

    def __str__(self):
        return self.title

class ProjectImage(models.Model):
    """
    Stores individual images uploaded for a video project.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(VideoCraftProject, on_delete=models.CASCADE, related_name='images')
    image_file = models.ImageField(storage=VideoCraftImageStorage())
    order = models.IntegerField(default=0) # To maintain the order of images in the video
    text_overlay = models.TextField(blank=True, null=True) # Text to overlay on this image
    duration = models.FloatField(default=3.0) # Duration of this image in seconds in the video

    class Meta:
        ordering = ['order'] # Order images by their 'order' field

    def __str__(self):
        return f"Image for {self.project.title} (Order: {self.order})"

class GeneratedVideo(models.Model):
    """
    Stores the final generated video for a project.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField(VideoCraftProject, on_delete=models.CASCADE, related_name='generated_video')
    video_file = models.FileField(storage=VideoCraftVideoStorage())
    # New field to store uploaded audio file, if any
    audio_file = models.FileField(storage=VideoCraftAudioStorage(), blank=True, null=True) 
    audio_file_url = models.URLField(max_length=500, blank=True, null=True) # URL to external audio or S3 audio (if not uploaded locally)
    status = models.CharField(max_length=20, default='pending') # e.g., 'pending', 'processing', 'completed', 'failed'
    status_message = models.TextField(blank=True, null=True) # More detailed status/error message
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Generated video for {self.project.title}"