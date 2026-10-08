"""
LiveClass WebRTC live classroom views, LiveKit token generation endpoint,
teacher and student live rooms, in-room real-time chat, and lifecycle management.
"""

from datetime import timedelta
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django.views import View

from classrooms.livekit_service import (
    LiveKitNotConfiguredError,
    generate_livekit_access_token,
    get_livekit_room_name,
    is_livekit_configured,
)
from classrooms.models import (
    Attendance,
    ChatMessage,
    ClassAccessRequest,
    ClassEnrollment,
    ClassParticipant,
    LiveClass,
    LiveClassParticipantModeration,
)
from classrooms.scheduling import (
    can_student_join_class,
    can_teacher_start_class,
    get_class_status,
)
from notifications.services import NotificationService

logger = logging.getLogger(__name__)


def check_student_class_access(live_class, user):
    """
    Validates whether an authenticated user has authorization to access the specified LiveClass.
    Returns (is_authorized: bool, error_message: str).
    """
    if not user.is_authenticated:
        return False, "Authentication required."
    if not user.is_active:
        return False, "Your account is deactivated."

    # Teacher owner or admin always has access
    if user == live_class.teacher or user.is_admin_role:
        return True, ""

    # Check active session-level removal
    if LiveClassParticipantModeration.objects.filter(
        live_class=live_class,
        student=user,
        action=LiveClassParticipantModeration.Action.REMOVED,
        active=True
    ).exists():
        return False, "You have been removed from this live class by the teacher."

    # Must have student role
    if not (user.is_student or getattr(user, 'role', '') == 'STUDENT'):
        return False, "Only registered students may join live classes as attendees."

    # Check if student's enrollment was revoked
    revoked_enrollment = ClassEnrollment.objects.filter(
        live_class=live_class,
        student=user,
        status=ClassEnrollment.Status.REVOKED
    ).exists()
    if revoked_enrollment:
        return False, "Access Denied: Your access to this class has been revoked by the instructor."

    # Check class access mode
    if live_class.access_mode == LiveClass.AccessMode.ENROLLMENT_ONLY:
        enrollment = ClassEnrollment.objects.filter(
            live_class=live_class,
            student=user
        ).first()
        if not enrollment:
            return False, "Access Denied: This live class is restricted to enrolled students."
        if enrollment.status not in (ClassEnrollment.Status.ENROLLED, ClassEnrollment.Status.INVITED):
            return False, "Access Denied: You do not have active enrollment in this class."

    return True, ""


def check_class_capacity(live_class, user):
    """
    Checks whether the class has reached max capacity for students,
    safely excluding reconnects for the same user.
    Returns (is_full: bool, active_count: int).
    """
    if user == live_class.teacher or user.is_admin_role:
        return False, 0

    active_count = live_class.participants.filter(
        status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED]
    ).exclude(user=user).count()

    return (active_count >= live_class.max_students), active_count


# =====================================================================
# 1. Public Class Landing / Join Screen
# =====================================================================

class LiveClassJoinScreenView(View):
    """
    Public live class landing / join screen at /live/<room_code>/.
    Enforces authentication (redirecting to student login with return URL),
    validates room code and class status, and prompts for room password if protected.
    """
    def get(self, request, room_code):
        # 1. Authentication check: redirect to student login with ?next= if not signed in
        if not request.user.is_authenticated:
            login_url = f"/student/login/?next={request.path}"
            return redirect(login_url)

        if not request.user.is_active:
            raise PermissionDenied("Access Denied: Your account has been deactivated.")

        # 2. Clean room code and validate class exists
        clean_code = room_code.strip()
        live_class = LiveClass.objects.filter(room_code__iexact=clean_code).select_related('teacher').first()

        if not live_class:
            return render(request, 'classrooms/live_not_found.html', {
                'room_code': clean_code,
                'page_title': 'Classroom Not Found - TeachLive',
            }, status=404)

        # 3. If teacher owner visits join screen, redirect directly to teacher live view
        is_teacher_owner = (request.user == live_class.teacher or request.user.is_admin_role)
        if is_teacher_owner and live_class.status in (LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE):
            return redirect('live_room:teacher_live', room_code=live_class.room_code)

        # Check if student was removed from this live session
        if not is_teacher_owner and LiveClassParticipantModeration.objects.filter(
            live_class=live_class,
            student=request.user,
            action=LiveClassParticipantModeration.Action.REMOVED,
            active=True
        ).exists():
            return redirect('live_room:removed', room_code=live_class.room_code)

        # 4. Check enrollment & access request status for student
        enrollment = None
        is_enrolled = False
        is_revoked = False
        pending_request = None

        if not is_teacher_owner:
            enrollment = live_class.enrollments.filter(student=request.user).first()
            if enrollment:
                is_enrolled = enrollment.status in (ClassEnrollment.Status.ENROLLED, ClassEnrollment.Status.INVITED)
                is_revoked = enrollment.status == ClassEnrollment.Status.REVOKED
            pending_request = live_class.access_requests.filter(
                student=request.user,
                status=ClassAccessRequest.Status.PENDING
            ).first()

        # Check participant count and capacity
        is_capacity_reached, active_participants_count = check_class_capacity(live_class, request.user)

        return render(request, 'classrooms/live_join.html', {
            'live_class': live_class,
            'is_teacher_owner': is_teacher_owner,
            'has_password': (live_class.has_password or live_class.access_mode == LiveClass.AccessMode.PASSWORD_PROTECTED),
            'active_participants_count': active_participants_count,
            'is_capacity_reached': is_capacity_reached,
            'enrollment': enrollment,
            'is_enrolled': is_enrolled,
            'is_revoked': is_revoked,
            'pending_request': pending_request,
            'page_title': f"{live_class.title} - TeachLive",
        })


