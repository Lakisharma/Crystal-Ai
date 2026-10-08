"""
TeachLive Secure Admin Dashboard Views.
Enforces strict administrator role validation on every view (returns HTTP 403 Forbidden
for teachers and students, redirects unauthenticated users to login).
Provides complete management for Teachers, Students, Live Classes, Attendance,
Chat Reports, Analytics, and Audit Logs.
"""

import csv
from datetime import timedelta
import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Avg, Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from accounts.models import AdminAuditLog, User, log_admin_action
from accounts.permissions import AdminRequiredMixin
from classrooms.livekit_service import is_livekit_configured
from classrooms.models import (
    Attendance,
    ChatMessage,
    ClassAccessRequest,
    ClassEnrollment,
    ClassParticipant,
    LiveClass,
)
from notifications.models import EmailLog, Notification
from notifications.services import NotificationService

logger = logging.getLogger(__name__)


# =====================================================================
# 1. Main Admin Dashboard View
# =====================================================================

class AdminDashboardIndexView(AdminRequiredMixin, View):
    """
    Main TeachLive Admin Dashboard at /admin-dashboard/.
    Displays real-time database statistics, recent live classes,
    recent teacher/student registrations, and administrative audit events.
    """
    def get(self, request):
        now = timezone.now()

        # Database metric cards (Section 3: DASHBOARD STATISTICS)
        total_teachers = User.objects.filter(role=User.Role.TEACHER).count()
        total_students = User.objects.filter(role=User.Role.STUDENT).count()
        total_classes = LiveClass.objects.count()
        currently_live = LiveClass.objects.filter(status=LiveClass.Status.LIVE).count()
        completed_classes = LiveClass.objects.filter(status=LiveClass.Status.COMPLETED).count()
        cancelled_classes = LiveClass.objects.filter(status=LiveClass.Status.CANCELLED).count()

        # Activity summary
        total_attendance_records = Attendance.objects.count()
        total_chat_messages = ChatMessage.objects.count()

        # Recent activities (Section 25: DASHBOARD RECENT ACTIVITY)
        recent_classes = (
            LiveClass.objects.select_related('teacher')
            .order_by('-created_at')[:6]
        )
        recent_teachers = (
            User.objects.filter(role=User.Role.TEACHER)
            .order_by('-date_joined')[:5]
        )
        recent_students = (
            User.objects.filter(role=User.Role.STUDENT)
            .order_by('-date_joined')[:5]
        )
        recent_audit_logs = (
            AdminAuditLog.objects.select_related('admin')
            .order_by('-created_at')[:6]
        )

        return render(request, 'admin_dashboard/dashboard.html', {
            'total_teachers': total_teachers,
            'total_students': total_students,
            'total_classes': total_classes,
            'currently_live': currently_live,
            'completed_classes': completed_classes,
            'cancelled_classes': cancelled_classes,
            'total_attendance_records': total_attendance_records,
            'total_chat_messages': total_chat_messages,
            'recent_classes': recent_classes,
            'recent_teachers': recent_teachers,
            'recent_students': recent_students,
            'recent_audit_logs': recent_audit_logs,
            'is_livekit_configured': is_livekit_configured(),
            'active_menu': 'dashboard',
            'page_title': 'Admin Dashboard - TeachLive',
        })


# =====================================================================
# 2. Teacher Management Views
# =====================================================================

class AdminTeacherListView(AdminRequiredMixin, View):
    """
    Teacher Management List at /admin-dashboard/teachers/.
    Allows filtering by status, server-side search, and pagination.
    """
    def get(self, request):
        query = request.GET.get('q', '').strip()
        status_filter = request.GET.get('status', '').strip().lower()

        qs = User.objects.filter(role=User.Role.TEACHER).annotate(
            classes_count=Count('live_classes')
        ).order_by('-date_joined')

        if query:
            qs = qs.filter(
                Q(username__icontains=query) |
                Q(first_name__icontains=query) |
                Q(last_name__icontains=query) |
                Q(email__icontains=query) |
                Q(phone_number__icontains=query)
            )

        if status_filter == 'active':
            qs = qs.filter(is_active=True)
        elif status_filter == 'inactive':
            qs = qs.filter(is_active=False)

        paginator = Paginator(qs, 25)
        page_num = request.GET.get('page', 1)
        try:
            teachers_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            teachers_page = paginator.page(1)

        return render(request, 'admin_dashboard/teachers_list.html', {
            'teachers': teachers_page,
            'search_query': query,
            'selected_status': status_filter,
            'total_count': qs.count(),
            'active_menu': 'teachers',
            'page_title': 'Teacher Management - TeachLive Admin',
        })


