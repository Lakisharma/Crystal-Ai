"""
Student authentication, Google OAuth flow, and student dashboard views for TeachLive.
"""

import calendar
import logging
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from accounts.models import AdminAuditLog, User
from accounts.oauth import (
    AccountConflictError,
    InvalidGoogleResponseError,
    OAuthFailureError,
    exchange_code_for_user_info,
    generate_google_auth_url,
    get_or_create_student_from_google,
    is_google_oauth_configured,
)
from classrooms.models import (
    Attendance,
    ClassAccessRequest,
    ClassEnrollment,
    ClassInviteToken,
    ClassParticipant,
    LiveClass,
    LiveClassParticipantModeration,
)
from notifications.email_service import EmailService
from notifications.models import Notification
from notifications.services import NotificationService

logger = logging.getLogger(__name__)


def get_student_authorized_classes(user):
    """
    Returns LiveClasses authorized for the specified student.
    Includes:
    - PUBLIC_LINK classes
    - PASSWORD_PROTECTED classes
    - ENROLLMENT_ONLY classes where student is actively ENROLLED or INVITED
    Excludes unauthorized ENROLLMENT_ONLY classes.
    """
    from django.db.models import Q
    return LiveClass.objects.filter(
        Q(access_mode__in=[LiveClass.AccessMode.PUBLIC_LINK, LiveClass.AccessMode.PASSWORD_PROTECTED]) |
        Q(access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY, enrollments__student=user, enrollments__status__in=[ClassEnrollment.Status.ENROLLED, ClassEnrollment.Status.INVITED])
    ).select_related('teacher').distinct()


from django.utils.http import url_has_allowed_host_and_scheme


def is_safe_redirect_url(url: str, request) -> bool:
    """Validates that a redirect URL is strictly internal and does not lead to an external domain."""
    if not url:
        return False
    if url.startswith('/') and not url.startswith('//'):
        try:
            return url_has_allowed_host_and_scheme(
                url=url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure()
            )
        except Exception:
            return False
    return False


class StudentLoginView(View):
    """
    Renders the dedicated TeachLive Student Login page.
    Features 'Continue with Google' and seamless redirect preservation.
    """
    def get(self, request):
        if request.user.is_authenticated:
            if request.user.is_student:
                next_url = request.GET.get('next', '').strip()
                if is_safe_redirect_url(next_url, request):
                    return redirect(next_url)
                return redirect('student:dashboard')
            elif request.user.is_teacher or request.user.is_admin_role:
                return redirect('accounts:teacher_dashboard')

        next_url = request.GET.get('next', '').strip()
        google_configured = is_google_oauth_configured()

        return render(request, 'student/login.html', {
            'next_url': next_url,
            'google_configured': google_configured,
            'is_debug': settings.DEBUG,
            'page_title': 'Student Login - Crystal AI',
        })


def is_simulator_allowed() -> bool:
    """Simulator mode is restricted strictly to local development and testing, never silently in production."""
    import sys
    if settings.DEBUG:
        return True
    if 'test' in sys.argv or getattr(settings, 'TESTING', False):
        return True
    if os.getenv('ALLOW_OAUTH_SIMULATOR', 'False').lower() in ('true', '1', 'yes'):
        return True
    return False


class StudentGoogleAuthInitiateView(View):
    """
    Initiates Google OAuth 2.0 flow for students.
    Stores CSRF state and destination next_url in session.
    Falls back to a dev/testing simulator if Google credentials are not set.
    """
    def get(self, request):
        next_url = request.GET.get('next', '').strip()

        # If Google OAuth credentials are fully configured, use real Google OAuth
        if is_google_oauth_configured():
            auth_url = generate_google_auth_url(request, next_url)
            return redirect(auth_url)

        # In production mode, do not expose simulator mode
        if not is_simulator_allowed():
            messages.error(
                request,
                "Google Sign-In is not currently configured in production. "
                "Please configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in Render Environment variables."
            )
            return redirect('student:login')

        # Development / Test Mode: Google OAuth credentials not yet configured
        return render(request, 'student/dev_google_simulator.html', {
            'next_url': next_url,
            'page_title': 'Google OAuth Simulator - Crystal AI',
        })

    def post(self, request):
        """Simulates Google OAuth sign-in during development / automated testing."""
        # Never allow simulator authentication in production
        if not is_simulator_allowed():
            messages.error(request, "Google OAuth Simulator is disabled in production.")
            return redirect('student:login')
        next_url = request.POST.get('next', '').strip() or request.session.pop('google_oauth_next', '')
        email = request.POST.get('email', '').strip().lower()
        name = request.POST.get('name', '').strip() or 'Demo Student'
        google_id = request.POST.get('google_id', '').strip() or f"google_{abs(hash(email)) % 100000000}"

        if not email:
            email = 'student.demo@teachlive.edu'

        user_info = {
            'sub': google_id,
            'email': email,
            'name': name,
            'picture': 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150',
            'given_name': name.split()[0] if name else 'Demo',
            'family_name': ' '.join(name.split()[1:]) if len(name.split()) > 1 else 'Student',
        }

        user, created = get_or_create_student_from_google(user_info)
        login(request, user)

        if created:
            messages.success(request, f"Welcome to TeachLive, {user.get_full_name() or user.username}!")
        else:
            messages.success(request, f"Welcome back, {user.get_full_name() or user.username}!")

        if is_safe_redirect_url(next_url, request):
            return redirect(next_url)
        return redirect('student:dashboard')


