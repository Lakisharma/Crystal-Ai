"""
Class enrollment and student management views for instructors and administrators.
Handles viewing enrolled students, adding/inviting students, revoking access,
re-inviting removed students, generating secure class invite links,
reviewing student access requests, and teacher student management dashboards.
"""

import logging
from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from accounts.models import AdminAuditLog, User, log_admin_action
from accounts.permissions import TeacherRequiredMixin
from classrooms.models import (
    Attendance,
    ClassAccessRequest,
    ClassEnrollment,
    ClassInviteToken,
    ClassParticipant,
    LiveClass,
    LiveClassParticipantModeration,
)
from notifications.services import NotificationService

logger = logging.getLogger(__name__)


def check_teacher_or_admin_ownership(user, live_class):
    """Verifies that the user is the instructor who owns the class or an administrator."""
    if not (user == live_class.teacher or user.is_admin_role):
        raise PermissionDenied("You do not have permission to manage students for this class.")


def get_live_class_by_identifier(class_id=None, room_code=None):
    """Retrieves LiveClass by either integer class_id or room_code string."""
    if class_id is not None:
        return get_object_or_404(
            LiveClass.objects.select_related('teacher'),
            pk=class_id
        )
    elif room_code is not None:
        clean_code = str(room_code).strip()
        return get_object_or_404(
            LiveClass.objects.select_related('teacher'),
            room_code__iexact=clean_code
        )
    raise PermissionDenied("Missing class identifier.")


# =====================================================================
# 1. Teacher Class Students Management View
# =====================================================================

@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherClassStudentsView(View):
    """
    Teacher class student management at:
    - /teacher/classes/<int:class_id>/students/
    - /teacher/classes/<str:room_code>/students/
    Displays Active Students, Pending Invitations, Pending Access Requests,
    Removed / Revoked Students, Capacity limit, and Invite Link tools.
    """
    def get(self, request, class_id=None, room_code=None):
        live_class = get_live_class_by_identifier(class_id, room_code)
        check_teacher_or_admin_ownership(request.user, live_class)

        search_query = request.GET.get('q', '').strip()
        active_tab = request.GET.get('tab', 'active').strip().lower()

        base_enrollments = (
            live_class.enrollments
            .select_related('student')
            .order_by('-created_at')
        )

        if search_query:
            base_enrollments = base_enrollments.filter(
                Q(student__email__icontains=search_query) |
                Q(student__first_name__icontains=search_query) |
                Q(student__last_name__icontains=search_query) |
                Q(student__username__icontains=search_query)
            )

        # Separate QuerySets for each section
        active_enrollments = base_enrollments.filter(status=ClassEnrollment.Status.ENROLLED)
        invited_enrollments = base_enrollments.filter(status=ClassEnrollment.Status.INVITED)
        revoked_enrollments = base_enrollments.filter(status=ClassEnrollment.Status.REVOKED)

        # Pending access requests
        access_requests_qs = (
            live_class.access_requests
            .filter(status=ClassAccessRequest.Status.PENDING)
            .select_related('student')
            .order_by('-created_at')
        )
        if search_query:
            access_requests_qs = access_requests_qs.filter(
                Q(student__email__icontains=search_query) |
                Q(student__first_name__icontains=search_query) |
                Q(student__last_name__icontains=search_query) |
                Q(student__username__icontains=search_query)
            )

        # Fast attendance lookup for this class
        attendances = Attendance.objects.filter(live_class=live_class)
        attendance_by_student = {a.student_id: a for a in attendances}

        for e in active_enrollments:
            e.attendance_record = attendance_by_student.get(e.student_id)
        for e in invited_enrollments:
            e.attendance_record = attendance_by_student.get(e.student_id)
        for e in revoked_enrollments:
            e.attendance_record = attendance_by_student.get(e.student_id)

        # Counts
        total_enrolled = live_class.enrollments.filter(status=ClassEnrollment.Status.ENROLLED).count()
        total_invited = live_class.enrollments.filter(status=ClassEnrollment.Status.INVITED).count()
        total_revoked = live_class.enrollments.filter(status=ClassEnrollment.Status.REVOKED).count()
        total_pending_requests = live_class.access_requests.filter(status=ClassAccessRequest.Status.PENDING).count()
        available_seats = max(0, live_class.max_students - total_enrolled)

        # Get or create active invite token for share link
        invite_token_obj = live_class.get_or_create_invite_token(request.user)
        invite_url = request.build_absolute_uri(
            reverse('student:invite', kwargs={'token': invite_token_obj.token})
        )

        return render(request, 'classrooms/teacher_manage_students.html', {
            'live_class': live_class,
            'active_enrollments': active_enrollments,
            'invited_enrollments': invited_enrollments,
            'revoked_enrollments': revoked_enrollments,
            'access_requests': access_requests_qs,
            'search_query': search_query,
            'active_tab': active_tab,
            'total_enrolled': total_enrolled,
            'total_invited': total_invited,
            'total_revoked': total_revoked,
            'total_pending_requests': total_pending_requests,
            'available_seats': available_seats,
            'invite_token': invite_token_obj,
            'invite_url': invite_url,
            'page_title': f"Manage Students: {live_class.title} - TeachLive",
        })


