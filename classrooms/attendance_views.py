"""
Live Class Attendance Views, Reports, and Secure CSV Export for TeachLive.
Enforces strict teacher-level ownership: teachers can only view and export attendance
for their own classes and students. Students are strictly denied access (HTTP 403 Forbidden).
Admins have global visibility.
All datetime operations are timezone-aware (Asia/Kolkata).
"""

import csv
from datetime import timedelta
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Avg, Count, Q, Sum
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from accounts.models import AdminAuditLog, User
from classrooms.attendance_services import (
    calculate_attendance_percentage,
    get_class_attendance_summary,
    get_date_range_bounds,
    get_teacher_attendance_dashboard_metrics,
)
from classrooms.models import Attendance, ClassEnrollment, ClassParticipant, LiveClass

logger = logging.getLogger(__name__)


def sanitize_csv_cell(value) -> str:
    """
    Prevents CSV Formula Injection (CWE-1236).
    If a cell value begins with =, +, -, @, or tab/CR, prepend a single quote (').
    """
    val_str = str(value if value is not None else '').strip()
    if val_str and val_str[0] in ('=', '+', '-', '@', '\t', '\r'):
        return f"'{val_str}"
    return val_str


@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherAttendanceDashboardView(View):
    """
    Teacher Attendance Dashboard at /teacher/attendance/.
    Page title: "Attendance"
    Dashboard cards:
    - Total Classes
    - Completed Classes
    - Total Students
    - Total Attendance Sessions
    - Average Attendance (%)
    - Total Teaching Hours

    Attendance table with search, filters (Class, Student, Date presets/custom, Status),
    pagination, and live attendance display for active classes.
    """
    def get(self, request):
        user = request.user
        if not (user.is_teacher or user.is_admin_role):
            raise PermissionDenied("Access Denied: Only instructors can view teacher attendance reports.")

        # Base QuerySet: strictly filtered by teacher ownership
        if user.is_admin_role:
            teacher_classes = LiveClass.objects.all().order_by('-scheduled_date', '-scheduled_time')
            base_attendances = Attendance.objects.all().select_related('live_class', 'student')
        else:
            teacher_classes = LiveClass.objects.filter(teacher=user).order_by('-scheduled_date', '-scheduled_time')
            base_attendances = Attendance.objects.filter(live_class__teacher=user).select_related('live_class', 'student')

        # Filter parameters
        class_id = request.GET.get('class_id', '').strip()
        student_id = request.GET.get('student_id', '').strip()
        search_query = request.GET.get('q', '').strip() or request.GET.get('student', '').strip()
        status_filter = request.GET.get('status', '').strip().upper()
        date_preset = request.GET.get('date_preset', '').strip().lower()
        date_from_str = request.GET.get('date_from', '').strip()
        date_to_str = request.GET.get('date_to', '').strip()
        # Backward compatibility with single 'date' param
        single_date = request.GET.get('date', '').strip()
        if single_date and not (date_from_str or date_preset):
            date_from_str = single_date
            date_to_str = single_date

        filtered_qs = base_attendances

        # 1. Class filter
        if class_id.isdigit():
            filtered_qs = filtered_qs.filter(live_class_id=int(class_id))

        # 2. Student ID filter
        if student_id.isdigit():
            filtered_qs = filtered_qs.filter(student_id=int(student_id))

        # 3. Date filtering (timezone-aware)
        start_dt, end_dt, date_label = get_date_range_bounds(
            date_preset=date_preset,
            date_from_str=date_from_str,
            date_to_str=date_to_str
        )
        if start_dt:
            filtered_qs = filtered_qs.filter(joined_at__gte=start_dt)
        if end_dt:
            filtered_qs = filtered_qs.filter(joined_at__lte=end_dt)

        # 4. Search query (Student name, Student email, Class title, Subject)
        if search_query:
            filtered_qs = filtered_qs.filter(
                Q(student_name__icontains=search_query) |
                Q(student__email__icontains=search_query) |
                Q(student__username__icontains=search_query) |
                Q(live_class__title__icontains=search_query) |
                Q(live_class__subject__icontains=search_query)
            )

        # 5. Status filter
        if status_filter and hasattr(Attendance.Status, status_filter):
            filtered_qs = filtered_qs.filter(status=status_filter)

        # Dashboard Summary Cards (Prompt #3)
        metrics = get_teacher_attendance_dashboard_metrics(user)

        # Live Attendance: Detect any active live classes for this teacher
        live_classes = teacher_classes.filter(status=LiveClass.Status.LIVE)
        live_sessions = []
        now = timezone.now()
        if live_classes.exists():
            active_participants = ClassParticipant.objects.filter(
                live_class__in=live_classes,
                status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED]
            ).select_related('live_class', 'user').order_by('-is_hand_raised', '-join_time')

            for p in active_participants:
                elapsed_mins = max(1, int(round((now - p.join_time).total_seconds() / 60))) if p.join_time else 1
                live_sessions.append({
                    'participant': p,
                    'student_name': p.student_name,
                    'student_email': p.student_email,
                    'live_class': p.live_class,
                    'joined_at': p.join_time,
                    'current_duration': elapsed_mins,
                    'is_hand_raised': p.is_hand_raised,
                    'is_muted': p.is_muted,
                    'status': p.status,
                })

        # Pagination
        paginator = Paginator(filtered_qs.order_by('-joined_at'), 20)
        page_num = request.GET.get('page', 1)
        try:
            attendances_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            attendances_page = paginator.page(1)

        # Distinct student options for filter dropdown
        teacher_students = (
            User.objects.filter(
                Q(live_attendances__live_class__in=teacher_classes) |
                Q(class_enrollments__live_class__in=teacher_classes)
            )
            .distinct()
            .order_by('first_name', 'username')
        )

        return render(request, 'classrooms/teacher_attendance.html', {
            'page_title': 'Attendance',
            'teacher_classes': teacher_classes,
            'teacher_students': teacher_students,
            'attendances': attendances_page,
            'metrics': metrics,
            'total_classes_count': metrics['total_classes'],
            'completed_classes_count': metrics['completed_classes'],
            'total_students_count': metrics['total_students'],
            'total_records_count': metrics['total_sessions'],
            'avg_attendance_percentage': metrics['avg_attendance_percentage'],
            'total_teaching_hours': metrics['total_teaching_hours'],
            'total_student_attendance_hours': metrics['total_student_attendance_hours'],
            # Preserve filter parameters
            'selected_class_id': class_id,
            'selected_student_id': student_id,
            'selected_search': search_query,
            'selected_status': status_filter,
            'selected_date_preset': date_preset,
            'selected_date_from': date_from_str,
            'selected_date_to': date_to_str,
            'selected_date': single_date,
            'status_choices': Attendance.Status.choices,
            'live_sessions': live_sessions,
            'has_live_classes': live_classes.exists(),
        })


