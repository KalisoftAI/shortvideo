# from django.db import models
#
# # Create your models here.
# # shorts_app/models.py
#
# from django.db import models
#
#
# class DownloadedVideo(models.Model):
#     # The unique 11-character ID from the YouTube URL (e.g., dQw4w9WgXcQ)
#     video_id = models.CharField(max_length=20, primary_key=True, unique=True)
#
#     # The title of the video
#     title = models.CharField(max_length=255)
#
#     # The duration in total seconds
#     duration = models.IntegerField()
#
#     # The relative path to the video file in your MEDIA_ROOT
#     # e.g., /media/videos/dQw4w9WgXcQ.mp4
#     file_path = models.CharField(max_length=512)
#
#     # The date and time the video was downloaded
#     downloaded_at = models.DateTimeField(auto_now_add=True)
#
#     def __str__(self):
#         return f"{self.title} ({self.video_id})"

# shorts_app/models.py

# shorts_app/models.py
# In shorts_app/models.py
# shorts_app/models.py
# shorts_app/models.py
import uuid
from django.db import models
from django.conf import settings

class DownloadedVideo(models.Model):
    video_id = models.CharField(max_length=20, unique=True, primary_key=True)
    title = models.CharField(max_length=255)
    duration = models.IntegerField()
    # These will now store S3 object keys (e.g., 'youtube_videos/video_id.mp4')
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
    tags = models.JSONField(null=True, blank=True)
    start_time = models.IntegerField() # In seconds
    end_time = models.IntegerField()   # In seconds
    short_path = models.CharField(max_length=512) # Will store S3 key (e.g., 'generated_shorts/uuid.mp4')
    thumbnail_path = models.CharField(max_length=512, null=True, blank=True) # S3 key (e.g., 'generated_shorts/uuid.webp')
    status = models.CharField(max_length=50, default='pending') # e.g., 'pending', 'processing', 'completed', 'failed'
    progress = models.IntegerField(default=0) # 0-100
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    def get_s3_short_url(self):
        """Returns the full S3 URL for the generated short."""
        if self.short_path and settings.AWS_S3_ENABLED:
            return f"https://{settings.AWS_S3_CUSTOM_DOMAIN}/{self.short_path}"
        return "" # Return empty string or handle local path if S3 not enabled

    def get_s3_thumbnail_url(self):
        """Returns the full S3 URL for the short thumbnail."""
        if self.thumbnail_path and settings.AWS_S3_ENABLED:
            return f"https://{settings.AWS_S3_CUSTOM_DOMAIN}/{self.thumbnail_path}"
        return "" # Return empty string or handle local path if S3 not enabled