@method_decorator(login_required(login_url='student:login'), name='dispatch')
class LiveClassJoinActionView(View):
    """
    POST-only action to enter a live class at /live/<room_code>/join/.
    Verifies status, server-side password check, capacity limit,
    and creates/updates ClassParticipant and Attendance records.
    """
    def post(self, request, room_code):
        clean_code = room_code.strip()
        live_class = LiveClass.objects.filter(room_code__iexact=clean_code).first()

        if not live_class:
            messages.error(request, "Classroom not found. Please check your room code.")
            return redirect('student:dashboard')

        user = request.user
        if not user.is_active:
            raise PermissionDenied("Access Denied: Your account has been deactivated.")
        is_teacher_owner = (user == live_class.teacher or user.is_admin_role)

        # 1. Status Validation
        if live_class.status == LiveClass.Status.CANCELLED:
            messages.error(request, "This class has been cancelled.")
            return redirect('live_room:detail', room_code=live_class.room_code)

        if live_class.status in (LiveClass.Status.COMPLETED, 'ENDED') or live_class.is_ended:
            messages.error(request, "This live class has ended.")
            return redirect('live_room:detail', room_code=live_class.room_code)

        if live_class.status == LiveClass.Status.SCHEDULED and not is_teacher_owner:
            can_join, join_reason, countdown = can_student_join_class(live_class, user)
            if not can_join:
                messages.warning(request, f"This class is scheduled but has not started yet. {join_reason}")
                return redirect('live_room:detail', room_code=live_class.room_code)
            messages.info(
                request,
                "This class is scheduled but has not started yet. Class join window is open; waiting for instructor to start."
            )
            return redirect('live_room:detail', room_code=live_class.room_code)

        # If teacher enters scheduled class, check early start window
        if is_teacher_owner and live_class.status == LiveClass.Status.SCHEDULED:
            can_start, start_reason, _ = can_teacher_start_class(live_class, user)
            if not can_start:
                messages.warning(request, start_reason)
                return redirect('live_room:detail', room_code=live_class.room_code)
            live_class.start_class()
            return redirect('live_room:teacher_live', room_code=live_class.room_code)

        # 2. Access Mode & Authorization Validation
        if not is_teacher_owner:
            is_authorized, auth_err = check_student_class_access(live_class, user)
            if not is_authorized:
                if "removed" in auth_err.lower():
                    return redirect('live_room:removed', room_code=live_class.room_code)
                messages.error(request, auth_err)
                return redirect('live_room:detail', room_code=live_class.room_code)

        # 3. Password Verification (server-side, never exposed to client)
        is_pwd_required = (live_class.has_password or live_class.access_mode == LiveClass.AccessMode.PASSWORD_PROTECTED)
        if is_pwd_required and not is_teacher_owner:
            password = request.POST.get('password', '').strip()
            if not password or not live_class.check_room_password(password):
                messages.error(request, "Incorrect class password. Please enter the valid password provided by your teacher.")
                return redirect('live_room:detail', room_code=live_class.room_code)

        # 4. Capacity Check
        is_full, active_count = check_class_capacity(live_class, user)
        if is_full and not is_teacher_owner:
            messages.error(request, "This live class is currently full.")
            return redirect('live_room:detail', room_code=live_class.room_code)

        now = timezone.now()
        student_display_name = user.get_full_name() or user.username
        student_email = user.email or f"{user.username}@student.teachlive"

        # 4. ClassParticipant record (create or update existing, duplicate prevention)
        participant = ClassParticipant.objects.filter(
            live_class=live_class,
            user=user
        ).first()

        if not participant and user.email:
            participant = ClassParticipant.objects.filter(
                live_class=live_class,
                student_email__iexact=user.email
            ).first()

        if participant:
            participant.user = user
            participant.student_name = student_display_name
            participant.student_email = student_email
            # Only reset join_time if participant was not already active (duplicate refresh prevention)
            if participant.status not in (ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED):
                participant.join_time = now
            participant.leave_time = None
            participant.last_seen_at = now
            participant.status = ClassParticipant.Status.ACTIVE
            participant.save()
        else:
            participant = ClassParticipant.objects.create(
                live_class=live_class,
                user=user,
                student_name=student_display_name,
                student_email=student_email,
                join_time=now,
                last_seen_at=now,
                status=ClassParticipant.Status.ACTIVE,
            )

        # 5. Attendance Record (create or update existing with status = PRESENT)
        attendance = Attendance.objects.filter(
            live_class=live_class,
            student=user
        ).first()

        if not attendance:
            attendance = Attendance.objects.filter(
                live_class=live_class,
                student_name=student_display_name
            ).first()

        if attendance:
            attendance.student = user
            attendance.student_name = student_display_name
            attendance.status = Attendance.Status.PRESENT
            # If rejoining after previous left/disconnected, reset left_at
            if attendance.left_at is not None:
                attendance.joined_at = now
                attendance.left_at = None
            attendance.save()
        else:
            Attendance.objects.create(
                live_class=live_class,
                student=user,
                student_name=student_display_name,
                status=Attendance.Status.PRESENT,
                joined_at=now,
            )

        # Record session flag for active membership
        request.session[f'in_live_class_{live_class.room_code}'] = True

        if is_teacher_owner:
            return redirect('live_room:teacher_live', room_code=live_class.room_code)
        return redirect('live_room:classroom', room_code=live_class.room_code)

    def get(self, request, room_code):
        """GET requests to join redirect back to the join screen."""
        return redirect('live_room:detail', room_code=room_code)


# =====================================================================
# 2. LiveKit Secure Token Generation Endpoint
# =====================================================================