@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class ClassAttendanceDetailView(View):
    """
    Class-specific attendance detail at /teacher/classes/<class_id>/attendance/.
    Shows:
    Class: Title, Subject, Schedule, Duration, Status, Teacher
    Attendance summary:
    - Enrolled students
    - Students joined
    - Students not joined (Absent)
    - Average duration
    - Attendance percentage
    - Total attendance duration
    Attendance table:
    - Student, Email, Join time, Leave time, Duration, Status, Connection state, Attendance %
    - List of absent enrolled students
    Enforces strict teacher ownership (returns 403 Forbidden for non-owners).
    """
    def get(self, request, class_id):
        live_class = get_object_or_404(LiveClass, pk=class_id)
        user = request.user

        # Ownership check
        if live_class.teacher != user and not user.is_admin_role:
            raise PermissionDenied("Access Denied: You do not own this live class.")

        summary = get_class_attendance_summary(live_class)
        attendances = live_class.attendances.select_related('student').order_by('-joined_at')

        # Live participants if currently running
        active_participants = []
        if live_class.is_live:
            active_participants = live_class.participants.filter(
                status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED]
            ).order_by('-is_hand_raised', '-join_time')

        return render(request, 'classrooms/class_attendance_detail.html', {
            'live_class': live_class,
            'summary': summary,
            'attendances': attendances,
            'active_participants': active_participants,
            'total_students': summary['total_joined_sessions'],
            'enrolled_count': summary['enrolled_count'],
            'students_joined_count': summary['students_joined_count'],
            'students_not_joined_count': summary['students_not_joined_count'],
            'absent_enrollments': summary['absent_enrollments'],
            'avg_duration_mins': summary['avg_duration_mins'],
            'total_duration_mins': summary['total_duration_mins'],
            'attendance_percentage': summary['attendance_percentage'],
            'page_title': f"Attendance: {live_class.title} - TeachLive",
        })


