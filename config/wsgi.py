"""
WSGI config for LiveClass project.

It exposes the WSGI callable as a module-level variable named ``application``.
"""

import logging
import os
from django.core.wsgi import get_wsgi_application

logger = logging.getLogger(__name__)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Automatically ensure static files are collected if missing in STATIC_ROOT (Render fail-safe)
try:
    from django.conf import settings
    custom_css = settings.STATIC_ROOT / 'css' / 'custom.css'
    if not custom_css.exists():
        from django.core.management import call_command
        logger.info("STATIC_ROOT missing custom.css. Running collectstatic...")
        call_command('collectstatic', interactive=False, verbosity=0)
        logger.info("Static files collected successfully on startup.")
except Exception as e:
    logger.warning("Startup collectstatic failed: %s", e)

application = get_wsgi_application()

# Automatically run database migrations on server boot
# Ensures SQLite / PostgreSQL tables always exist on Render deployments
try:
    from django.core.management import call_command
    call_command('migrate', interactive=False)
    logger.info("Database migrations applied successfully on startup.")
except Exception as e:
    logger.warning("Auto-migration during WSGI startup failed: %s", e)
