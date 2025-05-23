from django.urls import path
from . import views

app_name = 'shorts_app'

urlpatterns = [
    path('', views.index, name='index'),
    path('start_download_job/', views.start_download_job, name='start_download_job'),
    path('start_generate_job/', views.start_generate_job, name='start_generate_job'),
    path('check_job_status/', views.check_job_status, name='check_job_status'),
    path('get_quota_status/', views.get_quota_status, name='get_quota_status'),
    path('delete_video/<str:video_id_str>/', views.delete_video, name='delete_video'),
]