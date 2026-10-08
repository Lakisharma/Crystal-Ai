"""
Security headers middleware for TeachLive.
Configures Content-Security-Policy (CSP) and Permissions-Policy in a manner
fully compatible with Bootstrap 5 CDN, Bootstrap Icons, Google Fonts,
Google OAuth, and LiveKit WebRTC / WebSockets.
"""

from django.conf import settings


class SecurityHeadersMiddleware:
    """
    Appends Content-Security-Policy and Permissions-Policy response headers.
    Ensures WebRTC, camera, microphone, and WebSockets function without browser blocks.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Content-Security-Policy
        # Tailored specifically for TeachLive's CDN assets, LiveKit WebSockets, and Google OAuth
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://apis.google.com https://accounts.google.com",
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
            "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net",
            "img-src 'self' data: blob: https:",
            "media-src 'self' blob: https:",
            "connect-src 'self' wss: ws: https: blob:",
            "frame-src 'self' https://accounts.google.com",
            "object-src 'none'",
            "base-uri 'self'",
        ]

        # In production or if enabled, apply CSP
        if getattr(settings, 'CSP_ENABLED', True):
            response.headers.setdefault('Content-Security-Policy', '; '.join(csp_directives))

        # Permissions-Policy: permit camera, microphone, display-capture for live classes
        response.headers.setdefault(
            'Permissions-Policy',
            'camera=(self), microphone=(self), display-capture=(self)'
        )

        return response