# =====================================================================
# 2. Add / Invite Student to Class Action
# =====================================================================

@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherAddStudentView(View):
    """
    POST action to add/invite an existing registered student to a LiveClass.
    Searches by email or username, creates/updates ClassEnrollment, and dispatches notifications/emails.
    """
    def post(self, request, class_id=None, room_code=None):
        live_class = get_live_class_by_identifier(class_id, room_code)
        check_teacher_or_admin_ownership(request.user, live_class)

        student_query = request.POST.get('student_query', '').strip()
        action_type = request.POST.get('action_type', 'ENROLL').upper()  # 'ENROLL' or 'INVITE'

        redirect_url = reverse('classrooms:teacher_class_students', kwargs={'room_code': live_class.room_code})

        if not student_query:
            messages.error(request, "Please enter a student email address or username.")
            return redirect(redirect_url)

        # Find student by exact email or username
        student = User.objects.filter(
            Q(email__iexact=student_query) | Q(username__iexact=student_query)
        ).first()

        if not student:
            messages.error(
                request,
                f"No registered user found with '{student_query}'. "
                "Students must first register on TeachLive before they can be added or invited to classes."
            )
            return redirect(redirect_url)

        if student.role != User.Role.STUDENT and not student.is_student:
            messages.warning(
                request,
                f"Account '{student.username}' is registered as an {student.get_role_display()}, not a student."
            )
            return redirect(redirect_url)

        # Check capacity
        if live_class.max_students > 0:
            current_count = live_class.enrollments.filter(
                status__in=[ClassEnrollment.Status.ENROLLED, ClassEnrollment.Status.INVITED]
            ).exclude(student=student).count()
            if current_count >= live_class.max_students:
                messages.error(request, f"Cannot enroll {student.username}: Class capacity ({live_class.max_students}) has been reached.")
                return redirect(redirect_url)

        target_status = ClassEnrollment.Status.INVITED if action_type == 'INVITE' else ClassEnrollment.Status.ENROLLED
        now = timezone.now()

        with transaction.atomic():
            enrollment, created = ClassEnrollment.objects.get_or_create(
                live_class=live_class,
                student=student,
                defaults={
                    'status': target_status,
                    'invited_at': now if action_type == 'INVITE' else None,
                    'enrolled_at': now if action_type == 'ENROLL' else None,
                }
            )

            if not created:
                if enrollment.status == ClassEnrollment.Status.REVOKED:
                    enrollment.status = target_status
                    enrollment.revoked_at = None
                    enrollment.revocation_reason = ''
                    if action_type == 'INVITE':
                        enrollment.invited_at = now
                    else:
                        enrollment.enrolled_at = now
                    enrollment.save()
                    # Deactivate any active moderation removal
                    LiveClassParticipantModeration.objects.filter(
                        live_class=live_class,
                        student=student,
                        action=LiveClassParticipantModeration.Action.REMOVED
                    ).update(active=False)
                    messages.success(request, f"Re-enrolled student {student.get_full_name() or student.username} into class.")
                elif enrollment.status == ClassEnrollment.Status.INVITED and action_type == 'ENROLL':
                    enrollment.status = ClassEnrollment.Status.ENROLLED
                    enrollment.enrolled_at = now
                    enrollment.save()
                    messages.success(request, f"Upgraded {student.get_full_name() or student.username} from Invited to Enrolled.")
                else:
                    messages.info(request, f"Student {student.get_full_name() or student.username} is already {enrollment.get_status_display().lower()} in this class.")
                    return redirect(redirect_url)
            else:
                verb = "invited to" if action_type == 'INVITE' else "enrolled in"
                messages.success(request, f"Successfully {verb} {student.get_full_name() or student.username}.")

            # Clean up any pending access request for this student
            ClassAccessRequest.objects.filter(
                live_class=live_class,
                student=student,
                status=ClassAccessRequest.Status.PENDING
            ).update(
                status=ClassAccessRequest.Status.APPROVED,
                reviewed_at=now,
                reviewed_by=request.user
            )

        # Dispatch notification & email safely
        try:
            NotificationService.notify_student_invited(live_class, student)
        except Exception as exc:
            logger.error("Failed to notify invited student: %s", exc)

        audit_action = AdminAuditLog.Action.STUDENT_INVITED if action_type == 'INVITE' else AdminAuditLog.Action.ENROLLMENT_CREATED
        log_admin_action(
            admin=request.user,
            action=audit_action,
            target_type="LiveClass",
            target_id=str(live_class.id),
            description=f"{action_type.capitalize()} student {student.username} into {live_class.room_code}",
            request=request
        )

        return redirect(redirect_url)