class StudentGoogleCallbackView(View):
    """
    Handles redirect callback from Google OAuth 2.0.
    Validates anti-CSRF state token, exchanges code for access token,
    retrieves user profile info, and creates/logs in student.
    """
    def get(self, request):
        error = request.GET.get('error')
        if error:
            logger.warning("Google OAuth returned error: %s", error)
            if error in ('access_denied', 'user_cancelled'):
                messages.warning(request, "Google sign-in was cancelled. Please try again.")
            else:
                messages.error(request, "Could not sign in with Google. Please try again.")
            return redirect('student:login')

        code = request.GET.get('code')
        state = request.GET.get('state')
        session_state = request.session.get('google_oauth_state')

        # CSRF Protection: verify state token
        if not state or state != session_state:
            logger.error("Google OAuth state token mismatch (state=%s, session=%s)", state, session_state)
            messages.error(request, "Authentication session expired or invalid. Please try again.")
            return redirect('student:login')

        # Clear state token
        request.session.pop('google_oauth_state', None)
        next_url = request.session.pop('google_oauth_next', '')

        try:
            user_info = exchange_code_for_user_info(request, code)
            user, created = get_or_create_student_from_google(user_info)

            # Enforce Student role
            if user.role != User.Role.STUDENT and not user.is_teacher and not user.is_admin_role:
                user.role = User.Role.STUDENT
                user.save(update_fields=['role'])

            login(request, user)

            if created:
                try:
                    EmailService.send_welcome_student_google_email(user)
                except Exception as mail_err:
                    logger.warning("Could not dispatch Google welcome email: %s", mail_err)
                messages.success(request, f"Welcome to TeachLive, {user.get_full_name() or user.username}!")
            else:
                messages.success(request, f"Welcome back, {user.get_full_name() or user.username}!")

            if is_safe_redirect_url(next_url, request):
                return redirect(next_url)
            return redirect('student:dashboard')

        except AccountConflictError as ace:
            logger.warning("Google login account conflict: %s", ace)
            messages.error(request, str(ace))
            return redirect('accounts:teacher_login')

        except (OAuthFailureError, InvalidGoogleResponseError) as oe:
            logger.error("Google OAuth failure: %s", oe)
            messages.error(request, f"Google authentication failed: {oe}")
            return redirect('student:login')

        except Exception as e:
            logger.exception("Google OAuth callback exception: %s", e)
            messages.error(request, "An unexpected error occurred during Google sign-in. Please try again.")
            return redirect('student:login')


