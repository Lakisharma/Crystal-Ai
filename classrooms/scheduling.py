"""
TeachLive Class Scheduling & Automatic Status Management Service.
Centralized, timezone-aware scheduling engine enforcing server-side business rules:
- India Standard Time (Asia/Kolkata) project-aware datetime calculations.
- Configurable early-start window for teachers (default 15 minutes).
- Configurable early-join window for students (default 10 minutes).
- Strict status transitions: SCHEDULED -> LIVE -> COMPLETED/ENDED, SCHEDULED -> CANCELLED.
- Teacher schedule conflict detection preventing overlapping classes for the same instructor.
- Student overlap permitted without global enrollment blockers.
- Validated rescheduling and cancellation workflows with notifications and audit logging.
- Automatic expiration service for concluded or stale live classes.
"""

from datetime import date, datetime, time, timedelta
import logging
from typing import Optional, Tuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from accounts.models import AdminAuditLog, log_admin_action
from notifications.services import NotificationService

logger = logging.getLogger(__name__)

# Configurable Scheduling Settings
TEACHER_EARLY_START_MINUTES = getattr(settings, 'CLASS_EARLY_START_MINUTES', 15)
STUDENT_EARLY_JOIN_MINUTES = getattr(settings, 'CLASS_EARLY_JOIN_MINUTES', 10)
MIN_CLASS_DURATION_MINUTES = getattr(settings, 'MIN_CLASS_DURATION_MINUTES', 5)
MAX_CLASS_DURATION_MINUTES = getattr(settings, 'MAX_CLASS_DURATION_MINUTES', 360)


def get_current_aware_datetime() -> datetime:
    """Returns the current timezone-aware datetime."""
    return timezone.now()


def make_aware_datetime(target_date: date, target_time: time) -> datetime:
    """
    Combines a date and time into a timezone-aware datetime using the application's active timezone.
    Never returns naive datetimes.
    """
    if not target_date or not target_time:
        raise ValueError("Both target_date and target_time are required.")
    tz = timezone.get_current_timezone()
    naive_dt = datetime.combine(target_date, target_time)
    return timezone.make_aware(naive_dt, tz)


def get_class_status(live_class) -> str:
    """
    Authoritative server-side status string for a LiveClass.
    Returns: 'SCHEDULED', 'READY', 'LIVE', 'ENDED', 'CANCELLED'
    """
    if live_class.status == live_class.Status.CANCELLED:
        return 'CANCELLED'
    if live_class.status in (live_class.Status.COMPLETED, 'ENDED'):
        return 'ENDED'
    if live_class.status == live_class.Status.LIVE:
        return 'LIVE'
    if live_class.status == live_class.Status.SCHEDULED:
        if live_class.is_within_start_window:
            return 'READY'
        return 'SCHEDULED'
    return str(live_class.status)


