"""
TeachLive Attendance Services & Calculations.
Provides standardized, timezone-aware attendance calculations, aggregations,
reconnect duration tracking, and reporting analytics.
Primary timezone: Asia/Kolkata.
"""

from datetime import date, datetime, time, timedelta
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from classrooms.models import Attendance, ClassEnrollment, ClassParticipant, LiveClass

logger = logging.getLogger(__name__)


def calculate_attendance_percentage(
    attended_duration_minutes: int,
    scheduled_duration_minutes: int
) -> float:
    """
    Attendance % Calculation Formula (Prompt #16 Section 10):
        Attendance % = (attended duration / scheduled duration) * 100

    Rules:
    - Never exceed 100.0%
    - Never go below 0.0%
    - Avoid division by zero
    - Use server-side timestamps and durations
    - If scheduled duration is 0 or undefined, fallback to 100.0% if attended > 0, else 0.0%
    - Returns float rounded to 1 decimal place.
    """
    try:
        attended = int(attended_duration_minutes or 0)
    except (ValueError, TypeError):
        attended = 0

    try:
        scheduled = int(scheduled_duration_minutes or 0)
    except (ValueError, TypeError):
        scheduled = 0

    if attended <= 0:
        return 0.0

    if scheduled <= 0:
        return 100.0

    raw_percentage = (attended / scheduled) * 100.0
    bounded = max(0.0, min(100.0, round(raw_percentage, 1)))
    return bounded


def get_date_range_bounds(
    date_preset: str = '',
    date_from_str: str = '',
    date_to_str: str = '',
    tz=None
) -> Tuple[Optional[datetime], Optional[datetime], str]:
    """
    Parses preset ('today', 'yesterday', 'this_week', 'this_month') or custom range.
    Returns timezone-aware (start_datetime, end_datetime, label).
    """
    if tz is None:
        tz = timezone.get_current_timezone()

    now = timezone.now().astimezone(tz)
    today = now.date()

    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    label = 'All Dates'

    clean_preset = (date_preset or '').strip().lower()

    if clean_preset == 'today':
        start_dt = timezone.make_aware(datetime.combine(today, time.min), tz)
        end_dt = timezone.make_aware(datetime.combine(today, time.max), tz)
        label = 'Today'
    elif clean_preset == 'yesterday':
        yesterday = today - timedelta(days=1)
        start_dt = timezone.make_aware(datetime.combine(yesterday, time.min), tz)
        end_dt = timezone.make_aware(datetime.combine(yesterday, time.max), tz)
        label = 'Yesterday'
    elif clean_preset == 'this_week':
        # Monday to Sunday of current week
        start_day = today - timedelta(days=today.weekday())
        end_day = start_day + timedelta(days=6)
        start_dt = timezone.make_aware(datetime.combine(start_day, time.min), tz)
        end_dt = timezone.make_aware(datetime.combine(end_day, time.max), tz)
        label = 'This Week'
    elif clean_preset == 'this_month':
        start_day = today.replace(day=1)
        # Next month start - 1 day
        if today.month == 12:
            next_month = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month = today.replace(month=today.month + 1, day=1)
        end_day = next_month - timedelta(days=1)
        start_dt = timezone.make_aware(datetime.combine(start_day, time.min), tz)
        end_dt = timezone.make_aware(datetime.combine(end_day, time.max), tz)
        label = 'This Month'
    elif date_from_str or date_to_str:
        if date_from_str:
            try:
                d_from = datetime.strptime(date_from_str.strip(), '%Y-%m-%d').date()
                start_dt = timezone.make_aware(datetime.combine(d_from, time.min), tz)
            except ValueError:
                pass
        if date_to_str:
            try:
                d_to = datetime.strptime(date_to_str.strip(), '%Y-%m-%d').date()
                end_dt = timezone.make_aware(datetime.combine(d_to, time.max), tz)
            except ValueError:
                pass
        label = f"{date_from_str or 'Start'} to {date_to_str or 'Now'}"

    return start_dt, end_dt, label


def get_class_attendance_summary(live_class: LiveClass) -> Dict[str, Any]:
    """
    Computes attendance summary metrics for a single live class:
    - Enrolled students
    - Students joined
    - Students not joined (Absent)
    - Average duration
    - Attendance percentage
    - Total attendance duration
    - List of absent students
    """
    # 1. Enrolled students
    enrolled_qs = ClassEnrollment.objects.filter(
        live_class=live_class,
        status=ClassEnrollment.Status.ENROLLED
    ).select_related('student')
    enrolled_count = enrolled_qs.count()
    enrolled_user_ids = set(enrolled_qs.values_list('student_id', flat=True))

    # 2. Joined records
    attendances = live_class.attendances.select_related('student').order_by('-joined_at')
    total_joined_sessions = attendances.count()

    joined_user_ids: Set[int] = set()
    for att in attendances:
        if att.student_id:
            joined_user_ids.add(att.student_id)

    # Distinct students joined
    students_joined_count = len(joined_user_ids) if joined_user_ids else total_joined_sessions

    # 3. Absent (Enrolled students who never joined)
    absent_user_ids = enrolled_user_ids - joined_user_ids
    absent_enrollments = [e for e in enrolled_qs if e.student_id in absent_user_ids]
    students_not_joined_count = len(absent_enrollments)

    # 4. Total and Average duration
    agg = attendances.aggregate(
        total=Sum('total_duration'),
        avg=Avg('total_duration')
    )
    total_duration_mins = int(agg['total'] or 0)
    avg_duration_mins = int(round(agg['avg'])) if agg['avg'] is not None else 0

    # 5. Class Attendance Percentage
    scheduled_duration = live_class.duration or 60
    if enrolled_count > 0:
        # Based on enrolled student attendance
        # Sum of attendance percentages across enrolled students divided by enrolled_count
        student_percentages = []
        for att in attendances:
            student_percentages.append(calculate_attendance_percentage(att.total_duration, scheduled_duration))
        # Absent enrolled students contribute 0.0%
        absent_zeros = [0.0] * students_not_joined_count
        all_enrolled_pcts = student_percentages + absent_zeros
        if all_enrolled_pcts:
            class_attendance_pct = round(sum(all_enrolled_pcts) / max(1, len(all_enrolled_pcts)), 1)
        else:
            class_attendance_pct = 0.0
    else:
        # If open public link class without formal enrollment:
        if total_joined_sessions > 0:
            student_percentages = [
                calculate_attendance_percentage(att.total_duration, scheduled_duration)
                for att in attendances
            ]
            class_attendance_pct = round(sum(student_percentages) / total_joined_sessions, 1)
        else:
            class_attendance_pct = 0.0

    return {
        'enrolled_count': enrolled_count,
        'students_joined_count': students_joined_count,
        'students_not_joined_count': students_not_joined_count,
        'absent_enrollments': absent_enrollments,
        'avg_duration_mins': avg_duration_mins,
        'total_duration_mins': total_duration_mins,
        'total_duration_hours': round(total_duration_mins / 60.0, 1),
        'attendance_percentage': class_attendance_pct,
        'scheduled_duration': scheduled_duration,
        'total_joined_sessions': total_joined_sessions,
    }


