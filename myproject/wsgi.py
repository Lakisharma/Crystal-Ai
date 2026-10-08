"""
WSGI compatibility module for Render deployment referencing 'myproject.wsgi:application'.

Exposes the LiveClass WSGI application callable directly from config.wsgi.
"""

from config.wsgi import application

__all__ = ['application']