class AdminTeacherDetailView(AdminRequiredMixin, View):
    """
    Teacher Detail at /admin-dashboard/teachers/<id>/.
    Displays teacher profile, metrics, and list of classes conducted.
    """
    def get(self, request, pk):
        teacher = get_object_or_404(User, pk=pk, role=User.Role.TEACHER)

        # Teacher metrics
        teacher_classes = teacher.live_classes.all().order_by('-scheduled_date', '-scheduled_time')
        total_classes = teacher_classes.count()
        live_classes = teacher_classes.filter(status=LiveClass.Status.LIVE).count()
        completed_classes = teacher_classes.filter(status=LiveClass.Status.COMPLETED).count()
        cancelled_classes = teacher_classes.filter(status=LiveClass.Status.CANCELLED).count()

        # Students reached across all classes
        total_students_reached = ClassParticipant.objects.filter(
            live_class__teacher=teacher
        ).values('student_email').distinct().count()

        recent_classes = teacher_classes[:15]

        return render(request, 'admin_dashboard/teacher_detail.html', {
            'teacher': teacher,
            'total_classes': total_classes,
            'live_classes': live_classes,
            'completed_classes': completed_classes,
            'cancelled_classes': cancelled_classes,
            'total_students_reached': total_students_reached,
            'recent_classes': recent_classes,
            'active_menu': 'teachers',
            'page_title': f"Teacher: {teacher.get_full_name() or teacher.username} - TeachLive Admin",
        })


class AdminTeacherToggleStatusView(AdminRequiredMixin, View):
    """
    POST-only action to activate/deactivate a teacher account.
    Records an AdminAuditLog entry and preserves all historical classes & attendance.
    """
    def post(self, request, pk):
        teacher = get_object_or_404(User, pk=pk, role=User.Role.TEACHER)

        if teacher.is_active:
            teacher.is_active = False
            teacher.save(update_fields=['is_active'])
            log_admin_action(
                admin=request.user,
                action=AdminAuditLog.Action.TEACHER_DEACTIVATED,
                target_type='Teacher',
                target_id=teacher.pk,
                description=f"Deactivated teacher account for {teacher.get_full_name() or teacher.username} ({teacher.email}).",
                request=request
            )
            messages.warning(request, f"Teacher account '{teacher.get_full_name() or teacher.username}' has been deactivated.")
        else:
            teacher.is_active = True
            teacher.save(update_fields=['is_active'])
            log_admin_action(
                admin=request.user,
                action=AdminAuditLog.Action.TEACHER_ACTIVATED,
                target_type='Teacher',
                target_id=teacher.pk,
                description=f"Activated teacher account for {teacher.get_full_name() or teacher.username} ({teacher.email}).",
                request=request
            )
            messages.success(request, f"Teacher account '{teacher.get_full_name() or teacher.username}' has been successfully activated.")

        return redirect('admin_dashboard:teacher_detail', pk=teacher.pk)


# =====================================================================
# 3. Student Management Views
# =====================================================================

class AdminStudentListView(AdminRequiredMixin, View):
    """
    Student Management List at /admin-dashboard/students/.
    Allows filtering by status, server-side search, and pagination.
    Never exposes Google IDs, tokens, or private secrets.
    """
    def get(self, request):
        query = request.GET.get('q', '').strip()
        status_filter = request.GET.get('status', '').strip().lower()

        qs = User.objects.filter(role=User.Role.STUDENT).annotate(
            classes_joined=Count('live_class_participations', distinct=True)
        ).order_by('-date_joined')

        if query:
            qs = qs.filter(
                Q(username__icontains=query) |
                Q(first_name__icontains=query) |
                Q(last_name__icontains=query) |
                Q(email__icontains=query)
            )

        if status_filter == 'active':
            qs = qs.filter(is_active=True)
        elif status_filter == 'inactive':
            qs = qs.filter(is_active=False)

        paginator = Paginator(qs, 25)
        page_num = request.GET.get('page', 1)
        try:
            students_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            students_page = paginator.page(1)

        return render(request, 'admin_dashboard/students_list.html', {
            'students': students_page,
            'search_query': query,
            'selected_status': status_filter,
            'total_count': qs.count(),
            'active_menu': 'students',
            'page_title': 'Student Management - TeachLive Admin',
        })


class AdminStudentDetailView(AdminRequiredMixin, View):
    """
    Student Detail at /admin-dashboard/students/<id>/.
    Displays learning statistics, recent attendance history, and account toggle.
    """
    def get(self, request, pk):
        student = get_object_or_404(User, pk=pk, role=User.Role.STUDENT)

        # Statistics
        classes_joined_count = ClassParticipant.objects.filter(user=student).count()
        total_attendance_sessions = Attendance.objects.filter(student=student).count()
        total_learning_mins = Attendance.objects.filter(student=student).aggregate(
            total=Sum('total_duration')
        )['total'] or 0

        learning_time_formatted = (
            f"{total_learning_mins // 60}h {total_learning_mins % 60}m"
            if total_learning_mins >= 60 else f"{total_learning_mins} mins"
        )

        recent_attendances = (
            Attendance.objects.filter(student=student)
            .select_related('live_class', 'live_class__teacher')
            .order_by('-joined_at')[:20]
        )

        return render(request, 'admin_dashboard/student_detail.html', {
            'student': student,
            'classes_joined_count': classes_joined_count,
            'total_attendance_sessions': total_attendance_sessions,
            'total_learning_mins': total_learning_mins,
            'learning_time_formatted': learning_time_formatted,
            'recent_attendances': recent_attendances,
            'active_menu': 'students',
            'page_title': f"Student: {student.get_full_name() or student.username} - TeachLive Admin",
        })


