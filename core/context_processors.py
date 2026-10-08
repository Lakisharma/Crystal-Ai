from datetime import datetime


def site_info(request):
    """Provides global site-wide variables to templates."""
    return {
        'SITE_NAME': 'Crystal AI',
        'SITE_TAGLINE': 'Smart Live Learning & AI Classroom',
        'CURRENT_YEAR': datetime.now().year,
    }
