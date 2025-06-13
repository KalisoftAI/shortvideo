# videocraft_app/urls.py

from django.urls import path
from . import views

app_name = 'videocraft_app'

urlpatterns = [
    path('', views.videocraft_index, name='index'),
    path('create_project/', views.create_project, name='create_project'),
    path('project/<uuid:project_id>/', views.project_detail, name='project_detail'),
    path('project/<uuid:project_id>/upload_image/', views.upload_image, name='upload_image'),
    path('image/<uuid:image_id>/delete/', views.delete_image, name='delete_image'),
    path('image/<uuid:image_id>/update/', views.update_image_details, name='update_image_details'),
    path('project/<uuid:project_id>/generate_video/', views.generate_video, name='generate_video'),
    # Changed from task_id to project_id for progress check
    path('check_video_progress/<uuid:project_id>/', views.check_video_generation_progress, name='check_video_generation_progress'),
    path('image/<uuid:image_id>/generate_text_overlay/', views.generate_text_overlay, name='generate_text_overlay'),
    path('project/<uuid:project_id>/delete/', views.delete_project, name='delete_project'),
]