class AdminStudentToggleStatusView(AdminRequiredMixin, View):
    """
    POST-only action to activate/deactivate a student account.
    """
    def post(self, request, pk):
        student = get_object_or_404(User, pk=pk, role=User.Role.STUDENT)

        if student.is_active:
            student.is_active = False
            student.save(update_fields=['is_active'])
            log_admin_action(
                admin=request.user,
                action=AdminAuditLog.Action.STUDENT_DEACTIVATED,
                target_type='Student',
                target_id=student.pk,
                description=f"Deactivated student account for {student.get_full_name() or student.username} ({student.email}).",
                request=request
            )
            messages.warning(request, f"Student account '{student.get_full_name() or student.username}' has been deactivated.")
        else:
            student.is_active = True
            student.save(update_fields=['is_active'])
            log_admin_action(
                admin=request.user,
                action=AdminAuditLog.Action.STUDENT_ACTIVATED,
                target_type='Student',
                target_id=student.pk,
                description=f"Activated student account for {student.get_full_name() or student.username} ({student.email}).",
                request=request
            )
            messages.success(request, f"Student account '{student.get_full_name() or student.username}' has been successfully activated.")

        return redirect('admin_dashboard:student_detail', pk=student.pk)


# =====================================================================
# 4. Live Class Management Views
# =====================================================================

class AdminLiveClassListView(AdminRequiredMixin, View):
    """
    Live Class Management List at /admin-dashboard/classes/.
    Shows all classes with filters (Status, Teacher, Date, Subject) and search.
    """
    def get(self, request):
        query = request.GET.get('q', '').strip()
        status_filter = request.GET.get('status', '').strip().upper()
        teacher_id = request.GET.get('teacher_id', '').strip()
        date_str = request.GET.get('date', '').strip()
        subject_filter = request.GET.get('subject', '').strip()

        qs = LiveClass.objects.select_related('teacher').annotate(
            participants_count=Count('participants')
        ).order_by('-created_at')

        if query:
            qs = qs.filter(
                Q(title__icontains=query) |
                Q(subject__icontains=query) |
                Q(room_code__icontains=query) |
                Q(teacher__first_name__icontains=query) |
                Q(teacher__last_name__icontains=query) |
                Q(teacher__username__icontains=query)
            )

        if status_filter and hasattr(LiveClass.Status, status_filter):
            qs = qs.filter(status=status_filter)

        if teacher_id.isdigit():
            qs = qs.filter(teacher_id=int(teacher_id))

        if subject_filter:
            qs = qs.filter(subject__iexact=subject_filter)

        if date_str:
            try:
                date_val = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
                qs = qs.filter(scheduled_date=date_val)
            except ValueError:
                pass

        all_teachers = User.objects.filter(role=User.Role.TEACHER).order_by('first_name', 'username')
        all_subjects = LiveClass.objects.values_list('subject', flat=True).distinct().order_by('subject')

        paginator = Paginator(qs, 25)
        page_num = request.GET.get('page', 1)
        try:
            classes_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            classes_page = paginator.page(1)

        return render(request, 'admin_dashboard/classes_list.html', {
            'classes': classes_page,
            'all_teachers': all_teachers,
            'all_subjects': all_subjects,
            'search_query': query,
            'selected_status': status_filter,
            'selected_teacher': teacher_id,
            'selected_date': date_str,
            'selected_subject': subject_filter,
            'status_choices': LiveClass.Status.choices,
            'total_count': qs.count(),
            'active_menu': 'classes',
            'page_title': 'Live Class Management - TeachLive Admin',
        })


class AdminLiveClassDetailView(AdminRequiredMixin, View):
    """
    Live Class Detail at /admin-dashboard/classes/<id>/.
    Displays class info, metrics, participants, attendance, enrollments, access requests, and chat.
    Never exposes room passwords or LiveKit secrets.
    """
    def get(self, request, pk):
        live_class = get_object_or_404(LiveClass.objects.select_related('teacher'), pk=pk)

        participants = live_class.participants.all().order_by('-join_time')[:30]
        attendances = live_class.attendances.select_related('student').order_by('-joined_at')[:30]
        chat_messages = live_class.chat_messages.select_related('sender').order_by('-created_at')[:30]
        enrollments = live_class.enrollments.select_related('student').order_by('-created_at')
        access_requests = live_class.access_requests.select_related('student').order_by('-created_at')
        moderation_events = live_class.moderation_events.select_related('student', 'created_by').order_by('-created_at')[:50]

        # Statistics
        total_participants = live_class.participants.count()
        total_attendances = live_class.attendances.count()
        total_enrollments = enrollments.count()
        active_enrollments_count = enrollments.filter(status=ClassEnrollment.Status.ENROLLED).count()
        pending_requests_count = access_requests.filter(status=ClassAccessRequest.Status.PENDING).count()
        avg_duration = live_class.attendances.aggregate(avg=Avg('total_duration'))['avg']
        avg_duration_mins = int(round(avg_duration)) if avg_duration else 0
        total_chat_count = live_class.chat_messages.count()

        return render(request, 'admin_dashboard/class_detail.html', {
            'live_class': live_class,
            'participants': participants,
            'attendances': attendances,
            'chat_messages': chat_messages,
            'enrollments': enrollments,
            'access_requests': access_requests,
            'moderation_events': moderation_events,
            'total_participants': total_participants,
            'total_attendances': total_attendances,
            'total_enrollments': total_enrollments,
            'active_enrollments_count': active_enrollments_count,
            'pending_requests_count': pending_requests_count,
            'avg_duration_mins': avg_duration_mins,
            'total_chat_count': total_chat_count,
            'active_menu': 'classes',
            'page_title': f"Class: {live_class.title} ({live_class.room_code}) - TeachLive Admin",
        })