def get_teacher_attendance_dashboard_metrics(teacher_user) -> Dict[str, Any]:
    """
    Computes verified real database metrics for Teacher Attendance Dashboard:
    - Total Classes
    - Completed Classes
    - Total Students
    - Total Attendance Sessions
    - Average Attendance (%)
    - Total Teaching Hours
    Respects teacher ownership: only classes belonging strictly to teacher_user.
    """
    if teacher_user.is_admin_role:
        teacher_classes = LiveClass.objects.all()
        base_attendances = Attendance.objects.all().select_related('live_class')
    else:
        teacher_classes = LiveClass.objects.filter(teacher=teacher_user)
        base_attendances = Attendance.objects.filter(live_class__teacher=teacher_user).select_related('live_class')

    total_classes = teacher_classes.count()
    completed_classes = teacher_classes.filter(status=LiveClass.Status.COMPLETED).count()

    # Total distinct students connected to teacher's classes (via enrollments or attendances)
    attendance_student_ids = set(base_attendances.exclude(student__isnull=True).values_list('student_id', flat=True))
    attendance_student_names = set(base_attendances.filter(student__isnull=True).values_list('student_name', flat=True))
    total_distinct_students = len(attendance_student_ids) + len(attendance_student_names)

    total_sessions = base_attendances.count()

    # Average Attendance Percentage across all sessions
    if total_sessions > 0:
        session_percentages = [
            calculate_attendance_percentage(att.total_duration, att.live_class.duration)
            for att in base_attendances
        ]
        avg_attendance_pct = round(sum(session_percentages) / total_sessions, 1)
    else:
        avg_attendance_pct = 0.0

    # Total Teaching Hours
    # Computed from completed classes duration sum or actual duration
    total_teaching_mins = teacher_classes.filter(
        status__in=[LiveClass.Status.COMPLETED, LiveClass.Status.LIVE]
    ).aggregate(total_mins=Sum('duration'))['total_mins'] or 0
    total_teaching_hours = round(total_teaching_mins / 60.0, 1)

    # Total student attendance hours
    total_student_mins = base_attendances.aggregate(total_mins=Sum('total_duration'))['total_mins'] or 0
    total_student_attendance_hours = round(total_student_mins / 60.0, 1)

    return {
        'total_classes': total_classes,
        'completed_classes': completed_classes,
        'total_students': total_distinct_students,
        'total_sessions': total_sessions,
        'avg_attendance_percentage': avg_attendance_pct,
        'total_teaching_hours': total_teaching_hours,
        'total_student_attendance_hours': total_student_attendance_hours,
    }


def get_student_attendance_metrics(student_user, date_from=None, date_to=None) -> Dict[str, Any]:
    """
    Computes attendance metrics strictly for student_user:
    - Classes attended count
    - Total attendance duration
    - Average session duration
    - Overall attendance percentage
    """
    qs = Attendance.objects.filter(student=student_user).select_related('live_class')

    if date_from:
        qs = qs.filter(joined_at__gte=date_from)
    if date_to:
        qs = qs.filter(joined_at__lte=date_to)

    total_attended_sessions = qs.count()
    distinct_classes_count = qs.values('live_class_id').distinct().count()

    agg = qs.aggregate(
        total_mins=Sum('total_duration'),
        avg_mins=Avg('total_duration')
    )
    total_mins = int(agg['total_mins'] or 0)
    avg_session_mins = int(round(agg['avg_mins'])) if agg['avg_mins'] is not None else 0

    if total_attended_sessions > 0:
        session_percentages = [
            calculate_attendance_percentage(att.total_duration, att.live_class.duration)
            for att in qs
        ]
        overall_attendance_pct = round(sum(session_percentages) / total_attended_sessions, 1)
    else:
        overall_attendance_pct = 0.0

    return {
        'classes_attended': distinct_classes_count,
        'total_sessions': total_attended_sessions,
        'total_duration_mins': total_mins,
        'total_duration_hours': round(total_mins / 60.0, 1),
        'avg_session_mins': avg_session_mins,
        'attendance_percentage': overall_attendance_pct,
    }
