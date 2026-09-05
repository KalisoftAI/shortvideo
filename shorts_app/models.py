import uuid
from django.db import models
from django.conf import settings


class DownloadedVideo(models.Model):
    video_id = models.CharField(max_length=20, primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=255)
    duration = models.IntegerField()
    file_path = models.FileField()
    thumbnail_path = models.FileField(null=True, blank=True)
    suggestions = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class GeneratedShort(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent_video = models.ForeignKey(DownloadedVideo, on_delete=models.CASCADE, related_name='shorts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    tags = models.JSONField(default=list)
    short_path = models.FileField()
    thumbnail_path = models.FileField(null=True, blank=True)
    start_time = models.CharField(max_length=12)
    end_time = models.CharField(max_length=12)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
