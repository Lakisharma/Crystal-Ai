from datetime import timedelta
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, View
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from classrooms.models import Attendance, ClassEnrollment, ClassParticipant, Classroom, LiveClass
from attendance.models import AttendanceSession
from notifications.models import Notification
from .forms import (
    BootstrapLoginForm,
    StudentRegistrationForm,
    TeacherPasswordChangeForm,
    TeacherRegistrationForm,
    UserProfileForm,
)
from .models import User
from .permissions import TeacherRequiredMixin
from .serializers import UserSerializer
from notifications.email_service import EmailService


class BaseAuthLoginView(LoginView):
    """Base login view using our Bootstrap 5 form."""
    form_class = BootstrapLoginForm
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        if user.is_authenticated:
            if user.is_admin_role or user.is_superuser:
                return reverse_lazy('admin_dashboard:dashboard')
            elif user.is_teacher:
                return reverse_lazy('accounts:teacher_dashboard')
            elif user.is_student:
                return reverse_lazy('student:dashboard')
        return reverse_lazy('core:dashboard')


class AdminLoginView(BaseAuthLoginView):
    """Dedicated login view for TeachLive Administrators."""
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = "Admin Portal Login - TeachLive"
        context['target_role'] = "ADMIN"
        return context

    def form_valid(self, form):
        user = form.get_user()
        if not (user.is_admin_role or user.is_superuser):
            messages.error(
                self.request,
                "Access restricted: Only administrator accounts can sign in here."
            )
            return redirect('accounts:login')
        login(self.request, user)
        messages.success(self.request, f"Welcome to TeachLive Admin, {user.get_full_name() or user.username}!")
        return redirect('admin_dashboard:dashboard')


class UniversalLoginView(BaseAuthLoginView):
    """Universal login endpoint for all roles."""
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = "Sign In - LiveClass"
        context['target_role'] = "ALL"
        return context


class TeacherLoginView(BaseAuthLoginView):
    """Dedicated login view for Teachers enforcing teacher role."""
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = "Teacher Portal Login - LiveClass"
        context['target_role'] = "TEACHER"
        return context

    def form_valid(self, form):
        user = form.get_user()
        if not (user.is_teacher or user.is_admin_role):
            messages.error(
                self.request,
                "Access restricted: This account is registered as a student. Please sign in via the Student Portal."
            )
            return redirect('accounts:student_login')

        login(self.request, user)
        messages.success(
            self.request,
            f"Welcome back, Professor {user.get_full_name() or user.username}!"
        )
        return redirect('accounts:teacher_dashboard')


class StudentLoginView(BaseAuthLoginView):
    """Dedicated login view for Students."""
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = "Student Portal Login - LiveClass"
        context['target_role'] = "STUDENT"
        return context


class TeacherRegisterView(CreateView):
    """Dedicated registration view for Teachers."""
    model = User
    form_class = TeacherRegistrationForm
    template_name = 'accounts/register_teacher.html'
    success_url = reverse_lazy('accounts:teacher_dashboard')

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        try:
            EmailService.send_welcome_teacher_email(user)
        except Exception:
            pass
        messages.success(
            self.request,
            f"Welcome Professor {user.get_full_name() or user.username}! Your instructor account is ready."
        )
        return redirect('accounts:teacher_dashboard')


class StudentRegisterView(CreateView):
    """Dedicated registration view for Students."""
    model = User
    form_class = StudentRegistrationForm
    template_name = 'accounts/register_student.html'
    success_url = reverse_lazy('core:dashboard')

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        try:
            EmailService.send_welcome_student_email(user)
        except Exception:
            pass
        messages.success(
            self.request,
            f"Welcome to LiveClass, {user.get_full_name() or user.username}! You can now join your classrooms."
        )
        return redirect('core:dashboard')


def user_logout_view(request):
    """Logout handler with session termination and user notification."""
    logout(request)
    messages.info(request, "You have been logged out securely.")
    return redirect('core:home')