class AdminClassEnrollmentRevokeView(AdminRequiredMixin, View):
    """
    POST action to revoke a student's enrollment for a class from the Admin Dashboard.
    Records AdminAuditLog.Action.ENROLLMENT_REVOKED and notifies the student.
    """
    def post(self, request, pk, enrollment_id):
        live_class = get_object_or_404(LiveClass, pk=pk)
        enrollment = get_object_or_404(ClassEnrollment, pk=enrollment_id, live_class=live_class)
        student = enrollment.student
        now = timezone.now()

        enrollment.status = ClassEnrollment.Status.REVOKED
        enrollment.revoked_at = now
        enrollment.save(update_fields=['status', 'revoked_at', 'updated_at'])

        # Update any active participant state
        ClassParticipant.objects.filter(
            live_class=live_class,
            user=student,
            status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED]
        ).update(
            status=ClassParticipant.Status.KICKED,
            leave_time=now
        )

        try:
            NotificationService.notify_enrollment_revoked(live_class, student)
        except Exception as exc:
            logger.error("Failed to send revocation notice from admin: %s", exc)

        log_admin_action(
            admin=request.user,
            action=AdminAuditLog.Action.ENROLLMENT_REVOKED,
            target_type='LiveClass',
            target_id=str(live_class.id),
            description=f"Admin revoked enrollment for student {student.username} ({student.email}) in class '{live_class.title}' ({live_class.room_code}).",
            request=request
        )

        messages.warning(request, f"Revoked enrollment access for {student.get_full_name() or student.username}.")
        return redirect('admin_dashboard:class_detail', pk=live_class.pk)


class AdminAccessRequestApproveView(AdminRequiredMixin, View):
    """
    POST action to approve a student's access request from the Admin Dashboard.
    Enrolls the student, records AdminAuditLog.Action.ACCESS_REQUEST_APPROVED, and sends notification.
    """
    def post(self, request, pk, request_id):
        live_class = get_object_or_404(LiveClass, pk=pk)
        access_request = get_object_or_404(ClassAccessRequest, pk=request_id, live_class=live_class)
        student = access_request.student
        now = timezone.now()

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
            enrollment.enrolled_at = now
            enrollment.save(update_fields=['status', 'revoked_at', 'enrolled_at', 'updated_at'])

        try:
            NotificationService.notify_enrollment_approved(live_class, student)
        except Exception as exc:
            logger.error("Failed to send approval notice from admin: %s", exc)

        log_admin_action(
            admin=request.user,
            action=AdminAuditLog.Action.ACCESS_REQUEST_APPROVED,
            target_type='LiveClass',
            target_id=str(live_class.id),
            description=f"Admin approved access request for student {student.username} ({student.email}) in class '{live_class.title}' ({live_class.room_code}).",
            request=request
        )

        messages.success(request, f"Approved access request for {student.get_full_name() or student.username}.")
        return redirect('admin_dashboard:class_detail', pk=live_class.pk)


class AdminAccessRequestRejectView(AdminRequiredMixin, View):
    """
    POST action to reject a student's access request from the Admin Dashboard.
    Records AdminAuditLog.Action.ACCESS_REQUEST_REJECTED and sends notification.
    """
    def post(self, request, pk, request_id):
        live_class = get_object_or_404(LiveClass, pk=pk)
        access_request = get_object_or_404(ClassAccessRequest, pk=request_id, live_class=live_class)
        student = access_request.student
        now = timezone.now()

        access_request.status = ClassAccessRequest.Status.REJECTED
        access_request.reviewed_at = now
        access_request.reviewed_by = request.user
        access_request.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])

        try:
            NotificationService.notify_enrollment_rejected(live_class, student)
        except Exception as exc:
            logger.error("Failed to send rejection notice from admin: %s", exc)

        log_admin_action(
            admin=request.user,
            action=AdminAuditLog.Action.ACCESS_REQUEST_REJECTED,
            target_type='LiveClass',
            target_id=str(live_class.id),
            description=f"Admin rejected access request for student {student.username} ({student.email}) in class '{live_class.title}' ({live_class.room_code}).",
            request=request
        )

        messages.info(request, f"Rejected access request for {student.get_full_name() or student.username}.")
        return redirect('admin_dashboard:class_detail', pk=live_class.pk)