# =====================================================================
# 3. Revoke Student Access Action (with Reason & Live Kick)
# =====================================================================

@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherRevokeStudentView(View):
    """
    POST action to revoke a student's enrollment access to a LiveClass.
    Updates enrollment status to REVOKED, stores optional reason,
    immediately kicks active session participants, creates moderation block,
    dispatches notification/email, and logs audit trail.
    """
    def post(self, request, enrollment_id, class_id=None, room_code=None):
        live_class = get_live_class_by_identifier(class_id, room_code)
        check_teacher_or_admin_ownership(request.user, live_class)

        enrollment = get_object_or_404(ClassEnrollment, pk=enrollment_id, live_class=live_class)
        student = enrollment.student
        reason = request.POST.get('reason', '').strip()
        now = timezone.now()

        with transaction.atomic():
            enrollment.status = ClassEnrollment.Status.REVOKED
            enrollment.revoked_at = now
            enrollment.revocation_reason = reason
            enrollment.save(update_fields=['status', 'revoked_at', 'revocation_reason', 'updated_at'])

            # 1. If student is currently active in live classroom, mark as KICKED
            ClassParticipant.objects.filter(
                live_class=live_class,
                user=student,
                status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED]
            ).update(
                status=ClassParticipant.Status.KICKED,
                leave_time=now
            )

            # 2. Add session-level moderation restriction so heartbeat/token immediately blocks them
            LiveClassParticipantModeration.objects.create(
                live_class=live_class,
                student=student,
                action=LiveClassParticipantModeration.Action.REMOVED,
                reason=reason or "Access revoked by teacher",
                active=True,
                created_by=request.user
            )

        messages.warning(request, f"Revoked access for {student.get_full_name() or student.username}.")

        # Send notification safely
        try:
            NotificationService.notify_student_removed(live_class, student, reason)
        except Exception as exc:
            logger.error("Failed to send revocation notice: %s", exc)

        log_admin_action(
            admin=request.user,
            action=AdminAuditLog.Action.ENROLLMENT_REVOKED,
            target_type="LiveClass",
            target_id=str(live_class.id),
            description=f"Revoked enrollment for student {student.username} in {live_class.room_code}. Reason: {reason or 'None'}",
            request=request
        )

        return redirect('classrooms:teacher_class_students', room_code=live_class.room_code)


# =====================================================================
# 4. Restore / Re-invite Student Action
# =====================================================================

@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherRestoreStudentView(View):
    """
    POST action to restore or re-invite a previously revoked student.
    Reuses existing ClassEnrollment record, preserves historical attendance,
    deactivates moderation removal blocks, sends notification, and logs audit trail.
    """
    def post(self, request, enrollment_id, class_id=None, room_code=None):
        live_class = get_live_class_by_identifier(class_id, room_code)
        check_teacher_or_admin_ownership(request.user, live_class)

        enrollment = get_object_or_404(ClassEnrollment, pk=enrollment_id, live_class=live_class)
        student = enrollment.student
        action_type = request.POST.get('action_type', 'ENROLL').upper()  # 'ENROLL' or 'INVITE'
        now = timezone.now()

        with transaction.atomic():
            if action_type == 'INVITE':
                enrollment.status = ClassEnrollment.Status.INVITED
                enrollment.invited_at = now
            else:
                enrollment.status = ClassEnrollment.Status.ENROLLED
                enrollment.enrolled_at = now

            enrollment.revoked_at = None
            enrollment.revocation_reason = ''
            enrollment.save(update_fields=['status', 'revoked_at', 'enrolled_at', 'invited_at', 'revocation_reason', 'updated_at'])

            # Deactivate active removal moderation events
            LiveClassParticipantModeration.objects.filter(
                live_class=live_class,
                student=student,
                action=LiveClassParticipantModeration.Action.REMOVED
            ).update(active=False)

        verb = "Re-invited" if action_type == 'INVITE' else "Restored active access for"
        messages.success(request, f"{verb} {student.get_full_name() or student.username}.")

        try:
            if action_type == 'INVITE':
                NotificationService.notify_student_reinvited(live_class, student)
            else:
                NotificationService.notify_enrollment_approved(live_class, student)
        except Exception as exc:
            logger.error("Failed to send re-invite/restoration notice: %s", exc)

        log_admin_action(
            admin=request.user,
            action=AdminAuditLog.Action.STUDENT_REINVITED,
            target_type="LiveClass",
            target_id=str(live_class.id),
            description=f"Re-invited/Restored student {student.username} into {live_class.room_code}",
            request=request
        )

        return redirect('classrooms:teacher_class_students', room_code=live_class.room_code)


# =====================================================================
# 5. Approve & Reject Access Requests
# =====================================================================

