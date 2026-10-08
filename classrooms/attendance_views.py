"""
Live Class Attendance Views, Reports, and Secure CSV Export for TeachLive.
Enforces strict teacher-level ownership: teachers can only view and export attendance
for their own classes. Students are strictly denied access (HTTP 403 Forbidden).
"""

import csv
from datetime import timedelta
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, Q
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from classrooms.models import Attendance, ClassParticipant, LiveClass

logger = logging.getLogger(__name__)


def sanitize_csv_cell(value) -> str:
    """
    Prevents CSV Formula Injection (CWE-1236).
    If a cell value begins with =, +, -, @, or tab/CR, prepend a single quote (').
    """
    val_str = str(value or '').strip()
    if val_str and val_str[0] in ('=', '+', '-', '@', '\t', '\r'):
        return f"'{val_str}"
    return val_str


@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherAttendanceDashboardView(View):
    """
    Teacher Attendance Dashboard at /teacher/attendance/.
    Displays attendance records for classes belonging strictly to the logged-in teacher.
    Provides filter controls (Class, Date, Student, Status) and real database metrics.
    Students attempting to access receive HTTP 403 Forbidden.
    """
    def get(self, request):
        user = request.user
        # Strict Role & Ownership check
        if not (user.is_teacher or user.is_admin_role):
            raise PermissionDenied("Access Denied: Only instructors can view teacher attendance reports.")

        # Base QuerySet: strictly filtered by teacher ownership
        if user.is_admin_role:
            teacher_classes = LiveClass.objects.all()
            base_attendances = Attendance.objects.all().select_related('live_class', 'student')
        else:
            teacher_classes = LiveClass.objects.filter(teacher=user)
            base_attendances = Attendance.objects.filter(live_class__teacher=user).select_related('live_class', 'student')

        # Filter parameters
        class_id = request.GET.get('class_id', '').strip()
        date_str = request.GET.get('date', '').strip()
        student_query = request.GET.get('student', '').strip()
        status_filter = request.GET.get('status', '').strip()

        filtered_qs = base_attendances

        if class_id.isdigit():
            filtered_qs = filtered_qs.filter(live_class_id=int(class_id))

        if date_str:
            try:
                date_val = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
                filtered_qs = filtered_qs.filter(joined_at__date=date_val)
            except ValueError:
                pass

        if student_query:
            filtered_qs = filtered_qs.filter(
                Q(student_name__icontains=student_query) |
                Q(student__email__icontains=student_query) |
                Q(student__username__icontains=student_query)
            )

        if status_filter:
            filtered_qs = filtered_qs.filter(status__iexact=status_filter)

        # Real database metric calculations (Section 12: ATTENDANCE DASHBOARD SUMMARY)
        total_classes_count = teacher_classes.count()
        total_records_count = base_attendances.count()
        total_distinct_students = base_attendances.values('student_name').distinct().count()

        avg_duration_metric = base_attendances.aggregate(avg=Avg('total_duration'))['avg']
        avg_duration_minutes = int(round(avg_duration_metric)) if avg_duration_metric is not None else 0

        # Attendance Rate: percentage of completed/present attendances vs total records
        if total_records_count > 0:
            present_records = base_attendances.filter(status__in=[Attendance.Status.PRESENT, Attendance.Status.LEFT]).count()
            attendance_rate = round((present_records / total_records_count) * 100, 1)
        else:
            attendance_rate = 100.0 if total_classes_count > 0 else 0.0

        attendances_list = filtered_qs.order_by('-joined_at')[:100]

        return render(request, 'classrooms/teacher_attendance.html', {
            'teacher_classes': teacher_classes,
            'attendances': attendances_list,
            'total_classes_count': total_classes_count,
            'total_records_count': total_records_count,
            'total_distinct_students': total_distinct_students,
            'avg_duration_minutes': avg_duration_minutes,
            'attendance_rate': attendance_rate,
            'selected_class_id': class_id,
            'selected_date': date_str,
            'selected_student': student_query,
            'selected_status': status_filter,
            'status_choices': Attendance.Status.choices,
            'page_title': 'Attendance Reports - TeachLive',
        })


