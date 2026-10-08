from datetime import datetime


def site_info(request):
    """Provides global site-wide variables to templates."""
    return {
        'SITE_NAME': 'TeachLive',
        'SITE_TAGLINE': 'Teach Live. Learn Live.',
        'CURRENT_YEAR': datetime.now().year,
    }