@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherApproveAccessRequestView(View):
    """
    POST action to approve a student's pending access request.
    Creates or updates ClassEnrollment to ENROLLED, verifies capacity,
    and sends notification/email.
    """
    def post(self, request, request_id, class_id=None, room_code=None):
        live_class = get_live_class_by_identifier(class_id, room_code)
        check_teacher_or_admin_ownership(request.user, live_class)

        access_request = get_object_or_404(
            ClassAccessRequest,
            pk=request_id,
            live_class=live_class
        )
        student = access_request.student
        now = timezone.now()

        # Check capacity
        enrolled_count = live_class.enrollments.filter(status=ClassEnrollment.Status.ENROLLED).exclude(student=student).count()
        if enrolled_count >= live_class.max_students:
            messages.error(request, f"Cannot approve request: Class capacity ({live_class.max_students}) has been reached.")
            return redirect('classrooms:teacher_class_students', room_code=live_class.room_code)

        with transaction.atomic():
            access_request.status = ClassAccessRequest.Status.APPROVED
            access_request.reviewed_at = now
            access_request.reviewed_by = request.user
            access_request.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])

            enrollment, _ = ClassEnrollment.objects.get_or_create(
                live_class=live_class,
                student=student,
                defaults={
                    'status': ClassEnrollment.Status.ENROLLED,
                    'enrolled_at': now
                }
            )
            if enrollment.status != ClassEnrollment.Status.ENROLLED:
                enrollment.status = ClassEnrollment.Status.ENROLLED
                enrollment.revoked_at = None
                enrollment.revocation_reason = ''
                enrollment.enrolled_at = now
                enrollment.save(update_fields=['status', 'revoked_at', 'enrolled_at', 'revocation_reason', 'updated_at'])

        messages.success(request, f"Approved access request for {student.get_full_name() or student.username}.")

        try:
            NotificationService.notify_enrollment_approved(live_class, student)
        except Exception as exc:
            logger.error("Failed to send approval notice: %s", exc)

        log_admin_action(
            admin=request.user,
            action=AdminAuditLog.Action.ACCESS_REQUEST_APPROVED,
            target_type="LiveClass",
            target_id=str(live_class.id),
            description=f"Approved access request for student {student.username} in {live_class.room_code}",
            request=request
        )

        return redirect('classrooms:teacher_class_students', room_code=live_class.room_code)


@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherRejectAccessRequestView(View):
    """
    POST action to reject a student's pending access request.
    """
    def post(self, request, request_id, class_id=None, room_code=None):
        live_class = get_live_class_by_identifier(class_id, room_code)
        check_teacher_or_admin_ownership(request.user, live_class)

        access_request = get_object_or_404(
            ClassAccessRequest,
            pk=request_id,
            live_class=live_class
        )
        student = access_request.student
        now = timezone.now()

        access_request.status = ClassAccessRequest.Status.REJECTED
        access_request.reviewed_at = now
        access_request.reviewed_by = request.user
        access_request.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])

        messages.info(request, f"Rejected access request for {student.get_full_name() or student.username}.")

        try:
            NotificationService.notify_enrollment_rejected(live_class, student)
        except Exception as exc:
            logger.error("Failed to send rejection notice: %s", exc)

        log_admin_action(
            admin=request.user,
            action=AdminAuditLog.Action.ACCESS_REQUEST_REJECTED,
            target_type="LiveClass",
            target_id=str(live_class.id),
            description=f"Rejected access request for student {student.username} in {live_class.room_code}",
            request=request
        )

        return redirect('classrooms:teacher_class_students', room_code=live_class.room_code)


# =====================================================================
# 6. Secure Class Invite Token Management Views
# =====================================================================

@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherGenerateInviteLinkView(View):
    """
    POST endpoint to generate or regenerate a secure class invite link.
    Revokes any previous tokens when regenerate is requested.
    """
    def post(self, request, class_id=None, room_code=None):
        live_class = get_live_class_by_identifier(class_id, room_code)
        check_teacher_or_admin_ownership(request.user, live_class)

        regenerate = request.POST.get('regenerate') == '1'
        if regenerate:
            # Revoke existing tokens
            live_class.invite_tokens.filter(is_revoked=False).update(
                is_revoked=True,
                revoked_at=timezone.now()
            )

        token_obj = live_class.get_or_create_invite_token(request.user)
        full_invite_url = request.build_absolute_uri(
            reverse('student:class_invite', kwargs={'token': token_obj.token})
        )

        log_admin_action(
            admin=request.user,
            action=AdminAuditLog.Action.INVITE_LINK_GENERATED,
            target_type="LiveClass",
            target_id=str(live_class.id),
            description=f"Generated invite link for {live_class.room_code}",
            request=request
        )

        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('format') == 'json':
            return JsonResponse({
                'success': True,
                'token': token_obj.token,
                'invite_url': full_invite_url,
                'expires_at': token_obj.expires_at.isoformat() if token_obj.expires_at else None,
            })

        messages.success(request, "Secure class invite link ready.")
        return redirect('classrooms:teacher_class_students', room_code=live_class.room_code)