class AdminLiveClassCancelView(AdminRequiredMixin, View):
    """
    POST-only action to cancel a scheduled live class.
    Section 13: Admin can cancel a SCHEDULED class.
    If a class is already LIVE, rejects with:
    "Live classes cannot be cancelled from this action."
    """
    def post(self, request, pk):
        live_class = get_object_or_404(LiveClass, pk=pk)

        if live_class.status == LiveClass.Status.LIVE:
            messages.error(request, "Live classes cannot be cancelled from this action.")
            return redirect('admin_dashboard:class_detail', pk=live_class.pk)

        if live_class.status == LiveClass.Status.COMPLETED:
            messages.info(request, "This live class has already ended.")
            return redirect('admin_dashboard:class_detail', pk=live_class.pk)

        if live_class.status == LiveClass.Status.CANCELLED:
            messages.info(request, "This live class is already cancelled.")
            return redirect('admin_dashboard:class_detail', pk=live_class.pk)

        # Cancel scheduled class
        live_class.cancel_class()
        try:
            NotificationService.notify_class_cancelled(live_class, cancelled_by=request.user)
        except Exception as notif_err:
            logger.warning("Could not dispatch class cancelled notification: %s", notif_err)

        log_admin_action(
            admin=request.user,
            action=AdminAuditLog.Action.CLASS_CANCELLED,
            target_type='LiveClass',
            target_id=live_class.pk,
            description=f"Admin cancelled scheduled class '{live_class.title}' (Room: {live_class.room_code}, Teacher: {live_class.teacher.username}).",
            request=request
        )
        messages.success(request, f"Scheduled class '{live_class.title}' ({live_class.room_code}) has been successfully cancelled.")
        return redirect('admin_dashboard:class_detail', pk=live_class.pk)


# =====================================================================
# 5. Attendance Management View
# =====================================================================