class LiveKitTokenAPIView(View):
    """
    Secure endpoint generating short-lived LiveKit access tokens.
    URL: /live/<room_code>/token/
    Validates authentication, class existence, class status (LIVE),
    teacher ownership, and student capacity before generating token.
    Never returns LIVEKIT_API_SECRET.
    """
    def get(self, request, room_code):
        return self._generate_token(request, room_code)

    def post(self, request, room_code):
        return self._generate_token(request, room_code)

    def _generate_token(self, request, room_code):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required.'}, status=401)

        clean_code = room_code.strip()
        live_class = LiveClass.objects.filter(room_code__iexact=clean_code).first()
        if not live_class:
            return JsonResponse({'error': 'Classroom not found.'}, status=404)

        user = request.user
        is_teacher_owner = (user == live_class.teacher or user.is_admin_role)

        # Validate class status
        if live_class.status == LiveClass.Status.CANCELLED:
            return JsonResponse({'error': 'This live class has been cancelled.'}, status=400)

        if live_class.status in (LiveClass.Status.COMPLETED, 'ENDED') or live_class.is_ended:
            return JsonResponse({'error': 'This live class has ended.'}, status=400)

        if live_class.status == LiveClass.Status.SCHEDULED:
            if is_teacher_owner:
                can_start, start_reason, _ = can_teacher_start_class(live_class, user)
                if not can_start:
                    return JsonResponse({'error': start_reason}, status=400)
                live_class.start_class()
            else:
                can_join, join_reason, _ = can_student_join_class(live_class, user)
                if not can_join:
                    return JsonResponse({'error': join_reason}, status=400)
                return JsonResponse({'error': 'This class is scheduled and has not started yet. Waiting for instructor to begin.'}, status=400)

        # Enforce student role, access mode, capacity, and password
        if not is_teacher_owner:
            if not (user.is_student or getattr(user, 'role', '') == 'STUDENT'):
                return JsonResponse({'error': 'Only registered students may join live classes.'}, status=403)

            # Access mode validation
            is_authorized, auth_err = check_student_class_access(live_class, user)
            if not is_authorized:
                return JsonResponse({'error': auth_err}, status=403)

            # Enforce student capacity
            is_full, _ = check_class_capacity(live_class, user)
            if is_full:
                return JsonResponse({'error': 'This live class is currently full. The maximum student capacity has been reached.'}, status=403)

            # Enforce password validation if class has password
            is_pwd_required = (live_class.has_password or live_class.access_mode == LiveClass.AccessMode.PASSWORD_PROTECTED)
            if is_pwd_required and not request.session.get(f'in_live_class_{live_class.room_code}'):
                password = request.POST.get('password') or request.GET.get('password')
                if not password or not live_class.check_room_password(password):
                    return JsonResponse({'error': 'Class password verification required.'}, status=403)
                request.session[f'in_live_class_{live_class.room_code}'] = True

        # Generate LiveKit Token
        try:
            token_data = generate_livekit_access_token(
                live_class=live_class,
                user=user,
                is_teacher=is_teacher_owner
            )
            return JsonResponse(token_data)
        except LiveKitNotConfiguredError as e:
            logger.warning("LiveKit configuration missing: %s", e)
            return JsonResponse({
                'error': 'LiveKit credentials (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) are not configured in .env.'
            }, status=503)
        except Exception as e:
            logger.exception("Error generating LiveKit token: %s", e)
            return JsonResponse({'error': 'Failed to generate classroom access token.'}, status=500)


# =====================================================================
# 3. Teacher Live Classroom Page
# =====================================================================

@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherLiveClassroomView(View):
    """
    Teacher Live Classroom Page.
    Enforces teacher ownership (returns 403 if accessed by student or other teacher).
    Provides controls for Camera, Microphone, Screen Sharing, Students panel, Chat, and End Class.
    """
    def get(self, request, class_id=None, room_code=None):
        live_class = None
        if class_id:
            live_class = get_object_or_404(LiveClass, pk=class_id)
        elif room_code:
            live_class = get_object_or_404(LiveClass, room_code__iexact=room_code.strip())

        user = request.user
        # Enforce teacher ownership
        if user != live_class.teacher and not user.is_admin_role:
            raise PermissionDenied("You are not authorized to conduct this live class.")

        # Check status
        if live_class.status == LiveClass.Status.CANCELLED:
            messages.error(request, "This live class has been cancelled.")
            return redirect('classrooms:live_detail', pk=live_class.pk)

        if live_class.status in (LiveClass.Status.COMPLETED, 'ENDED') or live_class.is_ended:
            messages.info(request, "This live class has ended.")
            return redirect('classrooms:live_detail', pk=live_class.pk)

        # If scheduled, check early-start window
        if live_class.status == LiveClass.Status.SCHEDULED:
            can_start, start_reason, _ = can_teacher_start_class(live_class, user)
            if not can_start:
                messages.warning(request, start_reason)
                return redirect('classrooms:live_detail', pk=live_class.pk)
            live_class.start_class()

        recent_chat_messages = live_class.chat_messages.order_by('created_at')[:50]
        active_participants = live_class.participants.filter(
            status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED]
        ).order_by('-is_hand_raised', 'hand_raised_at', '-join_time')
        recent_moderation_events = live_class.moderation_events.select_related('student', 'created_by').order_by('-created_at')[:30]

        return render(request, 'classrooms/teacher_live.html', {
            'live_class': live_class,
            'is_teacher_owner': True,
            'recent_chat_messages': recent_chat_messages,
            'active_participants': active_participants,
            'recent_moderation_events': recent_moderation_events,
            'is_livekit_configured': is_livekit_configured(),
            'page_title': f"Conducting Live: {live_class.title} - TeachLive",
        })


# =====================================================================
# 4. Student Live Classroom Page
# =====================================================================

