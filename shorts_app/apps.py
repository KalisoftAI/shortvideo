# In shorts_app/apps.py
from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)

class ShortsAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shorts_app'