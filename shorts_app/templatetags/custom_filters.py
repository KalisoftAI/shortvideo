# shorts_app/templatetags/custom_filters.py

from django import template
from django.conf import settings

register = template.Library()

@register.filter
def cut(value, arg):
    """
    Removes all values of arg from the given string.
    This filter is modified to correctly handle URL parts for S3.
    """
    if isinstance(value, str) and isinstance(arg, str):
        # Ensure we only cut the prefix if it actually starts with it
        if value.startswith(arg):
            return value[len(arg):]
    return value