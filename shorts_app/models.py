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

from django.db import models

class DownloadedVideo(models.Model):
    video_id = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=255)
    duration = models.IntegerField()
    file_path = models.CharField(max_length=255)
    thumbnail_path = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    suggestions = models.JSONField(null=True, blank=True)

    def __str__(self):
        return self.title