# In shorts_app/urls.py

from django.urls import path
from . import views

app_name = 'shorts_app'

urlpatterns = [
    # Landing page
    path('', views.home, name='home'),

    # Marketing pages
    path('pricing/', views.pricing, name='pricing'),

    # Authentication
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),

    # Dashboard (moved from root to avoid collision with landing)
    path('dashboard/', views.index, name='index'),

    # Background task and video processing URLs (kept at root for dashboard JS compatibility)
    path('process_video/', views.process_video, name='process_video'),
    path('check_progress/<str:task_id>/', views.check_progress, name='check_progress'),

    # Short generation and management URLs
    path('generate_short/', views.generate_short, name='generate_short'),
    path('download_short/<uuid:short_id>/', views.download_short, name='download_short'),


    # Deletion URLs
    path('delete_video/<str:video_id>/', views.delete_video, name='delete_video'),
    path('delete_short/<uuid:short_id>/', views.delete_short, name='delete_short'),
]