@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherRevokeInviteLinkView(View):
    """
    POST endpoint to revoke an active invite token.
    """
    def post(self, request, class_id=None, room_code=None):
        live_class = get_live_class_by_identifier(class_id, room_code)
        check_teacher_or_admin_ownership(request.user, live_class)

        now = timezone.now()
        live_class.invite_tokens.filter(is_revoked=False).update(
            is_revoked=True,
            revoked_at=now
        )

        log_admin_action(
            admin=request.user,
            action=AdminAuditLog.Action.INVITE_LINK_REVOKED,
            target_type="LiveClass",
            target_id=str(live_class.id),
            description=f"Revoked invite link for {live_class.room_code}",
            request=request
        )

        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('format') == 'json':
            return JsonResponse({'success': True, 'message': 'Invite link revoked.'})

        messages.info(request, "Invite link revoked. Prospective students can no longer use this link.")
        return redirect('classrooms:teacher_class_students', room_code=live_class.room_code)


# =====================================================================
# 7. Teacher — My Students Page (/teacher/students/)
# =====================================================================

@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherStudentsListView(TeacherRequiredMixin, View):
    """
    Teacher "My Students" dashboard at /teacher/students/.
    Displays:
    - Total Students
    - Active Students
    - Students in Live Classes
    - Pending Invitations
    - Pending Access Requests
    Supports server-side search (Name, Email), filters, pagination,
    attendance statistics, and student actions.
    """
    def get(self, request):
        teacher = request.user
        search_query = request.GET.get('q', '').strip()
        status_filter = request.GET.get('filter', 'all').strip().lower()
        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)

        # 1. Find all student user IDs associated with this teacher's live classes
        enrolled_student_ids = set(
            ClassEnrollment.objects.filter(live_class__teacher=teacher)
            .values_list('student_id', flat=True)
        )
        participant_student_ids = set(
            ClassParticipant.objects.filter(live_class__teacher=teacher, user__isnull=False)
            .values_list('user_id', flat=True)
        )
        requested_student_ids = set(
            ClassAccessRequest.objects.filter(live_class__teacher=teacher)
            .values_list('student_id', flat=True)
        )

        all_associated_student_ids = enrolled_student_ids | participant_student_ids | requested_student_ids

        # 2. Metric Counters
        total_students_count = len(all_associated_student_ids)

        active_student_ids = set(
            ClassEnrollment.objects.filter(
                live_class__teacher=teacher,
                status=ClassEnrollment.Status.ENROLLED
            ).values_list('student_id', flat=True)
        )
        active_students_count = len(active_student_ids)

        students_in_live_count = (
            ClassParticipant.objects.filter(
                live_class__teacher=teacher,
                live_class__status=LiveClass.Status.LIVE,
                status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED],
                user__isnull=False
            ).values('user_id').distinct().count()
        )

        pending_invitations_count = (
            ClassEnrollment.objects.filter(
                live_class__teacher=teacher,
                status=ClassEnrollment.Status.INVITED
            ).count()
        )

        pending_requests_count = (
            ClassAccessRequest.objects.filter(
                live_class__teacher=teacher,
                status=ClassAccessRequest.Status.PENDING
            ).count()
        )

        # 3. Base QuerySet
        students_qs = User.objects.filter(id__in=all_associated_student_ids).order_by('first_name', 'last_name', 'username')

        # 4. Search Filter (Server-side, Case-insensitive)
        if search_query:
            students_qs = students_qs.filter(
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query) |
                Q(username__icontains=search_query) |
                Q(email__icontains=search_query)
            )

        # 5. Apply Status Filters
        if status_filter == 'active':
            students_qs = students_qs.filter(id__in=active_student_ids)
        elif status_filter == 'inactive':
            students_qs = students_qs.exclude(id__in=active_student_ids)
        elif status_filter == 'has_active_classes':
            has_active_class_ids = set(
                ClassEnrollment.objects.filter(
                    live_class__teacher=teacher,
                    status=ClassEnrollment.Status.ENROLLED,
                    live_class__status__in=[LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE]
                ).values_list('student_id', flat=True)
            )
            students_qs = students_qs.filter(id__in=has_active_class_ids)
        elif status_filter == 'no_active_classes':
            has_active_class_ids = set(
                ClassEnrollment.objects.filter(
                    live_class__teacher=teacher,
                    status=ClassEnrollment.Status.ENROLLED,
                    live_class__status__in=[LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE]
                ).values_list('student_id', flat=True)
            )
            students_qs = students_qs.exclude(id__in=has_active_class_ids)
        elif status_filter == 'recently_joined':
            recent_ids = set(
                ClassEnrollment.objects.filter(
                    live_class__teacher=teacher,
                    created_at__gte=thirty_days_ago
                ).values_list('student_id', flat=True)
            ) | set(
                ClassParticipant.objects.filter(
                    live_class__teacher=teacher,
                    user__isnull=False,
                    join_time__gte=thirty_days_ago
                ).values_list('user_id', flat=True)
            )
            students_qs = students_qs.filter(id__in=recent_ids)
        elif status_filter == 'pending_access':
            pending_ids = set(
                ClassAccessRequest.objects.filter(
                    live_class__teacher=teacher,
                    status=ClassAccessRequest.Status.PENDING
                ).values_list('student_id', flat=True)
            ) | set(
                ClassEnrollment.objects.filter(
                    live_class__teacher=teacher,
                    status=ClassEnrollment.Status.INVITED
                ).values_list('student_id', flat=True)
            )
            students_qs = students_qs.filter(id__in=pending_ids)
        elif status_filter == 'removed':
            removed_ids = set(
                ClassEnrollment.objects.filter(
                    live_class__teacher=teacher,
                    status=ClassEnrollment.Status.REVOKED
                ).values_list('student_id', flat=True)
            )
            students_qs = students_qs.filter(id__in=removed_ids)

        # 6. Pagination (15 students per page)
        paginator = Paginator(students_qs, 15)
        page_num = request.GET.get('page', 1)
        try:
            students_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            students_page = paginator.page(1)

        # 7. Optimized Data Pre-fetching for Current Page
        page_student_ids = [s.id for s in students_page]

        # Enrollments for page students with this teacher
        all_page_enrollments = list(
            ClassEnrollment.objects.filter(
                live_class__teacher=teacher,
                student_id__in=page_student_ids
            ).select_related('live_class')
        )
        enrollments_by_student = {}
        for e in all_page_enrollments:
            enrollments_by_student.setdefault(e.student_id, []).append(e)

        # Attendances for page students with this teacher
        all_page_attendances = list(
            Attendance.objects.filter(
                live_class__teacher=teacher,
                student_id__in=page_student_ids
            ).select_related('live_class').order_by('-joined_at')
        )
        attendances_by_student = {}
        for a in all_page_attendances:
            attendances_by_student.setdefault(a.student_id, []).append(a)

        # Participations for last joined date fallback
        all_page_participations = list(
            ClassParticipant.objects.filter(
                live_class__teacher=teacher,
                user_id__in=page_student_ids
            ).order_by('-join_time')
        )
        participations_by_student = {}
        for p in all_page_participations:
            participations_by_student.setdefault(p.user_id, []).append(p)

        # Attach computed metadata to each student object
        for student in students_page:
            s_enrollments = enrollments_by_student.get(student.id, [])
            s_attendances = attendances_by_student.get(student.id, [])
            s_participations = participations_by_student.get(student.id, [])

            enrolled_items = [e for e in s_enrollments if e.status == ClassEnrollment.Status.ENROLLED]
            student.enrolled_classes_count = len(enrolled_items)

            active_items = [
                e for e in enrolled_items
                if e.live_class.status in (LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE)
            ]
            student.active_classes_count = len(active_items)
            student.active_classes = [e.live_class for e in active_items]

            # Last joined timestamp
            last_dt = None
            if s_attendances:
                last_dt = s_attendances[0].joined_at
            if s_participations:
                p_dt = s_participations[0].join_time
                if not last_dt or (p_dt and p_dt > last_dt):
                    last_dt = p_dt
            student.last_joined_date = last_dt

            # Attendance percentage where meaningful
            completed_enrolled_count = len([
                e for e in enrolled_items
                if e.live_class.status == LiveClass.Status.COMPLETED
            ])
            attended_present_count = len([
                a for a in s_attendances
                if a.status == Attendance.Status.PRESENT
            ])
            if completed_enrolled_count > 0:
                pct = min(100.0, round((attended_present_count / completed_enrolled_count) * 100, 1))
                student.attendance_percentage = pct
            elif attended_present_count > 0:
                student.attendance_percentage = 100.0
            else:
                student.attendance_percentage = None

            # Current status badge
            is_revoked = any(e.status == ClassEnrollment.Status.REVOKED for e in s_enrollments)
            is_pending = any(e.status == ClassEnrollment.Status.INVITED for e in s_enrollments)
            is_enrolled = len(enrolled_items) > 0

            if is_enrolled:
                student.status_label = 'Active'
                student.status_badge = 'bg-success'
            elif is_pending:
                student.status_label = 'Pending'
                student.status_badge = 'bg-info'
            elif is_revoked:
                student.status_label = 'Removed'
                student.status_badge = 'bg-danger'
            else:
                student.status_label = 'Inactive'
                student.status_badge = 'bg-secondary'

            student.completed_classes_count = completed_enrolled_count
            student.pending_invite_count = len([e for e in s_enrollments if e.status == ClassEnrollment.Status.INVITED])
            student.pending_access_count = ClassAccessRequest.objects.filter(
                live_class__teacher=teacher,
                student=student,
                status=ClassAccessRequest.Status.PENDING
            ).count()

        student_items = []
        for s in students_page:
            student_items.append({
                'student': s,
                'enrolled_classes_count': getattr(s, 'enrolled_classes_count', 0),
                'active_classes_count': getattr(s, 'active_classes_count', 0),
                'completed_classes_count': getattr(s, 'completed_classes_count', 0),
                'attendance_percentage': getattr(s, 'attendance_percentage', 0.0) or 0.0,
                'status': 'ACTIVE' if getattr(s, 'status_label', '') == 'Active' else ('REVOKED' if getattr(s, 'status_label', '') == 'Removed' else 'INACTIVE'),
                'status_display': getattr(s, 'status_label', 'Active'),
                'pending_access_count': getattr(s, 'pending_access_count', 0),
                'pending_invite_count': getattr(s, 'pending_invite_count', 0),
            })

        # Teacher's scheduled classes for "Invite to Class" modal
        upcoming_teacher_classes = teacher.live_classes.filter(
            status__in=[LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE]
        ).order_by('scheduled_date', 'scheduled_time')[:20]

        return render(request, 'classrooms/teacher_students_list.html', {
            'students': students_page,
            'student_items': student_items,
            'page_obj': students_page,
            'search_query': search_query,
            'selected_filter': status_filter,
            'total_students': total_students_count,
            'total_students_count': total_students_count,
            'active_students': active_students_count,
            'active_students_count': active_students_count,
            'students_in_live_classes': students_in_live_count,
            'students_in_live_count': students_in_live_count,
            'pending_invitations': pending_invitations_count,
            'pending_invitations_count': pending_invitations_count,
            'pending_access_requests': pending_requests_count,
            'pending_requests_count': pending_requests_count,
            'upcoming_classes': upcoming_teacher_classes,
            'page_title': 'My Students - TeachLive Instructor',
        })


