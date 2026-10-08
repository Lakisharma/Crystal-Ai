from django.db.models import QuerySet


def unread_notifications(request):
    """
    Context processor adding unread notifications count to all template contexts.
    Uses efficient indexed query for authenticated users; returns 0 for anonymous.
    """
    if hasattr(request, 'user') and request.user.is_authenticated:
        count = request.user.notifications.filter(is_read=False).count()
        return {'unread_notifications_count': count}
    return {'unread_notifications_count': 0}