class AdminAttendanceListView(AdminRequiredMixin, View):
    """
    Attendance Management & Reporting at /admin-dashboard/attendance/.
    Section 18:
    Admin can filter:
    - Teacher, Student, Class, Date range (presets + custom range), Status.
    Global summary cards:
    - Total classes, Total students, Total sessions, Total duration, Average attendance, Attendance percentage.
    """
    def get(self, request):
        teacher_id = request.GET.get('teacher_id', '').strip()
        class_id = request.GET.get('class_id', '').strip()
        student_query = request.GET.get('student', '').strip()
        status_filter = request.GET.get('status', '').strip().upper()
        date_preset = request.GET.get('date_preset', '').strip().lower()
        date_from_str = request.GET.get('date_from', '').strip()
        date_to_str = request.GET.get('date_to', '').strip()
        single_date = request.GET.get('date', '').strip()

        if single_date and not (date_from_str or date_preset):
            date_from_str = single_date
            date_to_str = single_date

        from classrooms.attendance_services import (
            calculate_attendance_percentage,
            get_date_range_bounds,
        )

        qs = Attendance.objects.select_related(
            'live_class',
            'live_class__teacher',
            'student'
        ).order_by('-joined_at')

        if teacher_id.isdigit():
            qs = qs.filter(live_class__teacher_id=int(teacher_id))

        if class_id.isdigit():
            qs = qs.filter(live_class_id=int(class_id))

        if student_query:
            qs = qs.filter(
                Q(student_name__icontains=student_query) |
                Q(student__email__icontains=student_query) |
                Q(student__username__icontains=student_query) |
                Q(live_class__title__icontains=student_query)
            )

        if status_filter and hasattr(Attendance.Status, status_filter):
            qs = qs.filter(status=status_filter)

        start_dt, end_dt, date_label = get_date_range_bounds(
            date_preset=date_preset,
            date_from_str=date_from_str,
            date_to_str=date_to_str
        )
        if start_dt:
            qs = qs.filter(joined_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(joined_at__lte=end_dt)

        # Global attendance statistics (Prompt #18)
        total_sessions = qs.count()
        total_classes_count = qs.values('live_class_id').distinct().count()
        student_ids = qs.exclude(student__isnull=True).values('student_id').distinct().count()
        student_names = qs.filter(student__isnull=True).values('student_name').distinct().count()
        total_students_count = student_ids + student_names

        agg = qs.aggregate(
            total_mins=Sum('total_duration'),
            avg_mins=Avg('total_duration')
        )
        total_duration_mins = int(agg['total_mins'] or 0)
        total_duration_hours = round(total_duration_mins / 60.0, 1)
        avg_duration_minutes = int(round(agg['avg_mins'])) if agg['avg_mins'] is not None else 0

        # Attendance percentage
        if total_sessions > 0:
            session_percentages = [
                calculate_attendance_percentage(att.total_duration, att.live_class.duration)
                for att in qs
            ]
            overall_attendance_pct = round(sum(session_percentages) / total_sessions, 1)
        else:
            overall_attendance_pct = 0.0

        all_teachers = User.objects.filter(role=User.Role.TEACHER).order_by('first_name', 'username')
        all_classes = LiveClass.objects.all().order_by('-created_at')[:50]

        paginator = Paginator(qs, 25)
        page_num = request.GET.get('page', 1)
        try:
            attendances_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            attendances_page = paginator.page(1)

        return render(request, 'admin_dashboard/attendance_list.html', {
            'attendances': attendances_page,
            'all_teachers': all_teachers,
            'all_classes': all_classes,
            'total_sessions': total_sessions,
            'total_classes_count': total_classes_count,
            'total_students_count': total_students_count,
            'total_duration_hours': total_duration_hours,
            'avg_duration_minutes': avg_duration_minutes,
            'overall_attendance_pct': overall_attendance_pct,
            'selected_teacher': teacher_id,
            'selected_class': class_id,
            'selected_student': student_query,
            'selected_date': single_date,
            'selected_date_preset': date_preset,
            'selected_date_from': date_from_str,
            'selected_date_to': date_to_str,
            'selected_status': status_filter,
            'status_choices': Attendance.Status.choices,
            'total_count': total_sessions,
            'active_menu': 'attendance',
            'page_title': 'Attendance Management & Reports - TeachLive Admin',
        })


class AdminAttendanceExportCSVView(AdminRequiredMixin, View):
    """
    Exports filtered attendance reports across all classes/teachers as CSV.
    Enforces strict Admin role verification.
    Sanitizes formula injection (=, +, -, @).
    Contains all 12 standard columns.
    """
    def get(self, request):
        teacher_id = request.GET.get('teacher_id', '').strip()
        class_id = request.GET.get('class_id', '').strip()
        student_query = request.GET.get('student', '').strip()
        status_filter = request.GET.get('status', '').strip().upper()
        date_preset = request.GET.get('date_preset', '').strip().lower()
        date_from_str = request.GET.get('date_from', '').strip()
        date_to_str = request.GET.get('date_to', '').strip()
        single_date = request.GET.get('date', '').strip()

        if single_date and not (date_from_str or date_preset):
            date_from_str = single_date
            date_to_str = single_date

        from classrooms.attendance_services import (
            calculate_attendance_percentage,
            get_date_range_bounds,
        )
        from classrooms.attendance_views import sanitize_csv_cell

        qs = Attendance.objects.select_related(
            'live_class',
            'live_class__teacher',
            'student'
        ).order_by('-joined_at')

        if teacher_id.isdigit():
            qs = qs.filter(live_class__teacher_id=int(teacher_id))

        if class_id.isdigit():
            qs = qs.filter(live_class_id=int(class_id))

        if student_query:
            qs = qs.filter(
                Q(student_name__icontains=student_query) |
                Q(student__email__icontains=student_query) |
                Q(student__username__icontains=student_query) |
                Q(live_class__title__icontains=student_query)
            )

        if status_filter and hasattr(Attendance.Status, status_filter):
            qs = qs.filter(status=status_filter)

        start_dt, end_dt, _ = get_date_range_bounds(
            date_preset=date_preset,
            date_from_str=date_from_str,
            date_to_str=date_to_str
        )
        if start_dt:
            qs = qs.filter(joined_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(joined_at__lte=end_dt)

        filename_suffix = timezone.now().strftime('%Y-%m-%d')
        filename = f"teachlive-admin-attendance-{filename_suffix}.csv"

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow([
            'Student Name',
            'Student Email',
            'Class',
            'Subject',
            'Scheduled Date',
            'Scheduled Start',
            'Scheduled Duration (mins)',
            'Join Time',
            'Leave Time',
            'Attendance Duration (mins)',
            'Attendance Status',
            'Attendance Percentage',
        ])

        tz = timezone.get_current_timezone()

        for att in qs:
            student_email = ''
            if att.student and att.student.email:
                student_email = att.student.email
            else:
                p = ClassParticipant.objects.filter(live_class=att.live_class, student_name=att.student_name).first()
                if p and p.student_email:
                    student_email = p.student_email

            join_time_str = att.joined_at.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S') if att.joined_at else ''
            leave_time_str = att.left_at.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S') if att.left_at else 'Active / In Progress'
            scheduled_date_str = str(att.live_class.scheduled_date) if att.live_class.scheduled_date else ''
            scheduled_time_str = att.live_class.scheduled_time.strftime('%H:%M') if att.live_class.scheduled_time else ''
            scheduled_duration = att.live_class.duration or 0

            att_pct = calculate_attendance_percentage(att.total_duration, scheduled_duration)

            writer.writerow([
                sanitize_csv_cell(att.student_name),
                sanitize_csv_cell(student_email),
                sanitize_csv_cell(att.live_class.title),
                sanitize_csv_cell(att.live_class.subject),
                sanitize_csv_cell(scheduled_date_str),
                sanitize_csv_cell(scheduled_time_str),
                scheduled_duration,
                join_time_str,
                leave_time_str,
                att.total_duration,
                sanitize_csv_cell(att.get_status_display()),
                f"{att_pct}%",
            ])

        log_admin_action(
            admin=request.user,
            action=AdminAuditLog.Action.OTHER,
            target_type='Attendance',
            target_id=0,
            description=f"Admin exported global attendance CSV: {filename} ({qs.count()} records)",
            request=request
        )

        return response


# =====================================================================
# 6. Chat Report View
# =====================================================================

class AdminChatReportView(AdminRequiredMixin, View):
    """
    Chat Reports at /admin-dashboard/chat/.
    Allows searching and monitoring classroom chat history.
    """
    def get(self, request):
        teacher_id = request.GET.get('teacher_id', '').strip()
        class_id = request.GET.get('class_id', '').strip()
        student_query = request.GET.get('student', '').strip()
        date_str = request.GET.get('date', '').strip()
        search_query = request.GET.get('q', '').strip()

        qs = ChatMessage.objects.select_related(
            'live_class',
            'live_class__teacher',
            'sender'
        ).order_by('-created_at')

        if teacher_id.isdigit():
            qs = qs.filter(live_class__teacher_id=int(teacher_id))

        if class_id.isdigit():
            qs = qs.filter(live_class_id=int(class_id))

        if student_query:
            qs = qs.filter(
                Q(sender_name__icontains=student_query) |
                Q(sender__email__icontains=student_query) |
                Q(sender__username__icontains=student_query)
            )

        if search_query:
            qs = qs.filter(message__icontains=search_query)

        if date_str:
            try:
                d_val = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date=d_val)
            except ValueError:
                pass

        all_teachers = User.objects.filter(role=User.Role.TEACHER).order_by('first_name', 'username')
        all_classes = LiveClass.objects.all().order_by('-created_at')[:50]

        paginator = Paginator(qs, 25)
        page_num = request.GET.get('page', 1)
        try:
            chat_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            chat_page = paginator.page(1)

        return render(request, 'admin_dashboard/chat_reports.html', {
            'chat_messages': chat_page,
            'all_teachers': all_teachers,
            'all_classes': all_classes,
            'selected_teacher': teacher_id,
            'selected_class': class_id,
            'selected_student': student_query,
            'selected_date': date_str,
            'search_query': search_query,
            'total_count': qs.count(),
            'active_menu': 'chat',
            'page_title': 'Chat Reports - TeachLive Admin',
        })


# =====================================================================
# 7. Analytics & Summary Reports View
# =====================================================================

class AdminReportsView(AdminRequiredMixin, View):
    """
    Analytics & Summary Reports at /admin-dashboard/reports/.
    Section 16: Top Teachers by Classes, Most Active Students,
    Most Attended Classes, Average Attendance Duration.
    """
    def get(self, request):
        # 1. Top Teachers by Classes Conducted
        top_teachers = (
            User.objects.filter(role=User.Role.TEACHER)
            .annotate(classes_count=Count('live_classes'))
            .filter(classes_count__gt=0)
            .order_by('-classes_count')[:10]
        )

        # 2. Most Active Students by Attendance Sessions
        top_students = (
            User.objects.filter(role=User.Role.STUDENT)
            .annotate(
                sessions_count=Count('live_attendances'),
                learning_time=Sum('live_attendances__total_duration')
            )
            .filter(sessions_count__gt=0)
            .order_by('-sessions_count')[:10]
        )

        # 3. Most Attended Classes
        most_attended_classes = (
            LiveClass.objects.select_related('teacher')
            .annotate(participants_count=Count('participants'))
            .filter(participants_count__gt=0)
            .order_by('-participants_count')[:10]
        )

        # 4. Overall Attendance Duration & Rates
        total_attendance_count = Attendance.objects.count()
        avg_duration_metric = Attendance.objects.aggregate(avg=Avg('total_duration'))['avg']
        avg_duration_minutes = int(round(avg_duration_metric)) if avg_duration_metric else 0

        present_attendances = Attendance.objects.filter(
            status__in=[Attendance.Status.PRESENT, Attendance.Status.LEFT]
        ).count()
        attendance_rate = (
            round((present_attendances / total_attendance_count) * 100, 1)
            if total_attendance_count > 0 else 0.0
        )

        return render(request, 'admin_dashboard/reports.html', {
            'top_teachers': top_teachers,
            'top_students': top_students,
            'most_attended_classes': most_attended_classes,
            'avg_duration_minutes': avg_duration_minutes,
            'attendance_rate': attendance_rate,
            'total_attendance_count': total_attendance_count,
            'active_menu': 'reports',
            'page_title': 'Summary Reports & Analytics - TeachLive Admin',
        })


# =====================================================================
# 8. Administrative Audit Logs View
# =====================================================================

class AdminAuditLogListView(AdminRequiredMixin, View):
    """
    Administrative Audit Logs at /admin-dashboard/audit-logs/.
    Sections 23 & 24: Security audit trail tracking key administrator actions.
    """
    def get(self, request):
        action_filter = request.GET.get('action', '').strip()
        admin_id = request.GET.get('admin_id', '').strip()
        date_str = request.GET.get('date', '').strip()

        qs = AdminAuditLog.objects.select_related('admin').order_by('-created_at')

        if action_filter and hasattr(AdminAuditLog.Action, action_filter):
            qs = qs.filter(action=action_filter)

        if admin_id.isdigit():
            qs = qs.filter(admin_id=int(admin_id))

        if date_str:
            try:
                d_val = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date=d_val)
            except ValueError:
                pass

        all_admins = User.objects.filter(
            Q(role=User.Role.ADMIN) | Q(is_superuser=True)
        ).distinct().order_by('username')

        paginator = Paginator(qs, 25)
        page_num = request.GET.get('page', 1)
        try:
            logs_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            logs_page = paginator.page(1)

        return render(request, 'admin_dashboard/audit_logs.html', {
            'logs': logs_page,
            'all_admins': all_admins,
            'selected_action': action_filter,
            'selected_admin': admin_id,
            'selected_date': date_str,
            'action_choices': AdminAuditLog.Action.choices,
            'total_count': qs.count(),
            'active_menu': 'audit_logs',
            'page_title': 'Audit Logs - TeachLive Admin',
        })


