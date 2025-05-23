

from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'), # Homepage
    path('process_video/', views.process_video, name='process_video'), # Video processing ke liye

    # Path for generating the short clip
    path('generate_short/', views.generate_short, name='generate_short'),
    path('download_short/<str:filename>/', views.download_short, name='download_short'), # Short download ke liye
]