from django.urls import path
from . import views

app_name = 'shorts_app'

urlpatterns = [
    # Main page
    path('', views.index, name='index'),

    # New URLs for the job-based system with progress bars
    path('start_download_job/', views.start_download_job, name='start_download_job'),
    path('start_generate_job/', views.start_generate_job, name='start_generate_job'),
    path('check_job_status/', views.check_job_status, name='check_job_status'),

    # URL for checking YouTube API quota
    path('get_quota_status/', views.get_quota_status, name='get_quota_status'),
]