@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherStudentAttendanceDetailView(View):
    """
    Teacher Student Attendance Report at /teacher/students/<student_id>/attendance/.
    Section 15:
    Shows:
    Student: Name, Email, Profile photo / avatar
    Summary:
    - Total classes (connected to this teacher)
    - Classes attended
    - Classes missed (where determinable)
    - Total duration
    - Average duration
    - Attendance percentage
    History table:
    - Class, Date, Scheduled duration, Actual duration, Status, Attendance %
    Strict security: Teacher can only access students associated with their classes.
    """
    def get(self, request, student_id):
        user = request.user
        if not (user.is_teacher or user.is_admin_role):
            raise PermissionDenied("Access Denied: Only instructors can view student attendance reports.")

        student = get_object_or_404(User, pk=student_id, role=User.Role.STUDENT)

        # Strict Ownership check: student must be enrolled in or attended at least one of this teacher's classes
        if user.is_admin_role:
            teacher_classes = LiveClass.objects.all()
        else:
            teacher_classes = LiveClass.objects.filter(teacher=user)

        is_connected = (
            ClassEnrollment.objects.filter(live_class__in=teacher_classes, student=student).exists() or
            Attendance.objects.filter(live_class__in=teacher_classes, student=student).exists()
        )
        if not is_connected and not user.is_admin_role:
            raise PermissionDenied("Access Denied: This student is not enrolled in or associated with any of your classes.")

        # Attendances for this teacher's classes
        student_attendances = Attendance.objects.filter(
            live_class__in=teacher_classes,
            student=student
        ).select_related('live_class').order_by('-joined_at')

        # Total classes offered to this student by this teacher
        offered_classes = teacher_classes.filter(
            Q(enrollments__student=student, enrollments__status=ClassEnrollment.Status.ENROLLED) |
            Q(attendances__student=student)
        ).distinct()
        total_classes_count = offered_classes.count()

        # Attended classes count
        attended_sessions_count = student_attendances.filter(
            status__in=[Attendance.Status.PRESENT, Attendance.Status.LEFT]
        ).count()

        # Attended class IDs
        attended_class_ids = set(student_attendances.values_list('live_class_id', flat=True))

        # Classes missed (completed classes where student was enrolled but never attended)
        missed_classes_count = offered_classes.filter(
            status=LiveClass.Status.COMPLETED
        ).exclude(id__in=attended_class_ids).count()

        # Duration aggregation
        agg = student_attendances.aggregate(
            total=Sum('total_duration'),
            avg=Avg('total_duration')
        )
        total_duration_mins = int(agg['total'] or 0)
        avg_duration_mins = int(round(agg['avg'])) if agg['avg'] is not None else 0

        # Overall student attendance percentage for this teacher
        if offered_classes.filter(status=LiveClass.Status.COMPLETED).exists():
            # Percentage based on attended classes vs completed offered classes
            completed_count = offered_classes.filter(status=LiveClass.Status.COMPLETED).count()
            overall_attendance_pct = round((min(completed_count, attended_sessions_count) / max(1, completed_count)) * 100, 1)
        elif attended_sessions_count > 0:
            session_percentages = [
                calculate_attendance_percentage(att.total_duration, att.live_class.duration)
                for att in student_attendances
            ]
            overall_attendance_pct = round(sum(session_percentages) / len(session_percentages), 1)
        else:
            overall_attendance_pct = 0.0

        # Pagination for history table
        paginator = Paginator(student_attendances, 20)
        page_num = request.GET.get('page', 1)
        try:
            attendances_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            attendances_page = paginator.page(1)

        return render(request, 'classrooms/teacher_student_attendance.html', {
            'student': student,
            'attendances': attendances_page,
            'total_classes_count': total_classes_count,
            'attended_sessions_count': attended_sessions_count,
            'missed_classes_count': missed_classes_count,
            'total_duration_mins': total_duration_mins,
            'total_duration_hours': round(total_duration_mins / 60.0, 1),
            'avg_duration_mins': avg_duration_mins,
            'overall_attendance_pct': overall_attendance_pct,
            'page_title': f"Student Attendance: {student.get_full_name() or student.username} - TeachLive",
        })