@method_decorator(login_required(login_url='student:login'), name='dispatch')
class StudentLiveClassroomView(View):
    """
    Student Live Classroom Page.
    Subscribes to teacher video, audio, and screen sharing.
    Students cannot publish camera/microphone by default.
    Provides Audio volume, Fullscreen, In-room chat, and Leave Classroom.
    """
    def get(self, request, room_code):
        clean_code = room_code.strip()
        live_class = get_object_or_404(LiveClass, room_code__iexact=clean_code)

        user = request.user
        if not user.is_active:
            raise PermissionDenied("Access Denied: Your account has been deactivated.")
        # If teacher owner opens this, route them to teacher classroom
        if user == live_class.teacher or user.is_admin_role:
            return redirect('live_room:teacher_live', room_code=live_class.room_code)

        # Check active session-level removal
        if LiveClassParticipantModeration.objects.filter(
            live_class=live_class,
            student=user,
            action=LiveClassParticipantModeration.Action.REMOVED,
            active=True
        ).exists():
            return redirect('live_room:removed', room_code=live_class.room_code)

        # Check status
        if live_class.status == LiveClass.Status.CANCELLED:
            messages.error(request, "This class has been cancelled.")
            return redirect('live_room:detail', room_code=live_class.room_code)

        if live_class.status in (LiveClass.Status.COMPLETED, 'ENDED') or live_class.is_ended:
            messages.error(request, "This live class has ended.")
            return redirect('live_room:detail', room_code=live_class.room_code)

        if live_class.status == LiveClass.Status.SCHEDULED:
            messages.info(request, "This class has not started yet. Waiting for teacher.")
            return redirect('live_room:detail', room_code=live_class.room_code)

        # Verify access mode
        is_authorized, auth_err = check_student_class_access(live_class, user)
        if not is_authorized:
            messages.error(request, auth_err)
            return redirect('live_room:detail', room_code=live_class.room_code)

        # Check password verification session
        is_pwd_required = (live_class.has_password or live_class.access_mode == LiveClass.AccessMode.PASSWORD_PROTECTED)
        if is_pwd_required and not request.session.get(f'in_live_class_{live_class.room_code}'):
            messages.warning(request, "Class password required.")
            return redirect('live_room:detail', room_code=live_class.room_code)

        # Capacity check if not already in session
        is_already_active = live_class.participants.filter(
            user=user,
            status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED]
        ).exists()
        if not is_already_active:
            is_full, _ = check_class_capacity(live_class, user)
            if is_full:
                messages.error(request, "This live class is currently full.")
                return redirect('live_room:detail', room_code=live_class.room_code)

        now = timezone.now()
        student_display_name = user.get_full_name() or user.username
        student_email = user.email or f"{user.username}@student.teachlive"

        # Ensure student participant and attendance records are registered and active (duplicate prevention)
        participant = ClassParticipant.objects.filter(live_class=live_class, user=user).first()
        if not participant and user.email:
            participant = ClassParticipant.objects.filter(live_class=live_class, student_email__iexact=user.email).first()

        if participant:
            if participant.status not in (ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED):
                participant.status = ClassParticipant.Status.ACTIVE
                participant.join_time = now
                participant.leave_time = None
            participant.last_seen_at = now
            participant.save()
        else:
            participant = ClassParticipant.objects.create(
                live_class=live_class,
                user=user,
                student_name=student_display_name,
                student_email=student_email,
                join_time=now,
                last_seen_at=now,
                status=ClassParticipant.Status.ACTIVE,
            )

        attendance = Attendance.objects.filter(live_class=live_class, student=user).first()
        if not attendance:
            attendance = Attendance.objects.filter(live_class=live_class, student_name=student_display_name).first()

        if attendance:
            if attendance.left_at is not None:
                attendance.status = Attendance.Status.PRESENT
                attendance.joined_at = now
                attendance.left_at = None
                attendance.save()
            elif attendance.status != Attendance.Status.PRESENT:
                attendance.status = Attendance.Status.PRESENT
                attendance.save(update_fields=['status'])
        else:
            Attendance.objects.create(
                live_class=live_class,
                student=user,
                student_name=student_display_name,
                status=Attendance.Status.PRESENT,
                joined_at=now,
            )

        recent_chat_messages = live_class.chat_messages.order_by('created_at')[:50]
        active_participants = live_class.participants.filter(
            status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED]
        ).order_by('-is_hand_raised', 'hand_raised_at', '-join_time')

        return render(request, 'classrooms/student_live.html', {
            'live_class': live_class,
            'is_teacher_owner': False,
            'recent_chat_messages': recent_chat_messages,
            'active_participants': active_participants,
            'user_participant': participant,
            'is_hand_raised': bool(participant.is_hand_raised) if participant else False,
            'is_muted': bool(participant.is_muted) if participant else False,
            'is_livekit_configured': is_livekit_configured(),
            'page_title': f"Live Classroom: {live_class.title} - TeachLive",
        })


# =====================================================================
# 5. Teacher End Class Action
# =====================================================================

