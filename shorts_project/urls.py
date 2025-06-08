# shorts_project/urls.py
from django.contrib import admin
from django.urls import path, include # Import include
from django.conf import settings # Import settings
from django.conf.urls.static import static # Import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('shorts_app.urls')), # Your existing app
    path('videocraft/', include('videocraft_app.urls')), # NEW: Include your new app's URLs
]

# Serve media files in development (IMPORTANT: do NOT use in production with S3)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
