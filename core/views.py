from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.views.generic import TemplateView
from accounts.models import User
from classrooms.models import Classroom
from attendance.models import AttendanceSession, AttendanceRecord


class HomeView(TemplateView):
    template_name = 'home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context['total_classes'] = Classroom.objects.filter(is_active=True).count()
            context['total_teachers'] = User.objects.filter(role=User.Role.TEACHER).count()
            context['total_students'] = User.objects.filter(role=User.Role.STUDENT).count()
        except Exception:
            context['total_classes'] = 0
            context['total_teachers'] = 0
            context['total_students'] = 0
        return context


@login_required
def dashboard_router_view(request):
    """
    Intelligently routes the logged-in user to their respective dashboard:
    Teacher Dashboard vs Student Dashboard vs Admin.
    """
    user = request.user
    if user.is_teacher or user.is_admin_role:
        return redirect('accounts:teacher_dashboard')
    elif user.is_student:
        return redirect('student:dashboard')
    else:
        # Admin or general staff
        return render(request, 'core/dashboard_teacher.html', {
            'classrooms': Classroom.objects.all()[:10],
            'total_students': User.objects.filter(role=User.Role.STUDENT).count(),
            'recent_sessions': AttendanceSession.objects.all()[:5],
            'is_admin_mode': True,
        })


def about_view(request):
    return render(request, 'core/about.html')


def health_check_view(request):
    """
    Lightweight health check endpoint for Render / uptime monitoring.
    Returns HTTP 200 with status 'ok' if application and database are healthy.
    Never exposes internal configurations, credentials, or exception traces.
    """
    from django.db import connection
    from django.http import JsonResponse
    import logging

    health_logger = logging.getLogger(__name__)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()
    except Exception as e:
        health_logger.error("Health probe database failure: %s", e)
        return JsonResponse({"status": "unhealthy", "database": "unavailable"}, status=503)

    return JsonResponse({
        "status": "ok",
        "service": "TeachLive",
    }, status=200)


def custom_400_view(request, exception=None):
    return render(request, 'core/400.html', status=400)


def custom_403_view(request, exception=None):
    return render(request, 'core/403.html', status=403)


def custom_404_view(request, exception=None):
    return render(request, 'core/404.html', status=404)


def custom_500_view(request):
    return render(request, 'core/500.html', status=500)