@method_decorator(login_required(login_url='student:login'), name='dispatch')
class StudentDashboardView(View):
    """
    TeachLive Student Dashboard 2.0.
    Displays:
    - Welcome header with current date, student avatar, notification unread badge
    - Prominent LIVE NOW section with direct join action
    - Today's Classes with status badges
    - Upcoming Classes with schedule details
    - Real Attendance Summary metrics (Attended count, Completed classes, Attendance percentage)
    - Recently Attended Classes
    - Notifications Preview with unread badge
    Restricted private classes are hidden unless student is authorized.
    """
    def get(self, request):
        user = request.user

        if user.is_teacher:
            messages.info(request, "Redirected to Instructor Dashboard.")
            return redirect('accounts:teacher_dashboard')

        today_date = timezone.localtime(timezone.now()).date()

        # Authorized classes for this student
        authorized_classes = get_student_authorized_classes(user)

        # 1. LIVE NOW
        live_classes = (
            authorized_classes.filter(status=LiveClass.Status.LIVE)
            .order_by('-started_at', '-created_at')
        )
        live_class_now = live_classes.first()
        live_elapsed_minutes = 0
        if live_class_now and live_class_now.started_at:
            live_elapsed_minutes = max(0, int((timezone.now() - live_class_now.started_at).total_seconds() // 60))

        # 2. TODAY'S CLASSES
        today_classes = (
            authorized_classes.filter(scheduled_date=today_date)
            .order_by('scheduled_time')
        )

        # 3. UPCOMING CLASSES (Scheduled, today or future)
        upcoming_classes = (
            authorized_classes.filter(
                status=LiveClass.Status.SCHEDULED,
                scheduled_date__gte=today_date
            )
            .order_by('scheduled_date', 'scheduled_time')[:8]
        )

        # 4. INVITATIONS
        invited_enrollments = (
            ClassEnrollment.objects.filter(
                student=user,
                status=ClassEnrollment.Status.INVITED
            )
            .select_related('live_class', 'live_class__teacher')
            .order_by('-created_at')[:6]
        )
        invited_classes = [e.live_class for e in invited_enrollments]

        # 5. REAL ATTENDANCE SUMMARY METRICS
        attended_ids_from_att = set(
            Attendance.objects.filter(
                student=user,
                status=Attendance.Status.PRESENT
            ).values_list('live_class_id', flat=True)
        )
        attended_ids_from_part = set(
            ClassParticipant.objects.filter(
                models_q_student(user)
            ).values_list('live_class_id', flat=True)
        )
        attended_class_ids = attended_ids_from_att | attended_ids_from_part
        attended_count = len(attended_class_ids)

        enrolled_class_ids = set(
            ClassEnrollment.objects.filter(
                student=user,
                status=ClassEnrollment.Status.ENROLLED
            ).values_list('live_class_id', flat=True)
        )
        relevant_completed_ids = (
            set(authorized_classes.filter(status=LiveClass.Status.COMPLETED).values_list('id', flat=True))
            | enrolled_class_ids
            | attended_class_ids
        )
        total_completed_classes = LiveClass.objects.filter(
            id__in=relevant_completed_ids,
            status=LiveClass.Status.COMPLETED
        ).count()

        if total_completed_classes > 0:
            attendance_percentage = min(100.0, round((attended_count / total_completed_classes) * 100, 1))
        else:
            attendance_percentage = None

        # 6. RECENT ATTENDANCES & PARTICIPATIONS
        recent_attendances = (
            Attendance.objects.filter(student=user)
            .select_related('live_class', 'live_class__teacher')
            .order_by('-joined_at')[:6]
        )
        recent_participations = (
            ClassParticipant.objects.filter(models_q_student(user))
            .select_related('live_class', 'live_class__teacher')
            .order_by('-join_time')[:8]
        )

        # 7. NOTIFICATIONS PREVIEW
        unread_notifications_count = Notification.objects.filter(
            recipient=user,
            is_read=False
        ).count()
        latest_notifications = Notification.objects.filter(
            recipient=user
        ).order_by('-created_at')[:5]

        return render(request, 'student/dashboard.html', {
            'student': user,
            'today_date': today_date,
            'live_classes': live_classes,
            'live_class_now': live_class_now,
            'live_elapsed_minutes': live_elapsed_minutes,
            'today_classes': today_classes,
            'upcoming_classes': upcoming_classes,
            'invited_classes': invited_classes,
            'attended_count': attended_count,
            'total_completed_classes': total_completed_classes,
            'attendance_percentage': attendance_percentage,
            'recent_attendances': recent_attendances,
            'recent_participations': recent_participations,
            'unread_notifications_count': unread_notifications_count,
            'latest_notifications': latest_notifications,
            'page_title': 'Student Dashboard - TeachLive',
        })


def check_student_can_join(live_class, user):
    """
    Server-side authorization check to determine if student can join live session now.
    """
    if not user.is_authenticated or not user.is_student:
        return False
    if live_class.status != LiveClass.Status.LIVE:
        return False
    # Check if student was revoked
    is_revoked = ClassEnrollment.objects.filter(
        live_class=live_class,
        student=user,
        status=ClassEnrollment.Status.REVOKED
    ).exists()
    if is_revoked:
        return False
    # Check active session moderation restriction
    is_moderated = LiveClassParticipantModeration.objects.filter(
        live_class=live_class,
        student=user,
        action=LiveClassParticipantModeration.Action.REMOVED,
        active=True
    ).exists()
    if is_moderated:
        return False
    # Check access mode
    if live_class.access_mode in (LiveClass.AccessMode.PUBLIC_LINK, LiveClass.AccessMode.PASSWORD_PROTECTED):
        return True
    if live_class.access_mode == LiveClass.AccessMode.ENROLLMENT_ONLY:
        return ClassEnrollment.objects.filter(
            live_class=live_class,
            student=user,
            status=ClassEnrollment.Status.ENROLLED
        ).exists()
    return False


@method_decorator(login_required(login_url='student:login'), name='dispatch')
class StudentClassesListView(View):
    """
    Complete Student Classes Page at /student/classes/.
    Organized into: All, Live Now, Today, Upcoming, Invitations, Pending Requests, and Completed.
    Enforces privacy rules so private enrollment-only classes are never exposed.
    """
    def get(self, request):
        user = request.user
        if user.is_teacher:
            return redirect('accounts:teacher_dashboard')

        active_tab = request.GET.get('tab', 'all').strip().lower()
        search_query = request.GET.get('q', '').strip()
        today_date = timezone.localtime(timezone.now()).date()

        authorized_classes = get_student_authorized_classes(user)

        if search_query:
            authorized_classes = authorized_classes.filter(
                Q(title__icontains=search_query) |
                Q(subject__icontains=search_query) |
                Q(teacher__username__icontains=search_query) |
                Q(teacher__first_name__icontains=search_query) |
                Q(teacher__last_name__icontains=search_query) |
                Q(room_code__icontains=search_query)
            )

        live_classes = (
            authorized_classes.filter(status=LiveClass.Status.LIVE)
            .order_by('-started_at', '-created_at')
        )
        today_classes = (
            authorized_classes.filter(scheduled_date=today_date)
            .order_by('scheduled_time')
        )
        upcoming_classes = (
            authorized_classes.filter(
                status=LiveClass.Status.SCHEDULED,
                scheduled_date__gte=today_date
            )
            .order_by('scheduled_date', 'scheduled_time')
        )

        invited_enrollments = (
            ClassEnrollment.objects.filter(
                student=user,
                status=ClassEnrollment.Status.INVITED
            )
            .select_related('live_class', 'live_class__teacher')
            .order_by('-created_at')
        )
        if search_query:
            invited_enrollments = invited_enrollments.filter(
                Q(live_class__title__icontains=search_query) |
                Q(live_class__subject__icontains=search_query)
            )
        invited_classes = [e.live_class for e in invited_enrollments]

        # Pending Access Requests
        pending_access_requests = (
            ClassAccessRequest.objects.filter(
                student=user,
                status=ClassAccessRequest.Status.PENDING
            )
            .select_related('live_class', 'live_class__teacher')
            .order_by('-created_at')
        )
        if search_query:
            pending_access_requests = pending_access_requests.filter(
                Q(live_class__title__icontains=search_query) |
                Q(live_class__subject__icontains=search_query)
            )

        attended_class_ids = (
            ClassParticipant.objects.filter(models_q_student(user))
            .values_list('live_class_id', flat=True)
        )
        enrolled_class_ids = (
            ClassEnrollment.objects.filter(student=user)
            .values_list('live_class_id', flat=True)
        )
        completed_classes_qs = (
            LiveClass.objects.filter(
                id__in=set(list(attended_class_ids) + list(enrolled_class_ids) + list(authorized_classes.filter(status=LiveClass.Status.COMPLETED).values_list('id', flat=True))),
                status=LiveClass.Status.COMPLETED
            )
            .select_related('teacher')
            .order_by('-ended_at', '-scheduled_date')
        )
        if search_query:
            completed_classes_qs = completed_classes_qs.filter(
                Q(title__icontains=search_query) | Q(subject__icontains=search_query)
            )

        # Pagination for completed classes (9 per page)
        paginator = Paginator(completed_classes_qs, 9)
        page_number = request.GET.get('page', 1)
        try:
            completed_page_obj = paginator.get_page(page_number)
        except (PageNotAnInteger, EmptyPage):
            completed_page_obj = paginator.get_page(1)

        return render(request, 'student/classes_list.html', {
            'student': user,
            'classes': authorized_classes,
            'live_classes': live_classes,
            'today_classes': today_classes,
            'upcoming_classes': upcoming_classes,
            'invited_classes': invited_classes,
            'pending_requests': pending_access_requests,
            'pending_requests_count': pending_access_requests.count(),
            'completed_classes': completed_page_obj,
            'completed_total_count': completed_classes_qs.count(),
            'is_paginated': completed_page_obj.has_other_pages(),
            'page_obj': completed_page_obj,
            'active_tab': active_tab,
            'search_query': search_query,
            'page_title': 'My Classes - TeachLive Student',
        })


class StudentClassInviteView(View):
    """
    Secure class invite landing page at /student/classes/invite/<str:token>/.
    Validates token, capacity, revocation, and allows student to Accept or Decline.
    """
    def get(self, request, token):
        if not request.user.is_authenticated:
            invite_path = request.path
            return redirect(f"{reverse('student:login')}?next={invite_path}")

        user = request.user
        if user.is_teacher and not user.is_admin_role:
            messages.info(request, "This invite link is designed for students. Redirected to teacher dashboard.")
            return redirect('accounts:teacher_dashboard')

        token_obj = ClassInviteToken.objects.filter(token=token).select_related('live_class', 'live_class__teacher').first()
        if not token_obj or not token_obj.is_valid:
            return render(request, 'student/class_invite.html', {
                'invalid_token': True,
                'error_message': 'This invitation link is invalid, has expired, or has been revoked by the instructor.',
                'page_title': 'Invalid Invitation - TeachLive',
            })

        live_class = token_obj.live_class
        enrollment = ClassEnrollment.objects.filter(live_class=live_class, student=user).first()

        is_revoked = enrollment is not None and enrollment.status == ClassEnrollment.Status.REVOKED
        is_enrolled = enrollment is not None and enrollment.status == ClassEnrollment.Status.ENROLLED
        is_full = live_class.max_students > 0 and live_class.enrolled_count >= live_class.max_students

        return render(request, 'student/class_invite.html', {
            'token_obj': token_obj,
            'live_class': live_class,
            'teacher': live_class.teacher,
            'enrollment': enrollment,
            'is_revoked': is_revoked,
            'is_enrolled': is_enrolled,
            'is_full': is_full,
            'available_seats': live_class.available_seats,
            'page_title': f"Invitation: {live_class.title} - TeachLive",
        })

    def post(self, request, token):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('student:login')}?next={request.path}")

        user = request.user
        token_obj = ClassInviteToken.objects.filter(token=token).select_related('live_class', 'live_class__teacher').first()
        if not token_obj or not token_obj.is_valid:
            messages.error(request, "This invitation link is invalid or has expired.")
            return redirect('student:classes')

        live_class = token_obj.live_class
        action = request.POST.get('action', 'accept').strip().lower()

        if action == 'decline':
            enrollment = ClassEnrollment.objects.filter(live_class=live_class, student=user).first()
            if enrollment and enrollment.status == ClassEnrollment.Status.INVITED:
                enrollment.status = ClassEnrollment.Status.REVOKED
                enrollment.revocation_reason = "Invitation declined by student"
                enrollment.revoked_at = timezone.now()
                enrollment.save(update_fields=['status', 'revocation_reason', 'revoked_at'])

            AdminAuditLog.log_action(
                user=user,
                action=AdminAuditLog.Action.INVITATION_DECLINED,
                target_model='LiveClass',
                target_id=live_class.id,
                details=f"Student {user.username} declined invite for '{live_class.title}'"
            )
            try:
                NotificationService.notify_invitation_declined(live_class, user)
            except Exception as e:
                logger.warning("Could not dispatch notification: %s", e)

            messages.info(request, f"You have declined the invitation to join '{live_class.title}'.")
            return redirect('student:classes')

        # Accept action
        enrollment = ClassEnrollment.objects.filter(live_class=live_class, student=user).first()
        if enrollment and enrollment.status == ClassEnrollment.Status.REVOKED:
            messages.error(request, "Your access to this class was previously revoked by the instructor. You cannot use this invite link.")
            return redirect('student:classes')

        if live_class.max_students > 0 and live_class.enrolled_count >= live_class.max_students:
            messages.error(request, "This class is currently at full capacity.")
            return redirect('student:class_invite', token=token)

        with transaction.atomic():
            if not enrollment:
                enrollment = ClassEnrollment.objects.create(
                    live_class=live_class,
                    student=user,
                    status=ClassEnrollment.Status.ENROLLED,
                    enrolled_at=timezone.now()
                )
            else:
                enrollment.status = ClassEnrollment.Status.ENROLLED
                enrollment.enrolled_at = timezone.now()
                enrollment.revocation_reason = ''
                enrollment.save()

            # Approve any pending access request
            ClassAccessRequest.objects.filter(
                live_class=live_class,
                student=user,
                status=ClassAccessRequest.Status.PENDING
            ).update(
                status=ClassAccessRequest.Status.APPROVED,
                reviewed_at=timezone.now(),
                reviewed_by=live_class.teacher
            )

            # Record token usage
            token_obj.uses_count += 1
            if token_obj.max_uses and token_obj.uses_count >= token_obj.max_uses:
                token_obj.is_revoked = True
            token_obj.save(update_fields=['uses_count', 'is_revoked'])

            AdminAuditLog.log_action(
                user=user,
                action=AdminAuditLog.Action.INVITATION_ACCEPTED,
                target_model='LiveClass',
                target_id=live_class.id,
                details=f"Student {user.username} accepted invite for '{live_class.title}'"
            )
            try:
                NotificationService.notify_invitation_accepted(live_class, user)
            except Exception as e:
                logger.warning("Could not dispatch notification: %s", e)

        messages.success(request, f"Successfully joined '{live_class.title}'!")
        return redirect('student:class_detail', class_id=live_class.id)


@method_decorator(login_required(login_url='student:login'), name='dispatch')
class StudentClassDetailView(View):
    """
    Student Class Details page at /student/classes/<int:class_id>/.
    Displays:
    - Section A: Class details (title, subject, description, schedule, duration, room_code, status)
    - Section B: Teacher info (name, email)
    - Section C: Student access status badge (Enrolled, Invited, Pending Access, Access Revoked)
    - Section D: Attendance summary for this student in this class
    - Server-enforced join button (only when allowed)
    - Action buttons: Leave class (if enrolled), Accept/Decline (if invited), Request Access (if enrollment-only)
    """
    def get(self, request, class_id):
        user = request.user
        live_class = get_object_or_404(
            LiveClass.objects.select_related('teacher'),
            pk=class_id
        )

        if user.is_teacher and live_class.teacher == user:
            return redirect('classrooms:live_detail', pk=live_class.id)

        enrollment = ClassEnrollment.objects.filter(live_class=live_class, student=user).first()
        access_request = ClassAccessRequest.objects.filter(live_class=live_class, student=user).order_by('-created_at').first()

        # Student attendance records for this class
        attendances = Attendance.objects.filter(
            live_class=live_class,
            student=user
        ).order_by('-joined_at')
        total_sessions_attended = attendances.filter(status=Attendance.Status.PRESENT).count()
        total_minutes_attended = sum(a.total_duration for a in attendances)
        last_attended_date = attendances.first().joined_at if attendances.exists() else None

        can_join = check_student_can_join(live_class, user)

        return render(request, 'student/class_detail.html', {
            'student': user,
            'live_class': live_class,
            'enrollment': enrollment,
            'access_request': access_request,
            'attendances': attendances,
            'total_sessions_attended': total_sessions_attended,
            'total_minutes_attended': total_minutes_attended,
            'last_attended_date': last_attended_date,
            'can_join': can_join,
            'page_title': f"{live_class.title} - TeachLive Student",
        })


@method_decorator(login_required(login_url='student:login'), name='dispatch')
class StudentLeaveClassView(View):
    """
    Allows a student to leave an enrolled class.
    POST /student/classes/<int:class_id>/leave/
    """
    def post(self, request, class_id):
        user = request.user
        live_class = get_object_or_404(LiveClass, pk=class_id)
        enrollment = get_object_or_404(
            ClassEnrollment,
            live_class=live_class,
            student=user,
            status=ClassEnrollment.Status.ENROLLED
        )

        reason = request.POST.get('reason', '').strip()[:255] or "Left class voluntarily"

        with transaction.atomic():
            enrollment.status = ClassEnrollment.Status.REVOKED
            enrollment.revoked_at = timezone.now()
            enrollment.revocation_reason = reason
            enrollment.save(update_fields=['status', 'revoked_at', 'revocation_reason'])

            AdminAuditLog.log_action(
                user=user,
                action=AdminAuditLog.Action.STUDENT_REMOVED,
                target_model='LiveClass',
                target_id=live_class.id,
                details=f"Student {user.username} left class '{live_class.title}': {reason}"
            )

            try:
                NotificationService.notify_student_removed(live_class, user, reason=reason)
            except Exception as e:
                logger.warning("Could not dispatch notification: %s", e)

        messages.success(request, f"You have left the class '{live_class.title}'.")
        return redirect('student:classes')


@method_decorator(login_required(login_url='student:login'), name='dispatch')
class StudentAcceptInviteView(View):
    """
    POST /student/classes/<int:class_id>/accept/
    Allows student to accept an in-app invitation.
    """
    def post(self, request, class_id):
        user = request.user
        live_class = get_object_or_404(LiveClass, pk=class_id)
        enrollment = get_object_or_404(
            ClassEnrollment,
            live_class=live_class,
            student=user,
            status=ClassEnrollment.Status.INVITED
        )

        if live_class.max_students > 0 and live_class.enrolled_count >= live_class.max_students:
            messages.error(request, "This class is currently at maximum capacity.")
            return redirect('student:classes')

        with transaction.atomic():
            enrollment.status = ClassEnrollment.Status.ENROLLED
            enrollment.enrolled_at = timezone.now()
            enrollment.save(update_fields=['status', 'enrolled_at'])

            AdminAuditLog.log_action(
                user=user,
                action=AdminAuditLog.Action.INVITATION_ACCEPTED,
                target_model='LiveClass',
                target_id=live_class.id,
                details=f"Student {user.username} accepted in-app invitation for '{live_class.title}'"
            )

            try:
                NotificationService.notify_invitation_accepted(live_class, user)
            except Exception as e:
                logger.warning("Could not dispatch notification: %s", e)

        messages.success(request, f"You have joined '{live_class.title}'!")
        return redirect('student:class_detail', class_id=live_class.id)


@method_decorator(login_required(login_url='student:login'), name='dispatch')
class StudentDeclineInviteView(View):
    """
    POST /student/classes/<int:class_id>/decline/
    Allows student to decline an in-app invitation.
    """
    def post(self, request, class_id):
        user = request.user
        live_class = get_object_or_404(LiveClass, pk=class_id)
        enrollment = get_object_or_404(
            ClassEnrollment,
            live_class=live_class,
            student=user,
            status=ClassEnrollment.Status.INVITED
        )

        with transaction.atomic():
            enrollment.status = ClassEnrollment.Status.REVOKED
            enrollment.revoked_at = timezone.now()
            enrollment.revocation_reason = "Invitation declined by student"
            enrollment.save(update_fields=['status', 'revoked_at', 'revocation_reason'])

            AdminAuditLog.log_action(
                user=user,
                action=AdminAuditLog.Action.INVITATION_DECLINED,
                target_model='LiveClass',
                target_id=live_class.id,
                details=f"Student {user.username} declined in-app invitation for '{live_class.title}'"
            )

            try:
                NotificationService.notify_invitation_declined(live_class, user)
            except Exception as e:
                logger.warning("Could not dispatch notification: %s", e)

        messages.info(request, f"You have declined the invitation for '{live_class.title}'.")
        return redirect('student:classes')


def models_q_student(user):
    """Helper to query participant records for a user by user instance or email."""
    from django.db.models import Q
    if user.email:
        return Q(user=user) | Q(student_email__iexact=user.email)
    return Q(user=user)


def student_logout_view(request):
    """Dedicated student logout handler."""
    logout(request)
    messages.info(request, "You have been signed out successfully.")
    return redirect('student:login')


@method_decorator(login_required(login_url='student:login'), name='dispatch')
class StudentCalendarView(View):
    """
    Student Interactive Schedule Calendar View at /student/calendar/.
    Displays monthly schedule of authorized classes only.
    Strictly excludes unauthorized private classes.
    """
    def get(self, request):
        user = request.user
        if user.is_teacher:
            return redirect('classrooms:teacher_calendar')

        now = timezone.now()
        try:
            year = int(request.GET.get('year', now.year))
            month = int(request.GET.get('month', now.month))
            if month < 1 or month > 12:
                raise ValueError
        except (ValueError, TypeError):
            year = now.year
            month = now.month

        if month == 1:
            prev_month = 12
            prev_year = year - 1
        else:
            prev_month = month - 1
            prev_year = year

        if month == 12:
            next_month = 1
            next_year = year + 1
        else:
            next_month = month + 1
            next_year = year

        month_name = calendar.month_name[month]

        # Fetch only authorized classes for this student
        authorized_classes = get_student_authorized_classes(user).filter(
            scheduled_date__year=year,
            scheduled_date__month=month
        ).order_by('scheduled_time')

        classes_by_day = {}
        for c in authorized_classes:
            d = c.scheduled_date.day
            if d not in classes_by_day:
                classes_by_day[d] = []
            classes_by_day[d].append(c)

        cal = calendar.Calendar(firstweekday=6)  # Sunday
        month_weeks = cal.monthdayscalendar(year, month)

        return render(request, 'student/calendar.html', {
            'student': user,
            'year': year,
            'month': month,
            'month_name': month_name,
            'prev_year': prev_year,
            'prev_month': prev_month,
            'next_year': next_year,
            'next_month': next_month,
            'today': now.date(),
            'month_weeks': month_weeks,
            'classes_by_day': classes_by_day,
            'total_month_classes': authorized_classes.count(),
            'page_title': f"{month_name} {year} Schedule - TeachLive Student Calendar",
        })


@method_decorator(login_required(login_url='student:login'), name='dispatch')
class StudentAttendanceListView(View):
    """
    Student Attendance History & Analytics at /student/attendance/.
    Sections 8 & 14:
    Student sees strictly their own attendance records.
    Never exposes any other students or participants.
    Summary cards:
    - Classes attended
    - Total attendance duration
    - Average session duration
    - Overall attendance percentage
    Filters:
    - All, This Week, This Month, Custom Date Range
    Pagination enabled.
    """
    def get(self, request):
        user = request.user
        if not user.is_student and not user.is_admin_role:
            return redirect('classrooms:teacher_attendance')

        date_preset = request.GET.get('date_preset', '').strip().lower()
        date_from_str = request.GET.get('date_from', '').strip()
        date_to_str = request.GET.get('date_to', '').strip()

        from classrooms.attendance_services import (
            calculate_attendance_percentage,
            get_date_range_bounds,
            get_student_attendance_metrics,
        )

        start_dt, end_dt, date_label = get_date_range_bounds(
            date_preset=date_preset,
            date_from_str=date_from_str,
            date_to_str=date_to_str
        )

        # Base QuerySet: strictly for this student
        qs = Attendance.objects.filter(student=user).select_related(
            'live_class',
            'live_class__teacher'
        ).order_by('-joined_at')

        if start_dt:
            qs = qs.filter(joined_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(joined_at__lte=end_dt)

        # Metrics for student cards
        metrics = get_student_attendance_metrics(
            student_user=user,
            date_from=start_dt,
            date_to=end_dt
        )

        # Pagination
        paginator = Paginator(qs, 15)
        page_num = request.GET.get('page', 1)
        try:
            attendances_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            attendances_page = paginator.page(1)

        return render(request, 'student/attendance_list.html', {
            'attendances': attendances_page,
            'metrics': metrics,
            'selected_date_preset': date_preset,
            'selected_date_from': date_from_str,
            'selected_date_to': date_to_str,
            'date_label': date_label,
            'page_title': 'My Attendance - TeachLive',
        })


@method_decorator(login_required(login_url='student:login'), name='dispatch')
class StudentClassAttendanceDetailView(View):
    """
    Student Class Attendance Detail at /student/classes/<class_id>/attendance/.
    Section 9:
    Allows viewing only if student is authorized for that class.
    Shows only the student's own attendance for this class.
    Never exposes other students or class participants (strict IDOR & privacy protection).
    """
    def get(self, request, class_id):
        user = request.user
        live_class = get_object_or_404(LiveClass, pk=class_id)

        # Strict Authorization check
        is_authorized = (
            get_student_authorized_classes(user).filter(pk=class_id).exists() or
            Attendance.objects.filter(live_class=live_class, student=user).exists()
        )
        if not is_authorized and not user.is_admin_role:
            raise PermissionDenied("Access Denied: You are not enrolled in or authorized for this class.")

        # Fetch only this student's attendance record
        attendance = Attendance.objects.filter(
            live_class=live_class,
            student=user
        ).first()

        from classrooms.attendance_services import calculate_attendance_percentage

        scheduled_duration = live_class.duration or 0
        att_pct = 0.0
        if attendance:
            att_pct = calculate_attendance_percentage(attendance.total_duration, scheduled_duration)

        return render(request, 'student/class_attendance_detail.html', {
            'live_class': live_class,
            'attendance': attendance,
            'attendance_percentage': att_pct,
            'scheduled_duration': scheduled_duration,
            'page_title': f"My Attendance: {live_class.title} - TeachLive",
        })
