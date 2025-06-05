# shorts_app/models.py
# In shorts_app/models.py
import uuid
from django.db import models

class DownloadedVideo(models.Model):
    video_id = models.CharField(max_length=20, unique=True, primary_key=True)
    title = models.CharField(max_length=255)
    duration = models.IntegerField()
    file_path = models.CharField(max_length=512)
    thumbnail_path = models.CharField(max_length=512, null=True, blank=True)
    suggestions = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    def get_s3_video_url(self):
        """Returns the full S3 URL for the video."""
        if self.file_path and settings.AWS_S3_ENABLED:
            return f"https://{settings.AWS_S3_CUSTOM_DOMAIN}/{self.file_path}"
        return "" # Return empty string or handle local path if S3 not enabled

    def get_s3_thumbnail_url(self):
        """Returns the full S3 URL for the video thumbnail."""
        if self.thumbnail_path and settings.AWS_S3_ENABLED:
            return f"https://{settings.AWS_S3_CUSTOM_DOMAIN}/{self.thumbnail_path}"
        return "" # Return empty string or handle local path if S3 not enabled

class GeneratedShort(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent_video = models.ForeignKey(DownloadedVideo, on_delete=models.CASCADE, related_name='shorts')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    tags = models.JSONField(default=list)
    short_path = models.CharField(max_length=512)
    thumbnail_path = models.CharField(max_length=512)
    start_time = models.CharField(max_length=12)
    end_time = models.CharField(max_length=12)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Short: {self.title}"