@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class LiveClassEndView(View):
    """
    Ends the live class. Restricted strictly to the teacher owner (or admin).
    Students attempting to call this will receive HTTP 403 Forbidden.
    Updates LiveClass status to COMPLETED, ends participant sessions and attendance.
    """
    def post(self, request, room_code=None, class_id=None):
        live_class = None
        if room_code:
            live_class = get_object_or_404(LiveClass, room_code__iexact=room_code.strip())
        elif class_id:
            live_class = get_object_or_404(LiveClass, pk=class_id)

        user = request.user
        # Strict Ownership Check: Students and other teachers must never end class
        if user != live_class.teacher and not user.is_admin_role:
            return HttpResponseForbidden("Forbidden: Only the teacher who owns this class can end it.")

        now = timezone.now()
        live_class.end_class()

        # Update active participants to LEFT
        live_class.participants.filter(
            status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED]
        ).update(
            status=ClassParticipant.Status.LEFT,
            leave_time=now
        )

        # Update active attendance records
        for att in live_class.attendances.filter(left_at__isnull=True):
            att.left_at = now
            att.status = Attendance.Status.LEFT
            delta = now - att.joined_at
            att.total_duration = max(1, int(round(delta.total_seconds() / 60)))
            att.save(update_fields=['left_at', 'status', 'total_duration'])

        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('accept', ''):
            return JsonResponse({'status': 'COMPLETED', 'message': 'Live class ended successfully.'})

        messages.success(request, f"Live class '{live_class.title}' has concluded successfully.")
        return redirect('accounts:teacher_dashboard')


# =====================================================================
# 6. Real-Time In-Room Chat Endpoints
# =====================================================================

@method_decorator(login_required(login_url='student:login'), name='dispatch')
class LiveClassChatAPIView(View):
    """
    Real-time in-room chat message API.
    GET: Returns list of recent room messages (latest 50).
    POST: Verifies participant authorization, verifies class is LIVE,
          enforces 500-char limit, rate limits (max 5 msgs / 10s per user -> 429),
          escapes HTML, assigns role server-side, and stores in ChatMessage.
    """
    def get(self, request, room_code):
        clean_code = room_code.strip()
        live_class = get_object_or_404(LiveClass, room_code__iexact=clean_code)

        user = request.user
        is_teacher = (user == live_class.teacher or user.is_admin_role)
        is_participant = live_class.participants.filter(user=user).exists()
        if not is_teacher and not is_participant:
            return JsonResponse({'error': 'You are not an authorized participant of this classroom.'}, status=403)

        messages_qs = live_class.chat_messages.order_by('created_at')[:50]
        data = [
            {
                'id': m.id,
                'sender_name': m.sender_name,
                'sender_role': m.sender_role,
                'message': m.message,
                'created_at': m.created_at.strftime('%H:%M'),
            }
            for m in messages_qs
        ]
        return JsonResponse({'messages': data})

    def post(self, request, room_code):
        clean_code = room_code.strip()
        live_class = get_object_or_404(LiveClass, room_code__iexact=clean_code)

        user = request.user
        # 1. Authorization check
        is_teacher = (user == live_class.teacher or user.is_admin_role)
        if not is_teacher:
            is_authorized, auth_err = check_student_class_access(live_class, user)
            if not is_authorized:
                return JsonResponse({'error': auth_err}, status=403)

        is_participant = live_class.participants.filter(user=user).exists()
        if not is_teacher and not is_participant:
            return JsonResponse({'error': 'You are not an authorized participant of this classroom.'}, status=403)

        # 2. Verify class is LIVE
        if live_class.status != LiveClass.Status.LIVE:
            return JsonResponse({'error': 'Chat ended because the live class has ended.'}, status=400)

        # 3. Rate limiting: max 5 messages within 10 seconds per user
        cache_key = f"chat_rate_{live_class.id}_{user.id}"
        now_ts = timezone.now().timestamp()
        timestamps = cache.get(cache_key, [])
        recent = [ts for ts in timestamps if now_ts - ts < 10]
        if len(recent) >= 5:
            return JsonResponse({'error': 'Please slow down.'}, status=429)

        raw_message = ''
        if request.content_type == 'application/json':
            try:
                body = json.loads(request.body)
                raw_message = str(body.get('message', '')).strip()
            except Exception:
                return JsonResponse({'error': 'Invalid JSON body.'}, status=400)
        else:
            raw_message = request.POST.get('message', '').strip()

        # 4. Reject empty messages
        if not raw_message:
            return JsonResponse({'error': 'Message cannot be empty.'}, status=400)

        # 5. Message length limit (500 chars)
        if len(raw_message) > 500:
            return JsonResponse({'error': 'Message exceeds 500 characters limit.'}, status=400)

        # Record rate limit
        recent.append(now_ts)
        cache.set(cache_key, recent, timeout=15)

        # 6. Escape HTML to prevent XSS
        safe_message = escape(raw_message)

        # 7. Server-determined sender role
        role = ChatMessage.SenderRole.STUDENT
        if user == live_class.teacher:
            role = ChatMessage.SenderRole.TEACHER
        elif user.is_admin_role:
            role = ChatMessage.SenderRole.ADMIN

        sender_display_name = user.get_full_name() or user.username

        msg = ChatMessage.objects.create(
            live_class=live_class,
            sender=user,
            sender_name=sender_display_name,
            sender_role=role,
            message=safe_message,
        )

        return JsonResponse({
            'success': True,
            'message': {
                'id': msg.id,
                'sender_name': msg.sender_name,
                'sender_role': msg.sender_role,
                'message': msg.message,
                'created_at': msg.created_at.strftime('%H:%M'),
            }
        })


# =====================================================================
# 7. Live Status & Participants Polling API
# =====================================================================

class LiveClassStatusAPIView(View):
    """
    Polling endpoint returning live status and active participants.
    Prioritizes:
    1. Raised hand students (ordered by hand_raised_at)
    2. Active speaking / other participants (ordered by join_time or name)
    Includes mic, camera, connection status, and hand-raise status.
    """
    def get(self, request, room_code):
        clean_code = room_code.strip()
        live_class = get_object_or_404(LiveClass, room_code__iexact=clean_code)

        participants_qs = live_class.participants.filter(
            status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED]
        ).select_related('user').order_by('-is_hand_raised', 'hand_raised_at', 'student_name')

        participants_data = []
        for p in participants_qs[:100]:
            name = p.student_name
            initials = name[0].upper() if name else 'S'
            participants_data.append({
                'id': p.user_id if p.user else None,
                'student_name': name,
                'initials': initials,
                'join_time': p.join_time.strftime('%H:%M') if p.join_time else '',
                'is_hand_raised': bool(p.is_hand_raised),
                'hand_raised_at': p.hand_raised_at.strftime('%H:%M') if p.hand_raised_at else '',
                'is_muted': bool(p.is_muted),
                'status': p.status,
            })

        return JsonResponse({
            'room_code': live_class.room_code,
            'status': live_class.status,
            'is_live': live_class.is_live,
            'active_count': len(participants_data),
            'participants': participants_data,
        })


