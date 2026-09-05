from django.contrib import admin
from .models import DownloadedVideo, GeneratedShort


@admin.register(DownloadedVideo)
class DownloadedVideoAdmin(admin.ModelAdmin):
    list_display = ('video_id', 'title', 'user', 'duration', 'created_at')
    search_fields = ('video_id', 'title', 'user__username')
    list_filter = ('created_at',)
    readonly_fields = ('created_at',)
    raw_id_fields = ('user',)


@admin.register(GeneratedShort)
class GeneratedShortAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'user', 'parent_video', 'start_time', 'end_time', 'created_at')
    search_fields = ('title', 'user__username', 'parent_video__title')
    list_filter = ('created_at',)
    readonly_fields = ('created_at',)
    raw_id_fields = ('user', 'parent_video')