# =====================================================================
# 9. Platform Settings & System Status View
# =====================================================================

class AdminSettingsView(AdminRequiredMixin, View):
    """
    Platform Settings & Health Overview at /admin-dashboard/settings/.
    """
    def get(self, request):
        import sys
        import django

        system_info = {
            'django_version': django.get_version(),
            'python_version': sys.version.split(' ')[0],
            'timezone': str(timezone.get_current_timezone()),
            'is_livekit_configured': is_livekit_configured(),
            'user_count': User.objects.count(),
            'active_sessions': Attendance.objects.filter(status=Attendance.Status.PRESENT, left_at__isnull=True).count(),
        }

        return render(request, 'admin_dashboard/settings.html', {
            'system_info': system_info,
            'active_menu': 'settings',
            'page_title': 'Platform Settings - TeachLive Admin',
        })


# =====================================================================
# 10. Admin Notification & Email Monitoring Views
# =====================================================================

class AdminNotificationListView(AdminRequiredMixin, View):
    """
    In-App Notification Monitoring at /admin-dashboard/notifications/.
    Displays platform-wide notification activity, delivery stats, filtering and search.
    """
    def get(self, request):
        query = request.GET.get('q', '').strip()
        type_filter = request.GET.get('type', '').strip().upper()
        status_filter = request.GET.get('status', '').strip().lower()

        qs = Notification.objects.select_related('recipient', 'related_live_class').order_by('-created_at')

        if query:
            qs = qs.filter(
                Q(title__icontains=query) |
                Q(message__icontains=query) |
                Q(recipient__username__icontains=query) |
                Q(recipient__email__icontains=query)
            )

        if type_filter and type_filter in Notification.Type.values:
            qs = qs.filter(notification_type=type_filter)

        if status_filter == 'unread':
            qs = qs.filter(is_read=False)
        elif status_filter == 'read':
            qs = qs.filter(is_read=True)

        # Overview statistics
        total_notifications = Notification.objects.count()
        unread_notifications = Notification.objects.filter(is_read=False).count()
        today = timezone.now().date()
        today_notifications = Notification.objects.filter(created_at__date=today).count()

        paginator = Paginator(qs, 25)
        page_num = request.GET.get('page', 1)
        try:
            notifications_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            notifications_page = paginator.page(1)

        return render(request, 'admin_dashboard/notifications_list.html', {
            'notifications': notifications_page,
            'total_notifications': total_notifications,
            'unread_notifications': unread_notifications,
            'today_notifications': today_notifications,
            'search_query': query,
            'selected_type': type_filter,
            'selected_status': status_filter,
            'type_choices': Notification.Type.choices,
            'total_count': qs.count(),
            'active_menu': 'notifications',
            'page_title': 'Notifications Log - TeachLive Admin',
        })