@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherAttendanceReportsView(View):
    """
    Teacher Comprehensive Attendance Reports at /teacher/reports/attendance/.
    Section 16:
    A. Class Attendance Report
    B. Student Attendance Report
    C. Date Range Report
    D. Attendance Summary
    Filters: Class, Student, Date range, Status.
    """
    def get(self, request):
        user = request.user
        if not (user.is_teacher or user.is_admin_role):
            raise PermissionDenied("Access Denied: Only instructors can view teacher attendance reports.")

        if user.is_admin_role:
            teacher_classes = LiveClass.objects.all().order_by('-scheduled_date', '-scheduled_time')
            base_attendances = Attendance.objects.all().select_related('live_class', 'student')
        else:
            teacher_classes = LiveClass.objects.filter(teacher=user).order_by('-scheduled_date', '-scheduled_time')
            base_attendances = Attendance.objects.filter(live_class__teacher=user).select_related('live_class', 'student')

        # Filter parameters
        class_id = request.GET.get('class_id', '').strip()
        student_query = request.GET.get('student', '').strip()
        status_filter = request.GET.get('status', '').strip().upper()
        date_preset = request.GET.get('date_preset', '').strip().lower()
        date_from_str = request.GET.get('date_from', '').strip()
        date_to_str = request.GET.get('date_to', '').strip()

        filtered_qs = base_attendances
        filtered_classes = teacher_classes

        if class_id.isdigit():
            filtered_qs = filtered_qs.filter(live_class_id=int(class_id))
            filtered_classes = filtered_classes.filter(pk=int(class_id))

        if student_query:
            filtered_qs = filtered_qs.filter(
                Q(student_name__icontains=student_query) |
                Q(student__email__icontains=student_query) |
                Q(student__username__icontains=student_query)
            )

        if status_filter and hasattr(Attendance.Status, status_filter):
            filtered_qs = filtered_qs.filter(status=status_filter)

        start_dt, end_dt, date_label = get_date_range_bounds(
            date_preset=date_preset,
            date_from_str=date_from_str,
            date_to_str=date_to_str
        )
        if start_dt:
            filtered_qs = filtered_qs.filter(joined_at__gte=start_dt)
            filtered_classes = filtered_classes.filter(scheduled_date__gte=start_dt.date())
        if end_dt:
            filtered_qs = filtered_qs.filter(joined_at__lte=end_dt)
            filtered_classes = filtered_classes.filter(scheduled_date__lte=end_dt.date())

        # Section A: Class Attendance Report Breakdown
        class_reports = []
        for c in filtered_classes[:25]:
            summary = get_class_attendance_summary(c)
            class_reports.append({
                'class': c,
                'enrolled_count': summary['enrolled_count'],
                'joined_count': summary['students_joined_count'],
                'absent_count': summary['students_not_joined_count'],
                'avg_duration': summary['avg_duration_mins'],
                'attendance_percentage': summary['attendance_percentage'],
            })

        # Section B: Student Attendance Report Breakdown
        # Aggregated student metrics
        student_reports = []
        student_ids = filtered_qs.exclude(student__isnull=True).values_list('student_id', flat=True).distinct()
        students_qs = User.objects.filter(pk__in=student_ids).order_by('first_name', 'username')[:30]

        for s in students_qs:
            s_attendances = filtered_qs.filter(student=s)
            s_agg = s_attendances.aggregate(
                total_mins=Sum('total_duration'),
                avg_mins=Avg('total_duration')
            )
            s_total_mins = int(s_agg['total_mins'] or 0)
            s_avg_mins = int(round(s_agg['avg_mins'])) if s_agg['avg_mins'] is not None else 0
            s_sessions = s_attendances.count()

            s_pcts = [
                calculate_attendance_percentage(att.total_duration, att.live_class.duration)
                for att in s_attendances
            ]
            s_avg_pct = round(sum(s_pcts) / max(1, len(s_pcts)), 1) if s_pcts else 0.0

            student_reports.append({
                'student': s,
                'sessions_count': s_sessions,
                'total_duration_mins': s_total_mins,
                'total_duration_hours': round(s_total_mins / 60.0, 1),
                'avg_duration_mins': s_avg_mins,
                'attendance_percentage': s_avg_pct,
            })

        # Section D: Attendance Summary KPIs
        metrics = get_teacher_attendance_dashboard_metrics(user)

        # Highest and Lowest Attendance Classes
        highest_class = None
        lowest_class = None
        if class_reports:
            sorted_classes = sorted(class_reports, key=lambda x: x['attendance_percentage'], reverse=True)
            highest_class = sorted_classes[0]
            lowest_class = sorted_classes[-1]

        return render(request, 'classrooms/teacher_attendance_reports.html', {
            'page_title': 'Attendance Reports & Analytics - TeachLive',
            'teacher_classes': teacher_classes,
            'class_reports': class_reports,
            'student_reports': student_reports,
            'metrics': metrics,
            'highest_class': highest_class,
            'lowest_class': lowest_class,
            'selected_class_id': class_id,
            'selected_student': student_query,
            'selected_status': status_filter,
            'selected_date_preset': date_preset,
            'selected_date_from': date_from_str,
            'selected_date_to': date_to_str,
            'date_label': date_label,
            'status_choices': Attendance.Status.choices,
        })