# =====================================================================
# 8. Teacher Student Detail View (/teacher/students/<student_id>/)
# =====================================================================

@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherStudentDetailView(TeacherRequiredMixin, View):
    """
    Teacher view of a specific student's profile, relationship summary,
    attendance history, and class history table.
    Enforces strict teacher-student relationship authorization:
    Teacher can ONLY view students who have enrolled in, requested, or joined
    at least one class owned by this teacher.
    """
    def get(self, request, student_id):
        teacher = request.user
        student = get_object_or_404(User, pk=student_id)

        # Strict Ownership Check: Must have relation with at least one of this teacher's classes
        if not teacher.is_admin_role:
            has_enrollment = ClassEnrollment.objects.filter(live_class__teacher=teacher, student=student).exists()
            has_participation = ClassParticipant.objects.filter(live_class__teacher=teacher, user=student).exists()
            has_request = ClassAccessRequest.objects.filter(live_class__teacher=teacher, student=student).exists()
            if not (has_enrollment or has_participation or has_request):
                raise PermissionDenied("Access Denied: You do not have permission to view details for this student.")

        # Class Summary (with this teacher)
        teacher_enrollments = ClassEnrollment.objects.filter(
            live_class__teacher=teacher,
            student=student
        ).select_related('live_class')

        total_enrolled_classes = teacher_enrollments.filter(status=ClassEnrollment.Status.ENROLLED).count()
        active_enrollments = teacher_enrollments.filter(
            status=ClassEnrollment.Status.ENROLLED,
            live_class__status__in=[LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE]
        ).count()
        completed_classes = teacher_enrollments.filter(
            status=ClassEnrollment.Status.ENROLLED,
            live_class__status=LiveClass.Status.COMPLETED
        ).count()
        pending_invitations = teacher_enrollments.filter(status=ClassEnrollment.Status.INVITED).count()
        pending_access_requests = ClassAccessRequest.objects.filter(
            live_class__teacher=teacher,
            student=student,
            status=ClassAccessRequest.Status.PENDING
        ).count()

        # Attendance Summary (with this teacher)
        attendances = list(
            Attendance.objects.filter(
                live_class__teacher=teacher,
                student=student
            ).select_related('live_class').order_by('-joined_at')
        )
        classes_attended = len([a for a in attendances if a.status == Attendance.Status.PRESENT])
        total_duration_minutes = sum(a.total_duration for a in attendances)
        total_duration_hours = round(total_duration_minutes / 60.0, 1)

        if completed_classes > 0:
            attendance_percentage = min(100.0, round((classes_attended / completed_classes) * 100, 1))
        elif classes_attended > 0:
            attendance_percentage = 100.0
        else:
            attendance_percentage = None

        last_attendance = attendances[0].joined_at if attendances else None
        if not last_attendance:
            last_part = ClassParticipant.objects.filter(
                live_class__teacher=teacher,
                user=student
            ).order_by('-join_time').first()
            if last_part:
                last_attendance = last_part.join_time

        # Recent Activity (Joins, leaves, moderation, access requests)
        recent_participations = list(
            ClassParticipant.objects.filter(
                live_class__teacher=teacher,
                user=student
            ).select_related('live_class').order_by('-join_time')[:10]
        )
        recent_requests = list(
            ClassAccessRequest.objects.filter(
                live_class__teacher=teacher,
                student=student
            ).select_related('live_class').order_by('-created_at')[:5]
        )
        recent_moderations = list(
            LiveClassParticipantModeration.objects.filter(
                live_class__teacher=teacher,
                student=student
            ).select_related('live_class').order_by('-created_at')[:5]
        )

        # Class History (Server-side search, status filter, date filter, pagination)
        search_query = request.GET.get('q', '').strip()
        status_filter = request.GET.get('status', '').strip().upper()
        date_filter = request.GET.get('date', '').strip()

        # Find all of this teacher's classes connected to this student
        enrolled_class_ids = teacher_enrollments.values_list('live_class_id', flat=True)
        participant_class_ids = ClassParticipant.objects.filter(
            live_class__teacher=teacher,
            user=student
        ).values_list('live_class_id', flat=True)
        connected_class_ids = set(enrolled_class_ids) | set(participant_class_ids)

        history_classes_qs = LiveClass.objects.filter(
            id__in=connected_class_ids,
            teacher=teacher
        ).order_by('-scheduled_date', '-scheduled_time')

        if search_query:
            history_classes_qs = history_classes_qs.filter(
                Q(title__icontains=search_query) |
                Q(subject__icontains=search_query) |
                Q(room_code__icontains=search_query)
            )

        if status_filter and status_filter in LiveClass.Status.values:
            history_classes_qs = history_classes_qs.filter(status=status_filter)

        if date_filter:
            try:
                parsed_date = timezone.datetime.strptime(date_filter, '%Y-%m-%d').date()
                history_classes_qs = history_classes_qs.filter(scheduled_date=parsed_date)
            except ValueError:
                pass

        paginator = Paginator(history_classes_qs, 10)
        page_num = request.GET.get('page', 1)
        try:
            history_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            history_page = paginator.page(1)

        # Fast lookup mapping for history page rows
        page_class_ids = [c.id for c in history_page]
        enrollment_map = {e.live_class_id: e for e in teacher_enrollments if e.live_class_id in page_class_ids}
        attendance_map = {a.live_class_id: a for a in attendances if a.live_class_id in page_class_ids}
        participant_map = {}
        for p in ClassParticipant.objects.filter(live_class_id__in=page_class_ids, user=student).order_by('-join_time'):
            if p.live_class_id not in participant_map:
                participant_map[p.live_class_id] = p

        class_history_items = []
        for c in history_page:
            c.student_enrollment = enrollment_map.get(c.id)
            c.student_attendance = attendance_map.get(c.id)
            c.student_participant = participant_map.get(c.id)
            status_val = 'Not Enrolled'
            revoked_reason = ''
            enrolled_date = None
            if c.student_enrollment:
                status_val = c.student_enrollment.status
                revoked_reason = c.student_enrollment.revocation_reason
                enrolled_date = c.student_enrollment.created_at
            class_history_items.append({
                'class': c,
                'enrollment': c.student_enrollment,
                'status': status_val,
                'attendance': c.student_attendance,
                'enrolled_date': enrolled_date,
                'revoked_reason': revoked_reason,
            })

        # Teacher's upcoming classes for invite modal
        upcoming_teacher_classes = teacher.live_classes.filter(
            status__in=[LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE]
        ).exclude(enrollments__student=student, enrollments__status=ClassEnrollment.Status.ENROLLED).order_by('scheduled_date', 'scheduled_time')

        return render(request, 'classrooms/teacher_student_detail.html', {
            'student': student,
            'total_classes': total_enrolled_classes,
            'total_enrolled_classes': total_enrolled_classes,
            'active_classes': active_enrollments,
            'active_enrollments': active_enrollments,
            'completed_classes': completed_classes,
            'pending_invitations': pending_invitations,
            'pending_access_requests': pending_access_requests,
            'classes_attended': classes_attended,
            'total_attended_sessions': classes_attended,
            'total_duration_minutes': total_duration_minutes,
            'total_minutes_attended': total_duration_minutes,
            'total_duration_hours': total_duration_hours,
            'attendance_percentage': attendance_percentage or 0.0,
            'overall_attendance_rate': attendance_percentage or 0.0,
            'last_attendance': last_attendance,
            'recent_participations': recent_participations,
            'recent_requests': recent_requests,
            'recent_moderations': recent_moderations,
            'history_classes': history_page,
            'page_obj': history_page,
            'class_history_items': class_history_items,
            'search_query': search_query,
            'status_filter': status_filter,
            'date_filter': date_filter,
            'upcoming_classes': upcoming_teacher_classes,
            'page_title': f"{student.get_full_name() or student.username} - Student Details - TeachLive",
        })