@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class ClassAttendanceDetailView(View):
    """
    Class-specific attendance detail at /teacher/classes/<class_id>/attendance/.
    Shows attendance metrics and roster for an individual live class.
    Enforces strict teacher ownership (returns 403 Forbidden for non-owners).
    """
    def get(self, request, class_id):
        live_class = get_object_or_404(LiveClass, pk=class_id)
        user = request.user

        # Ownership check
        if live_class.teacher != user and not user.is_admin_role:
            raise PermissionDenied("Access Denied: You do not own this live class.")

        attendances = live_class.attendances.select_related('student').order_by('-joined_at')

        total_students = attendances.count()
        present_count = attendances.filter(status=Attendance.Status.PRESENT).count()
        left_count = attendances.filter(status=Attendance.Status.LEFT).count()
        disconnected_count = attendances.filter(status=Attendance.Status.DISCONNECTED).count()

        avg_duration = attendances.aggregate(avg=Avg('total_duration'))['avg']
        avg_duration_mins = int(round(avg_duration)) if avg_duration is not None else 0

        return render(request, 'classrooms/class_attendance_detail.html', {
            'live_class': live_class,
            'attendances': attendances,
            'total_students': total_students,
            'present_count': present_count,
            'left_count': left_count,
            'disconnected_count': disconnected_count,
            'avg_duration_mins': avg_duration_mins,
            'page_title': f"Attendance: {live_class.title} - TeachLive",
        })


@method_decorator(login_required(login_url='accounts:teacher_login'), name='dispatch')
class TeacherAttendanceExportCSVView(View):
    """
    Exports attendance data as a secure, UTF-8 CSV spreadsheet.
    Supports exporting all teacher classes or a single class.
    Enforces strict teacher ownership (students get 403 Forbidden).
    Sanitizes formula injection (=, +, -, @).
    Never exposes passwords, Google IDs, or private auth secrets.
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

            # Apply dashboard filter params if present
            c_id = request.GET.get('class_id', '').strip()
            date_str = request.GET.get('date', '').strip()
            student_query = request.GET.get('student', '').strip()
            status_filter = request.GET.get('status', '').strip()

            if c_id.isdigit():
                qs = qs.filter(live_class_id=int(c_id))
            if date_str:
                try:
                    d_val = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
                    qs = qs.filter(joined_at__date=d_val)
                except ValueError:
                    pass
            if student_query:
                qs = qs.filter(
                    Q(student_name__icontains=student_query) |
                    Q(student__email__icontains=student_query) |
                    Q(student__username__icontains=student_query)
                )
            if status_filter:
                qs = qs.filter(status__iexact=status_filter)

            filename = f"teachlive_attendance_{filename_suffix}.csv"

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        # CSV Header
        writer.writerow([
            'Student Name',
            'Student Email',
            'Class Title',
            'Subject',
            'Room Code',
            'Join Time',
            'Leave Time',
            'Total Duration (mins)',
            'Status',
        ])

        tz = timezone.get_current_timezone()

        for att in qs:
            student_email = ''
            if att.student and att.student.email:
                student_email = att.student.email
            else:
                # Check participant record
                p = ClassParticipant.objects.filter(live_class=att.live_class, student_name=att.student_name).first()
                if p and p.student_email:
                    student_email = p.student_email

            join_time_str = att.joined_at.astimezone(tz).strftime('%Y-%m-%d %H:%M') if att.joined_at else ''
            leave_time_str = att.left_at.astimezone(tz).strftime('%Y-%m-%d %H:%M') if att.left_at else 'In Progress / Active'

            writer.writerow([
                sanitize_csv_cell(att.student_name),
                sanitize_csv_cell(student_email),
                sanitize_csv_cell(att.live_class.title),
                sanitize_csv_cell(att.live_class.subject),
                sanitize_csv_cell(att.live_class.room_code),
                join_time_str,
                leave_time_str,
                att.total_duration,
                sanitize_csv_cell(att.get_status_display()),
            ])

        return response