# -------------------------------------------------------------
# Teacher Specific Dashboard, Profile, and Password Views
# -------------------------------------------------------------

class TeacherDashboardView(TeacherRequiredMixin, View):
    """
    TeachLive Professional Teacher Dashboard 2.0.
    Provides complete overview of:
    - Real database statistics (Today, Upcoming, Live Now, Completed, Total Students, Teaching Hours)
    - Prominent LIVE NOW section with direct classroom join action
    - Today's Classes with status-aware action triggers
    - Upcoming Classes with countdown & date metadata
    - Recently completed classes with attendance counts
    - Current week mini-calendar / schedule strip
    - Notification preview with unread count
    - Instructor profile summary
    """
    def get(self, request):
        teacher = request.user
        now = timezone.now()
        local_now = timezone.localtime(now)
        today_date = local_now.date()

        # Teacher's LiveClasses
        live_classes_qs = teacher.live_classes.all()

        # 1. Statistics
        today_classes_count = live_classes_qs.filter(scheduled_date=today_date).count()
        
        upcoming_classes_qs = live_classes_qs.filter(
            status=LiveClass.Status.SCHEDULED,
            scheduled_date__gt=today_date
        )
        upcoming_classes_count = upcoming_classes_qs.count()

        live_now_classes_qs = live_classes_qs.filter(status=LiveClass.Status.LIVE)
        live_classes_count = live_now_classes_qs.count()

        completed_classes_qs = live_classes_qs.filter(status=LiveClass.Status.COMPLETED)
        completed_classes_count = completed_classes_qs.count()

        # Total distinct students taught or enrolled across all teacher's live classes
        distinct_participant_user_ids = set(
            ClassParticipant.objects.filter(
                live_class__teacher=teacher,
                user__isnull=False
            ).values_list('user_id', flat=True)
        )
        distinct_enrolled_user_ids = set(
            ClassEnrollment.objects.filter(
                live_class__teacher=teacher,
                status=ClassEnrollment.Status.ENROLLED
            ).values_list('student_id', flat=True)
        )
        total_students = len(distinct_participant_user_ids | distinct_enrolled_user_ids)

        # Total teaching hours: sum duration of completed classes
        total_duration_minutes = completed_classes_qs.aggregate(total=Sum('duration'))['total'] or 0
        total_teaching_hours = round(total_duration_minutes / 60.0, 1)

        # 2. Prominent LIVE NOW section
        live_class_now = live_now_classes_qs.order_by('-started_at', '-created_at').first()
        elapsed_minutes = None
        if live_class_now and live_class_now.started_at:
            elapsed_minutes = max(0, int((now - live_class_now.started_at).total_seconds() // 60))

        # 3. Today's Classes
        today_classes = (
            live_classes_qs.filter(scheduled_date=today_date)
            .order_by('scheduled_time')
        )

        # 4. Upcoming Classes (up to 5)
        upcoming_classes = upcoming_classes_qs.order_by('scheduled_date', 'scheduled_time')[:5]

        # 5. Recently Completed Classes (up to 5)
        recent_classes = (
            completed_classes_qs
            .annotate(attendance_count=Count('attendances', distinct=True))
            .order_by('-ended_at', '-scheduled_date')[:5]
        )

        # 6. Weekly Mini-Calendar / Schedule
        start_of_week = today_date - timedelta(days=today_date.weekday())  # Monday
        end_of_week = start_of_week + timedelta(days=6)  # Sunday
        week_classes_qs = live_classes_qs.filter(
            scheduled_date__gte=start_of_week,
            scheduled_date__lte=end_of_week
        ).order_by('scheduled_date', 'scheduled_time')

        week_days = []
        for i in range(7):
            day_date = start_of_week + timedelta(days=i)
            day_classes = [c for c in week_classes_qs if c.scheduled_date == day_date]
            week_days.append({
                'date': day_date,
                'day_name': day_date.strftime('%a'),
                'day_num': day_date.day,
                'is_today': (day_date == today_date),
                'classes': day_classes,
                'classes_count': len(day_classes),
            })

        # 7. Notifications Preview
        latest_notifications = (
            Notification.objects.filter(recipient=teacher)
            .select_related('related_live_class')
            .order_by('-created_at')[:5]
        )
        unread_notifications_count = Notification.objects.filter(
            recipient=teacher,
            is_read=False
        ).count()

        # Traditional Course Classrooms (backward compatibility)
        course_classes_qs = teacher.teaching_classes.all()
        total_course_classes = course_classes_qs.count()
        course_scheduled = course_classes_qs.filter(status='SCHEDULED').count()
        course_live = course_classes_qs.filter(status='LIVE').count()
        course_completed = course_classes_qs.filter(status='COMPLETED').count()

        final_recent_classes = recent_classes
        if not final_recent_classes and course_classes_qs.exists():
            final_recent_classes = course_classes_qs[:5]

        return render(request, 'accounts/teacher_dashboard.html', {
            'teacher': teacher,
            'today_date': today_date,
            'today_classes_count': today_classes_count,
            'upcoming_classes_count': upcoming_classes_count + course_scheduled,
            'live_classes_count': live_classes_count + course_live,
            'completed_classes_count': completed_classes_count + course_completed,
            'total_students': total_students,
            'total_teaching_hours': total_teaching_hours,
            'live_class_now': live_class_now,
            'elapsed_minutes': elapsed_minutes,
            'live_elapsed_minutes': elapsed_minutes,
            'today_classes': today_classes,
            'upcoming_classes': upcoming_classes,
            'recent_classes': final_recent_classes,
            'week_days': week_days,
            'start_of_week': start_of_week,
            'end_of_week': end_of_week,
            'latest_notifications': latest_notifications,
            'unread_notifications_count': unread_notifications_count,
            'total_classes': live_classes_qs.count() + total_course_classes,
            'scheduled_classes': upcoming_classes_count + course_scheduled,
            'live_classes': live_classes_count + course_live,
            'completed_classes': completed_classes_count + course_completed,
            'classrooms': course_classes_qs,
            'page_title': 'Teacher Dashboard 2.0 - TeachLive',
        })


class TeacherProfileView(TeacherRequiredMixin, View):
    """
    Teacher profile viewing and editing.
    Allows instructors to update their personal details, contact info, bio, and avatar.
    """
    def get(self, request):
        form = UserProfileForm(instance=request.user)
        teaching_classes = request.user.teaching_classes.all()
        return render(request, 'accounts/teacher_profile.html', {
            'form': form,
            'teacher': request.user,
            'total_classes': teaching_classes.count(),
            'total_students': sum(c.student_count for c in teaching_classes),
        })

    def post(self, request):
        form = UserProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your teacher profile was updated successfully!")
            return redirect('accounts:teacher_profile')

        teaching_classes = request.user.teaching_classes.all()
        return render(request, 'accounts/teacher_profile.html', {
            'form': form,
            'teacher': request.user,
            'total_classes': teaching_classes.count(),
            'total_students': sum(c.student_count for c in teaching_classes),
        })


class TeacherPasswordChangeView(TeacherRequiredMixin, PasswordChangeView):
    """
    Secure password change for Teachers.
    Validates current password, enforces password strength rules,
    hashes new password using Django hashing, and preserves active session.
    """
    form_class = TeacherPasswordChangeForm
    template_name = 'accounts/change_password.html'
    success_url = reverse_lazy('accounts:teacher_profile')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Your password has been changed successfully!")
        return response


@login_required
def profile_view(request):
    """General user profile router."""
    if request.user.is_teacher or request.user.is_admin_role:
        return redirect('accounts:teacher_profile')

    if request.method == 'POST':
        form = UserProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your profile was updated successfully.")
            return redirect('accounts:profile')
    else:
        form = UserProfileForm(instance=request.user)

    return render(request, 'accounts/profile.html', {
        'form': form,
        'user': request.user,
    })


# DRF API View
class CurrentUserAPIView(APIView):
    """Retrieve details of the currently authenticated user."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)