class AdminEmailLogListView(AdminRequiredMixin, View):
    """
    Email Delivery Monitoring at /admin-dashboard/emails/.
    Tracks sent and failed emails, delivery rates, and provides search and filters.
    Never exposes SMTP credentials or sensitive secrets.
    """
    def get(self, request):
        query = request.GET.get('q', '').strip()
        status_filter = request.GET.get('status', '').strip().upper()
        type_filter = request.GET.get('type', '').strip()

        qs = EmailLog.objects.select_related('related_live_class').order_by('-sent_at')

        if query:
            qs = qs.filter(
                Q(recipient__icontains=query) |
                Q(subject__icontains=query) |
                Q(error_message__icontains=query)
            )

        if status_filter in [EmailLog.Status.SENT, EmailLog.Status.FAILED]:
            qs = qs.filter(status=status_filter)

        if type_filter:
            qs = qs.filter(email_type=type_filter)

        # Delivery statistics
        total_emails = EmailLog.objects.count()
        sent_emails = EmailLog.objects.filter(status=EmailLog.Status.SENT).count()
        failed_emails = EmailLog.objects.filter(status=EmailLog.Status.FAILED).count()
        success_rate = round((sent_emails / total_emails) * 100, 1) if total_emails > 0 else 100.0

        # Unique email types for filter
        distinct_types = EmailLog.objects.values_list('email_type', flat=True).distinct()[:20]

        paginator = Paginator(qs, 25)
        page_num = request.GET.get('page', 1)
        try:
            emails_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            emails_page = paginator.page(1)

        return render(request, 'admin_dashboard/emails_list.html', {
            'email_logs': emails_page,
            'total_emails': total_emails,
            'sent_emails': sent_emails,
            'failed_emails': failed_emails,
            'success_rate': success_rate,
            'search_query': query,
            'selected_status': status_filter,
            'selected_type': type_filter,
            'distinct_types': distinct_types,
            'total_count': qs.count(),
            'active_menu': 'emails',
            'page_title': 'Email Delivery Logs - TeachLive Admin',
        })

