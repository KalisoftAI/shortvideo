"""
WSGI config for shorts_project project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
"""


from django.core.wsgi import get_wsgi_application

import os
import sys
print("DEBUG: DJANGO_SETTINGS_MODULE is:", os.environ.get('DJANGO_SETTINGS_MODULE'), file=sys.stderr)
print("DEBUG: Python path:", sys.path, file=sys.stderr)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shorts_project.settings')

application = get_wsgi_application()
