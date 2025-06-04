# shorts_app/models.py
import uuid
from django.db import models
from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage # Keep this for video files if still on S3

# Custom storage classes. Remove 'location' from these classes.
# The S3 path will be determined by what's stored in the FileField directly in views.py.
class YoutubeVideoStorage(S3Boto3Storage):
    file_overwrite = False

class ShortsStorage(S3Boto3Storage):
    file_overwrite = False


class DownloadedVideo(models.Model):
    video_id = models.CharField(max_length=20, unique=True, primary_key=True)
    title = models.CharField(max_length=255)
    duration = models.IntegerField()
    # Use FileField with custom storage for S3 for video files
    file_path = models.FileField(storage=YoutubeVideoStorage())
    # CHANGE START: Remove custom storage for thumbnail_path
    thumbnail_path = models.FileField(null=True, blank=True) # This will use default local storage
    # CHANGE END
    suggestions = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class GeneratedShort(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent_video = models.ForeignKey(DownloadedVideo, on_delete=models.CASCADE, related_name='shorts')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    tags = models.JSONField(default=list)
    # Use FileField with custom storage for S3 for short videos
    short_path = models.FileField(storage=ShortsStorage())
    thumbnail_path = models.FileField(null=True, blank=True) # Also change this for shorts thumbnails if you want them local
    status = models.CharField(max_length=50, default='pending') # e.g., 'pending', 'processing', 'completed', 'failed'
    progress = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title