# =====================================================================
# 8. Student Leave View
# =====================================================================

@method_decorator(login_required(login_url='student:login'), name='dispatch')
class LiveClassLeaveView(View):
    """
    Handles student leaving the live class at /live/<room_code>/leave/.
    Updates ClassParticipant to LEFT and records Attendance total_duration.
    """
    def post(self, request, room_code):
        return self._leave(request, room_code)

    def get(self, request, room_code):
        return self._leave(request, room_code)

    def _leave(self, request, room_code):
        clean_code = room_code.strip()
        live_class = LiveClass.objects.filter(room_code__iexact=clean_code).first()

        if live_class:
            now = timezone.now()
            user = request.user
            student_display_name = user.get_full_name() or user.username

            # Update Participant
            participant = ClassParticipant.objects.filter(
                live_class=live_class,
                user=user
            ).first()
            if not participant and user.email:
                participant = ClassParticipant.objects.filter(
                    live_class=live_class,
                    student_email__iexact=user.email
                ).first()

            if participant:
                participant.leave_time = now
                participant.status = ClassParticipant.Status.LEFT
                participant.save()

            # Update Attendance duration
            attendance = Attendance.objects.filter(
                live_class=live_class,
                student=user
            ).first()
            if not attendance:
                attendance = Attendance.objects.filter(
                    live_class=live_class,
                    student_name=student_display_name
                ).first()

            if attendance:
                attendance.left_at = now
                attendance.status = Attendance.Status.LEFT
                delta = now - attendance.joined_at
                duration_mins = max(1, int(round(delta.total_seconds() / 60)))
                attendance.total_duration = duration_mins
                attendance.save()

            # Clear session flag
            request.session.pop(f'in_live_class_{live_class.room_code}', None)

            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('accept', ''):
                return JsonResponse({'status': 'LEFT', 'message': 'You have exited the live class.'})

            if request.user.is_teacher:
                messages.info(request, "You have exited the live class.")
                return redirect('accounts:teacher_dashboard')

            return render(request, 'classrooms/live_left.html', {
                'live_class': live_class,
                'attendance': attendance,
                'page_title': 'Class Session Ended - TeachLive',
            })

        messages.info(request, "You have exited the live class.")
        return redirect('student:dashboard')


# =====================================================================
# 9. Classroom Heartbeat & Inactivity Tracking API
# =====================================================================

@method_decorator(login_required(login_url='student:login'), name='dispatch')
class LiveClassHeartbeatAPIView(View):
    """
    Heartbeat endpoint called every 20-30s by active classroom clients.
    URL: /live/<room_code>/heartbeat/
    Updates calling participant's last_seen_at and cleans up inactive participants (>75s),
    marking them DISCONNECTED and computing attendance duration.
    """
    def post(self, request, room_code):
        return self._handle_heartbeat(request, room_code)

    def get(self, request, room_code):
        return self._handle_heartbeat(request, room_code)

    def _handle_heartbeat(self, request, room_code):
        clean_code = room_code.strip()
        live_class = get_object_or_404(LiveClass, room_code__iexact=clean_code)

        if live_class.status != LiveClass.Status.LIVE:
            return JsonResponse({
                'status': 'ended',
                'is_live': False,
                'message': 'Chat ended because the live class has ended.'
            })

        user = request.user
        is_teacher = (user == live_class.teacher or user.is_admin_role)
        if not is_teacher:
            is_authorized, auth_err = check_student_class_access(live_class, user)
            if not is_authorized:
                return JsonResponse({
                    'status': 'REVOKED',
                    'is_live': False,
                    'error': auth_err or 'Access revoked.'
                }, status=403)

        now = timezone.now()

        # Update calling participant's last_seen_at
        participant = ClassParticipant.objects.filter(live_class=live_class, user=user).first()
        if participant:
            participant.last_seen_at = now
            if participant.status not in (ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED):
                participant.status = ClassParticipant.Status.ACTIVE
                participant.leave_time = None
            participant.save(update_fields=['last_seen_at', 'status', 'leave_time'])

            # Keep attendance in PRESENT state if not ended
            att = Attendance.objects.filter(live_class=live_class, student=user, left_at__isnull=True).first()
            if att and att.status != Attendance.Status.PRESENT:
                att.status = Attendance.Status.PRESENT
                att.save(update_fields=['status'])

        # Inactivity cleanup: sweep participants inactive for > 75 seconds
        stale_threshold = now - timedelta(seconds=75)
        stale_participants = ClassParticipant.objects.filter(
            live_class=live_class,
            status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED],
            last_seen_at__lt=stale_threshold
        )

        for sp in stale_participants:
            sp.status = ClassParticipant.Status.DISCONNECTED
            sp.leave_time = sp.last_seen_at or now
            sp.save(update_fields=['status', 'leave_time'])

            # Update Attendance
            open_att = Attendance.objects.filter(
                live_class=live_class,
                student=sp.user,
                left_at__isnull=True
            ).first()
            if not open_att and sp.student_name:
                open_att = Attendance.objects.filter(
                    live_class=live_class,
                    student_name=sp.student_name,
                    left_at__isnull=True
                ).first()

            if open_att:
                open_att.status = Attendance.Status.DISCONNECTED
                open_att.left_at = sp.last_seen_at or now
                delta = open_att.left_at - open_att.joined_at
                open_att.total_duration = max(1, int(round(delta.total_seconds() / 60)))
                open_att.save(update_fields=['status', 'left_at', 'total_duration'])

        return JsonResponse({
            'status': 'ok',
            'is_live': live_class.is_live,
            'timestamp': now.isoformat()
        })


