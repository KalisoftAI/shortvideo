# shorts_app/urls.py

# shorts_app/urls.py

from django.urls import path
from . import views

app_name = 'shorts_app'

urlpatterns = [
    path('', views.index, name='index'),
    path('process/', views.process_video, name='process_video'),
    path('generate_short/', views.generate_short, name='generate_short'),
    path('download_short/<str:filename>/', views.download_short, name='download_short'),
    path('delete_video/<str:video_id>/', views.delete_video, name='delete_video'),
    path('check_progress/<str:task_id>/', views.check_progress, name='check_progress'),

    # --- ADD THIS LINE FOR THE YOUTUBE UPLOAD URL ---
    path('upload_youtube/', views.upload_to_youtube_view, name='upload_youtube'),
]