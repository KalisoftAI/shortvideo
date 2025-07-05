# videocraft_app/models.py
import uuid
from django.db import models
from storages.backends.s3boto3 import S3Boto3Storage


# Custom storage classes for videocraft_app
# These will save files into specific folders in your S3 bucket.
class VideoCraftImageStorage(S3Boto3Storage):
    """Custom storage for project images, placing them in a 'videocraft_images/' folder."""
    location = 'videocraft_images'
    file_overwrite = False


class VideoCraftVideoStorage(S3Boto3Storage):
    """Custom storage for generated videos, placing them in a 'videocraft_videos/' folder."""
    location = 'videocraft_videos'
    file_overwrite = False


class VideoCraftAudioStorage(S3Boto3Storage):
    """Custom storage for uploaded audio, placing them in a 'videocraft_audio/' folder."""
    location = 'videocraft_audio'
    file_overwrite = False


class VideoCraftProject(models.Model):
    """
    Represents a user's video project.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class ProjectImage(models.Model):
    """
    Stores individual images uploaded for a video project, using S3 storage.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(VideoCraftProject, on_delete=models.CASCADE, related_name='images')
    image_file = models.ImageField(storage=VideoCraftImageStorage())
    order = models.IntegerField(default=0)
    text_overlay = models.TextField(blank=True, null=True)
    duration = models.FloatField(default=3.0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Image for {self.project.title} (Order: {self.order})"


class GeneratedVideo(models.Model):
    """
    Stores the final generated video and its associated audio file, using S3 storage.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField(VideoCraftProject, on_delete=models.CASCADE, related_name='generated_video')

    # The final rendered video file, stored in the 'videocraft_videos' S3 folder.
    video_file = models.FileField(storage=VideoCraftVideoStorage(), blank=True, null=True)

    # The user-uploaded audio file, stored in the 'videocraft_audio' S3 folder.
    audio_file = models.FileField(storage=VideoCraftAudioStorage(), blank=True, null=True)

    # Used if the user provides a link to an audio file instead of uploading one.
    audio_file_url = models.URLField(max_length=500, blank=True, null=True)

    status = models.CharField(max_length=20, default='pending')
    status_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Generated video for {self.project.title}"
