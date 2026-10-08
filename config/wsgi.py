"""
WSGI config for LiveClass project.

It exposes the WSGI callable as a module-level variable named ``application``.
"""

import logging
import os
from django.core.wsgi import get_wsgi_application

logger = logging.getLogger(__name__)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()