# =====================================================================
# 10. Student Access Request View (For Enrollment-Only Classes)
# =====================================================================

@method_decorator(login_required(login_url='student:login'), name='dispatch')
class LiveClassRequestAccessView(View):
    """
    POST action for students to submit an access request to an ENROLLMENT_ONLY class.
    URL: /live/<room_code>/request-access/
    """
    def post(self, request, room_code):
        clean_code = room_code.strip()
        live_class = get_object_or_404(LiveClass, room_code__iexact=clean_code)
        user = request.user

        if not user.is_active:
            raise PermissionDenied("Access Denied: Your account has been deactivated.")

        if live_class.access_mode != LiveClass.AccessMode.ENROLLMENT_ONLY:
            messages.info(request, "This class does not require an access request.")
            return redirect('live_room:detail', room_code=live_class.room_code)

        # Check if student is already actively enrolled
        enrollment = live_class.enrollments.filter(student=user).first()
        if enrollment and enrollment.status in (ClassEnrollment.Status.ENROLLED, ClassEnrollment.Status.INVITED):
            messages.info(request, "You are already enrolled in this class.")
            return redirect('live_room:detail', room_code=live_class.room_code)

        # Check if request already pending
        existing_req = live_class.access_requests.filter(
            student=user,
            status=ClassAccessRequest.Status.PENDING
        ).first()

        note = request.POST.get('request_note', '').strip()[:500]

        if existing_req:
            messages.info(request, "Your access request has already been submitted and is pending instructor review.")
        else:
            ClassAccessRequest.objects.create(
                live_class=live_class,
                student=user,
                status=ClassAccessRequest.Status.PENDING,
                request_note=note
            )
            messages.success(request, "Your access request has been sent to the instructor.")
            try:
                NotificationService.notify_access_requested(live_class, user, note)
            except Exception as exc:
                logger.error("Failed to notify teacher of access request: %s", exc)

        return redirect('live_room:detail', room_code=live_class.room_code)


# =====================================================================
# 11. Live Classroom Moderation Controls (Teacher Host Control)
# =====================================================================

@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class LiveClassModerationAPIView(View):
    """
    Teacher moderation controls: mute participant, remove participant, lower participant's hand.
    URL: /live/<room_code>/moderate/
    Methods: POST
    Strictly restricted to the teacher who owns this live class (or admin).
    """
    def post(self, request, room_code):
        clean_code = room_code.strip()
        live_class = get_object_or_404(LiveClass, room_code__iexact=clean_code)
        user = request.user

        # 1. Authorization: Only teacher owner of this live class (or admin) can moderate
        if user != live_class.teacher and not user.is_admin_role:
            return JsonResponse({'error': 'Forbidden: You are not authorized to moderate this live class.'}, status=403)

        # 2. LiveClass status check
        if live_class.status != LiveClass.Status.LIVE:
            return JsonResponse({'error': 'Moderation controls are only active while the class is live.'}, status=400)

        # 3. Parse payload (supports JSON or form-data)
        data = {}
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body)
            except Exception:
                return JsonResponse({'error': 'Invalid JSON body.'}, status=400)
        else:
            data = request.POST

        action = str(data.get('action', '')).strip().lower()
        student_id = data.get('student_id')
        reason = str(data.get('reason', '')).strip()[:255]

        valid_actions = ['mute', 'remove', 'lower_hand']
        if action not in valid_actions:
            return JsonResponse({'error': f"Invalid action '{action}'. Valid actions: {', '.join(valid_actions)}."}, status=400)

        if not student_id:
            return JsonResponse({'error': 'student_id is required.'}, status=400)

        # 4. Target student validation
        from django.contrib.auth import get_user_model
        UserModel = get_user_model()
        try:
            target_student = UserModel.objects.get(pk=student_id)
        except UserModel.DoesNotExist:
            return JsonResponse({'error': 'Target student not found.'}, status=404)

        if target_student == live_class.teacher or target_student.is_admin_role:
            return JsonResponse({'error': 'Cannot moderate the teacher or administrator.'}, status=403)

        now = timezone.now()

        if action == 'mute':
            ClassParticipant.objects.filter(
                live_class=live_class,
                user=target_student
            ).update(is_muted=True)

            LiveClassParticipantModeration.objects.create(
                live_class=live_class,
                student=target_student,
                action=LiveClassParticipantModeration.Action.MUTED,
                reason=reason,
                created_by=user,
                active=True,
            )

            return JsonResponse({
                'success': True,
                'action': 'MUTED',
                'student_id': target_student.id,
                'student_name': target_student.get_full_name() or target_student.username,
                'message': f"Student {target_student.get_full_name() or target_student.username} has been muted.",
            })

        elif action == 'remove':
            # Update participant status to KICKED
            ClassParticipant.objects.filter(
                live_class=live_class,
                user=target_student
            ).update(
                status=ClassParticipant.Status.KICKED,
                leave_time=now,
                is_hand_raised=False,
                hand_raised_at=None,
            )

            # Close open Attendance record and calculate total duration
            open_attendances = live_class.attendances.filter(student=target_student, left_at__isnull=True)
            for att in open_attendances:
                att.left_at = now
                att.status = Attendance.Status.LEFT
                delta = now - att.joined_at
                att.total_duration = max(1, int(round(delta.total_seconds() / 60)))
                att.save(update_fields=['left_at', 'status', 'total_duration'])

            # Create active session-level removal restriction
            LiveClassParticipantModeration.objects.create(
                live_class=live_class,
                student=target_student,
                action=LiveClassParticipantModeration.Action.REMOVED,
                reason=reason,
                created_by=user,
                active=True,
            )

            return JsonResponse({
                'success': True,
                'action': 'REMOVED',
                'student_id': target_student.id,
                'student_name': target_student.get_full_name() or target_student.username,
                'message': f"Student {target_student.get_full_name() or target_student.username} has been removed from the live class.",
            })

        elif action == 'lower_hand':
            ClassParticipant.objects.filter(
                live_class=live_class,
                user=target_student
            ).update(
                is_hand_raised=False,
                hand_raised_at=None,
            )

            LiveClassParticipantModeration.objects.create(
                live_class=live_class,
                student=target_student,
                action=LiveClassParticipantModeration.Action.HAND_LOWERED,
                reason=reason,
                created_by=user,
                active=False,
            )

            return JsonResponse({
                'success': True,
                'action': 'HAND_LOWERED',
                'student_id': target_student.id,
                'student_name': target_student.get_full_name() or target_student.username,
                'message': f"Lowered hand for {target_student.get_full_name() or target_student.username}.",
            })