def can_teacher_start_class(live_class, user) -> Tuple[bool, str, int]:
    """
    Validates whether the instructor may start the class session right now.
    Returns:
        (allowed: bool, reason: str, countdown_seconds_to_start: int)
    """
    is_teacher_owner = (user == live_class.teacher or getattr(user, 'is_admin_role', False))
    if not is_teacher_owner:
        return False, "Access Denied: You are not authorized to start this class.", 0

    if live_class.status == live_class.Status.CANCELLED:
        return False, "This class has been cancelled and cannot be started.", 0

    if live_class.status in (live_class.Status.COMPLETED, 'ENDED'):
        return False, "This class has already ended and cannot be restarted.", 0

    if live_class.status == live_class.Status.LIVE:
        return True, "Class is already LIVE.", 0

    if live_class.status == live_class.Status.SCHEDULED:
        s_dt = live_class.scheduled_datetime
        if not s_dt:
            return True, "Class is ready to start.", 0

        now = timezone.now()
        start_allowed_at = s_dt - timedelta(minutes=TEACHER_EARLY_START_MINUTES)
        countdown_secs = int((s_dt - now).total_seconds())

        if now < start_allowed_at:
            wait_seconds = int((start_allowed_at - now).total_seconds())
            wait_mins = max(1, (wait_seconds + 59) // 60)
            return (
                False,
                f"Class cannot be started yet. Instructors can start classes up to {TEACHER_EARLY_START_MINUTES} minutes before scheduled time (opens in {wait_mins} min).",
                countdown_secs
            )

        return True, "Class is ready to start.", countdown_secs

    return False, f"Invalid class status: {live_class.status}", 0


def can_student_join_class(live_class, user) -> Tuple[bool, str, int]:
    """
    Validates whether a student can enter the class flow or view join availability.
    Returns:
        (allowed: bool, reason: str, countdown_seconds_to_start: int)
    """
    if live_class.status == live_class.Status.CANCELLED:
        return False, "This class has been cancelled.", 0

    if live_class.status in (live_class.Status.COMPLETED, 'ENDED'):
        return False, "This live class has ended.", 0

    s_dt = live_class.scheduled_datetime
    now = timezone.now()
    countdown_secs = int((s_dt - now).total_seconds()) if s_dt else 0

    if live_class.status == live_class.Status.LIVE:
        return True, "Class is LIVE now.", 0

    if live_class.status == live_class.Status.SCHEDULED:
        if not s_dt:
            return False, "Class schedule time is unconfigured.", 0

        join_allowed_at = s_dt - timedelta(minutes=STUDENT_EARLY_JOIN_MINUTES)
        if now < join_allowed_at:
            wait_secs = int((join_allowed_at - now).total_seconds())
            wait_mins = max(1, (wait_secs + 59) // 60)
            return (
                False,
                f"Join is not available yet. Class join opens {STUDENT_EARLY_JOIN_MINUTES} minutes before scheduled start (in {wait_mins} min).",
                countdown_secs
            )

        return True, "Class join window is open. Waiting for instructor to start.", countdown_secs

    return False, f"Cannot join class with status {live_class.status}.", 0


def detect_teacher_schedule_conflict(
    teacher,
    scheduled_date: date,
    scheduled_time: time,
    duration: int,
    exclude_class_id: Optional[int] = None
) -> Tuple[bool, str, Optional[object]]:
    """
    Checks if the proposed class time slot overlaps with an existing scheduled or live
    class hosted by the same teacher.
    Interval overlap rule: [start_a, end_a) overlaps [start_b, end_b) iff
    start_a < end_b and end_a > start_b.
    Returns:
        (has_conflict: bool, error_message: str, conflicting_class: LiveClass or None)
    """
    from classrooms.models import LiveClass

    if not teacher or not scheduled_date or not scheduled_time:
        return False, "", None

    # Calculate proposed start and end aware datetimes
    proposed_start = make_aware_datetime(scheduled_date, scheduled_time)
    proposed_end = proposed_start + timedelta(minutes=duration)

    # Check classes on same date +/- 1 day to handle boundary cases
    candidate_qs = LiveClass.objects.filter(
        teacher=teacher,
        status__in=[LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE],
        scheduled_date__gte=scheduled_date - timedelta(days=1),
        scheduled_date__lte=scheduled_date + timedelta(days=1)
    )

    if exclude_class_id:
        candidate_qs = candidate_qs.exclude(pk=exclude_class_id)

    for existing_class in candidate_qs:
        ext_start = existing_class.scheduled_datetime
        if not ext_start:
            continue
        ext_end = existing_class.expected_end_datetime
        if not ext_end:
            ext_end = ext_start + timedelta(minutes=existing_class.duration or 60)

        # Check interval overlap
        if proposed_start < ext_end and proposed_end > ext_start:
            tz = timezone.get_current_timezone()
            local_ext_start = ext_start.astimezone(tz)
            local_ext_end = ext_end.astimezone(tz)
            msg = (
                f"Schedule conflict: You already have another class '{existing_class.title}' "
                f"scheduled during this time ({local_ext_start.strftime('%I:%M %p')} - {local_ext_end.strftime('%I:%M %p')})."
            )
            return True, msg, existing_class

    return False, "", None


def validate_scheduling_parameters(
    scheduled_date: date,
    scheduled_time: time,
    duration: int,
    is_reschedule: bool = False
) -> None:
    """
    Validates scheduling date/time and duration constraints server-side.
    Raises ValidationError if constraints are violated.
    """
    if not scheduled_date:
        raise ValidationError("Scheduled date is required.")
    if not scheduled_time:
        raise ValidationError("Scheduled time is required.")

    if duration < MIN_CLASS_DURATION_MINUTES:
        raise ValidationError(f"Class duration must be at least {MIN_CLASS_DURATION_MINUTES} minutes.")
    if duration > MAX_CLASS_DURATION_MINUTES:
        raise ValidationError(f"Class duration cannot exceed {MAX_CLASS_DURATION_MINUTES} minutes (6 hours).")

    scheduled_dt = make_aware_datetime(scheduled_date, scheduled_time)
    now = timezone.now()

    # Disallow scheduling in the past (allow a 2-minute buffer for form submission delays)
    if scheduled_dt < (now - timedelta(minutes=2)):
        raise ValidationError("Cannot schedule a class in the past. Please select a future date and time.")


def reschedule_live_class(
    live_class,
    new_date: date,
    new_time: time,
    new_duration: int,
    actor,
    update_summary: str = "",
    request=None
) -> Tuple[bool, str]:
    """
    Reschedules a SCHEDULED LiveClass session safely.
    Strictly forbids rescheduling LIVE, COMPLETED, or CANCELLED classes.
    Verifies teacher ownership, conflict detection, and notifies enrolled students.
    """
    from classrooms.models import LiveClass

    # Permission check
    is_owner = (actor == live_class.teacher or getattr(actor, 'is_admin_role', False))
    if not is_owner:
        return False, "Access Denied: You can only reschedule your own classes."

    # Status check
    if live_class.status == LiveClass.Status.LIVE:
        return False, "Live classes cannot be rescheduled while in progress."
    if live_class.status in (LiveClass.Status.COMPLETED, 'ENDED'):
        return False, "Completed classes cannot be rescheduled."
    if live_class.status == LiveClass.Status.CANCELLED:
        return False, "Cancelled classes cannot be rescheduled."
    if live_class.status != LiveClass.Status.SCHEDULED:
        return False, f"Class cannot be rescheduled with status {live_class.status}."

    # Parameter validation
    try:
        validate_scheduling_parameters(new_date, new_time, new_duration, is_reschedule=True)
    except ValidationError as ve:
        return False, str(ve.message if hasattr(ve, 'message') else ve)

    # Conflict detection
    has_conflict, conflict_msg, _ = detect_teacher_schedule_conflict(
        teacher=live_class.teacher,
        scheduled_date=new_date,
        scheduled_time=new_time,
        duration=new_duration,
        exclude_class_id=live_class.pk
    )
    if has_conflict:
        return False, conflict_msg

    old_date = live_class.scheduled_date
    old_time = live_class.scheduled_time
    old_duration = live_class.duration

    # Apply reschedule
    live_class.scheduled_date = new_date
    live_class.scheduled_time = new_time
    live_class.duration = new_duration
    live_class.save(update_fields=['scheduled_date', 'scheduled_time', 'duration'])

    # Format notification note
    change_summary = (
        f"Rescheduled from {old_date} at {old_time.strftime('%I:%M %p')} ({old_duration}m) "
        f"to {new_date} at {new_time.strftime('%I:%M %p')} ({new_duration}m)."
    )
    if update_summary:
        change_summary += f" Instructor note: {update_summary}"

    # Notify students & teacher
    try:
        NotificationService.notify_class_updated(live_class, update_summary=change_summary)
    except Exception as exc:
        logger.warning("Failed to dispatch reschedule notifications: %s", exc)

    # Record AdminAuditLog
    try:
        log_admin_action(
            admin=actor,
            action=AdminAuditLog.Action.CLASS_RESCHEDULED,
            target_type='LiveClass',
            target_id=str(live_class.pk),
            description=f"Class '{live_class.title}' ({live_class.room_code}) rescheduled: {change_summary}",
            request=request
        )
    except Exception as audit_err:
        logger.warning("Failed to log reschedule audit: %s", audit_err)

    return True, f"Class '{live_class.title}' was successfully rescheduled to {new_date} at {new_time.strftime('%I:%M %p')}."


def cancel_live_class(
    live_class,
    actor,
    reason: str = "",
    request=None
) -> Tuple[bool, str]:
    """
    Cancels a SCHEDULED LiveClass session.
    Strictly forbids cancelling COMPLETED classes.
    Preserves historical attendance, chat, and participants.
    """
    from classrooms.models import LiveClass

    is_owner = (actor == live_class.teacher or getattr(actor, 'is_admin_role', False))
    if not is_owner:
        return False, "Access Denied: You can only cancel your own classes."

    if live_class.status == LiveClass.Status.COMPLETED or live_class.is_ended:
        return False, "Completed classes cannot be cancelled."

    if live_class.status == LiveClass.Status.CANCELLED:
        return False, "This class is already cancelled."

    if live_class.status == LiveClass.Status.LIVE:
        return False, "Cannot cancel a class that is currently LIVE. Please end the class instead."

    # Mark cancelled
    live_class.status = LiveClass.Status.CANCELLED
    live_class.save(update_fields=['status'])

    # Notify students and teacher
    try:
        NotificationService.notify_class_cancelled(live_class, cancelled_by=actor)
    except Exception as exc:
        logger.warning("Failed to dispatch cancellation notifications: %s", exc)

    # Record audit log
    try:
        desc = f"Class '{live_class.title}' ({live_class.room_code}) cancelled by {actor.username}."
        if reason:
            desc += f" Reason: {reason}"
        log_admin_action(
            admin=actor,
            action=AdminAuditLog.Action.CLASS_CANCELLED,
            target_type='LiveClass',
            target_id=str(live_class.pk),
            description=desc,
            request=request
        )
    except Exception as audit_err:
        logger.warning("Failed to log cancel audit: %s", audit_err)

    return True, f"Class '{live_class.title}' ({live_class.room_code}) has been successfully cancelled."


def mark_expired_classes_ended() -> int:
    """
    Sweeps the database for active classes that have exceeded their expected duration.
    Server-side authoritative expiration handler:
    - Finds LIVE classes where now > started_at + duration + 15 min buffer.
    - Transitions them to COMPLETED.
    - Sets ended_at timestamp.
    - Dispatches completion notifications.
    Returns count of updated classes.
    """
    from classrooms.models import LiveClass

    now = timezone.now()
    # Query LIVE classes
    live_classes = LiveClass.objects.filter(status=LiveClass.Status.LIVE).select_related('teacher')
    ended_count = 0

    for lc in live_classes:
        exp_end = lc.expected_end_datetime
        if not exp_end:
            continue
        # Allow 15-minute grace period beyond expected duration before auto-closing
        hard_cutoff = exp_end + timedelta(minutes=15)
        if now > hard_cutoff:
            lc.end_class()
            try:
                NotificationService.notify_class_ended(lc)
            except Exception as e:
                logger.warning("Failed to send auto-end notification for %s: %s", lc.room_code, e)
            ended_count += 1
            logger.info("mark_expired_classes_ended: Concluded expired live class %s (%s)", lc.title, lc.room_code)

    return ended_count