@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherAttendanceExportCSVView(View):
    """
    Exports attendance data as a secure, UTF-8 CSV spreadsheet.
    Section 17:
    CSV Columns:
    - Student Name
    - Student Email
    - Class
    - Subject
    - Scheduled Date
    - Scheduled Start
    - Scheduled Duration
    - Join Time
    - Leave Time
    - Attendance Duration
    - Attendance Status
    - Attendance Percentage
    Enforces strict teacher ownership (students get 403 Forbidden).
    Sanitizes formula injection (=, +, -, @).
    Never exposes passwords, tokens, or private secrets.
    """
    def get(self, request, class_id=None):
        user = request.user
        if not (user.is_teacher or user.is_admin_role):
            return HttpResponseForbidden("Forbidden: Students are not authorized to export attendance records.")

        filename_suffix = timezone.now().strftime('%Y-%m-%d')

        if class_id:
            live_class = get_object_or_404(LiveClass, pk=class_id)
            if live_class.teacher != user and not user.is_admin_role:
                return HttpResponseForbidden("Forbidden: You do not own this live class.")
            qs = live_class.attendances.select_related('live_class', 'student').order_by('student_name')
            filename = f"teachlive_attendance_{live_class.room_code}_{filename_suffix}.csv"
        else:
            if user.is_admin_role:
                qs = Attendance.objects.all().select_related('live_class', 'student').order_by('-joined_at')
            else:
                qs = Attendance.objects.filter(live_class__teacher=user).select_related('live_class', 'student').order_by('-joined_at')

            # Apply filters
            c_id = request.GET.get('class_id', '').strip()
            student_id = request.GET.get('student_id', '').strip()
            search_query = request.GET.get('q', '').strip() or request.GET.get('student', '').strip()
            status_filter = request.GET.get('status', '').strip().upper()
            date_preset = request.GET.get('date_preset', '').strip().lower()
            date_from_str = request.GET.get('date_from', '').strip()
            date_to_str = request.GET.get('date_to', '').strip()
            single_date = request.GET.get('date', '').strip()

            if single_date and not (date_from_str or date_preset):
                date_from_str = single_date
                date_to_str = single_date

            if c_id.isdigit():
                qs = qs.filter(live_class_id=int(c_id))
            if student_id.isdigit():
                qs = qs.filter(student_id=int(student_id))
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

            if search_query:
                qs = qs.filter(
                    Q(student_name__icontains=search_query) |
                    Q(student__email__icontains=search_query) |
                    Q(student__username__icontains=search_query) |
                    Q(live_class__title__icontains=search_query) |
                    Q(live_class__subject__icontains=search_query)
                )

            filename = f"teachlive_attendance_{filename_suffix}.csv"

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        # CSV Header required by Prompt #17
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

        # Security Audit Log for report export
        try:
            AdminAuditLog.log_action(
                user=user,
                action=AdminAuditLog.Action.ATTENDANCE_EXPORTED if hasattr(AdminAuditLog.Action, 'ATTENDANCE_EXPORTED') else 'EXPORT',
                target_model='LiveClass' if class_id else 'Attendance',
                target_id=class_id or 0,
                details=f"Attendance CSV exported by {user.username} ({user.email}). File: {filename}"
            )
        except Exception as log_err:
            logger.debug("Could not record audit log for export: %s", log_err)

        return response