# =====================================================================
# 12. Raise / Lower Hand API
# =====================================================================

@method_decorator(login_required(login_url='student:login'), name='dispatch')
class LiveClassHandToggleAPIView(View):
    """
    Raise Hand / Lower Hand API endpoint.
    URL: /live/<room_code>/hand/
    Methods: POST
    Students can raise or lower their own hand.
    Teachers can lower any student's hand.
    Rate limited to avoid spamming (max 5 toggles / 10s).
    """
    def post(self, request, room_code):
        clean_code = room_code.strip()
        live_class = get_object_or_404(LiveClass, room_code__iexact=clean_code)
        user = request.user

        if live_class.status != LiveClass.Status.LIVE:
            return JsonResponse({'error': 'Class is not live.'}, status=400)

        # Rate limiting: max 5 hand actions within 10 seconds per user
        cache_key = f"hand_rate_{live_class.id}_{user.id}"
        now_ts = timezone.now().timestamp()
        timestamps = cache.get(cache_key, [])
        recent = [ts for ts in timestamps if now_ts - ts < 10]
        if len(recent) >= 5:
            return JsonResponse({'error': 'Please slow down.'}, status=429)

        data = {}
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body)
            except Exception:
                return JsonResponse({'error': 'Invalid JSON body.'}, status=400)
        else:
            data = request.POST

        action = str(data.get('action', '')).strip().lower()
        if action not in ['raise', 'lower']:
            return JsonResponse({'error': "Action must be 'raise' or 'lower'."}, status=400)

        is_teacher = (user == live_class.teacher or user.is_admin_role)

        # Determine target student
        target_student_id = data.get('student_id')
        if target_student_id and is_teacher:
            from django.contrib.auth import get_user_model
            UserModel = get_user_model()
            try:
                target_user = UserModel.objects.get(pk=target_student_id)
            except UserModel.DoesNotExist:
                return JsonResponse({'error': 'Target student not found.'}, status=404)
        else:
            if target_student_id and int(target_student_id) != user.id:
                return JsonResponse({'error': "Students cannot modify other participants' hand status."}, status=403)
            target_user = user

        # Check target is participant
        participant = ClassParticipant.objects.filter(live_class=live_class, user=target_user).first()
        if not participant:
            return JsonResponse({'error': 'Participant record not found.'}, status=404)

        recent.append(now_ts)
        cache.set(cache_key, recent, timeout=15)

        now = timezone.now()

        if action == 'raise':
            participant.is_hand_raised = True
            participant.hand_raised_at = now
            participant.save(update_fields=['is_hand_raised', 'hand_raised_at'])

            LiveClassParticipantModeration.objects.create(
                live_class=live_class,
                student=target_user,
                action=LiveClassParticipantModeration.Action.HAND_RAISED,
                created_by=user,
                active=True,
            )

            return JsonResponse({
                'success': True,
                'action': 'HAND_RAISED',
                'is_hand_raised': True,
                'student_id': target_user.id,
                'student_name': target_user.get_full_name() or target_user.username,
            })

        else: # lower
            participant.is_hand_raised = False
            participant.hand_raised_at = None
            participant.save(update_fields=['is_hand_raised', 'hand_raised_at'])

            LiveClassParticipantModeration.objects.create(
                live_class=live_class,
                student=target_user,
                action=LiveClassParticipantModeration.Action.HAND_LOWERED,
                created_by=user,
                active=False,
            )

            return JsonResponse({
                'success': True,
                'action': 'HAND_LOWERED',
                'is_hand_raised': False,
                'student_id': target_user.id,
                'student_name': target_user.get_full_name() or target_user.username,
            })


# =====================================================================
# 13. Removed Student View
# =====================================================================

@method_decorator(login_required(login_url='student:login'), name='dispatch')
class LiveClassRemovedView(View):
    """
    Displays clean notification to a student who has been removed from the live class by the teacher.
    URL: /live/<room_code>/removed/
    """
    def get(self, request, room_code):
        clean_code = room_code.strip()
        live_class = get_object_or_404(LiveClass, room_code__iexact=clean_code)

        # Clear session flag
        request.session.pop(f'in_live_class_{live_class.room_code}', None)

        return render(request, 'classrooms/live_removed.html', {
            'live_class': live_class,
            'page_title': f"Removed from Class: {live_class.title} - TeachLive",
        })

