from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from .models import Notification


class NotificationListView(LoginRequiredMixin, View):
    """
    User Notification Center at /notifications/.
    Displays logged-in user's notifications (newest first), highlights unread,
    supports filtering by read status or category, and paginates results.
    """
    def get(self, request):
        user = request.user
        filter_status = request.GET.get('status', 'all').strip().lower()
        filter_type = request.GET.get('type', '').strip().upper()

        qs = user.notifications.select_related('related_live_class').order_by('-created_at')

        if filter_status == 'unread':
            qs = qs.filter(is_read=False)
        elif filter_status == 'read':
            qs = qs.filter(is_read=True)

        if filter_type and filter_type in Notification.Type.values:
            qs = qs.filter(notification_type=filter_type)

        total_unread = user.notifications.filter(is_read=False).count()
        total_count = user.notifications.count()

        paginator = Paginator(qs, 20)
        page_num = request.GET.get('page', 1)
        try:
            notifications_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            notifications_page = paginator.page(1)

        return render(request, 'notifications/list.html', {
            'notifications': notifications_page,
            'total_unread': total_unread,
            'total_count': total_count,
            'selected_status': filter_status,
            'selected_type': filter_type,
            'type_choices': Notification.Type.choices,
            'page_title': 'Notifications - TeachLive',
        })


class NotificationMarkReadView(LoginRequiredMixin, View):
    """
    POST-only action to mark a specific notification as read.
    Enforces strict ownership: users can only mark their own notifications.
    """
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
        notification.mark_as_read()

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            unread_count = request.user.notifications.filter(is_read=False).count()
            return JsonResponse({'status': 'ok', 'unread_count': unread_count})

        messages.success(request, "Notification marked as read.")
        next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/notifications/'
        return redirect(next_url)

    def get(self, request, pk):
        # Strict enforcement: GET method not allowed for state changes
        return HttpResponseNotAllowed(['POST'])


class NotificationMarkAllReadView(LoginRequiredMixin, View):
    """
    POST-only action to mark all notifications for the current user as read.
    """
    def post(self, request):
        now = timezone.now()
        updated_count = request.user.notifications.filter(is_read=False).update(
            is_read=True,
            read_at=now
        )

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'status': 'ok', 'marked_count': updated_count, 'unread_count': 0})

        if updated_count > 0:
            messages.success(request, f"Marked {updated_count} notification(s) as read.")
        else:
            messages.info(request, "All notifications are already marked as read.")

        next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/notifications/'
        return redirect(next_url)

    def get(self, request):
        return HttpResponseNotAllowed(['POST'])
