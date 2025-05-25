# shorts_app/urls.py

from django.urls import path
from . import views

app_name = 'shorts_app'

urlpatterns = [
    path('', views.index, name='index'),
    path('process/', views.process_video, name='process_video'),
    path('generate_short/', views.generate_short, name='generate_short'),
    path('download_short/<str:filename>/', views.download_short, name='download_short'),

    # --- NEW URLS ---
    # URL to delete a downloaded video
    path('delete_video/<str:video_id>/', views.delete_video, name='delete_video'),
    # URL for the frontend to poll for progress updates
    path('check_progress/<str:task_id>/', views.check_progress, name='check_progress'),
]