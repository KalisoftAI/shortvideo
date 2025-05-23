from django.db import models
from django.utils import timezone

class DownloadedVideo(models.Model):
    video_id = models.CharField(max_length=20, primary_key=True, unique=True)
    title = models.CharField(max_length=255)
    duration = models.IntegerField()
    file_path = models.CharField(max_length=512)
    thumbnail_url = models.URLField(max_length=1024, blank=True, null=True)
    # --- ADD THIS LINE TO STORE SUGGESTIONS ---
    suggestions = models.JSONField(null=True, blank=True)
    downloaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.video_id})"

# The YouTubeUpload model remains unchanged
class YouTubeUpload(models.Model):
    video_title = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.video_title} uploaded at {self.uploaded_at}"