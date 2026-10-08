from datetime import date, datetime, timedelta
import logging

from django.utils import timezone

from .email_service import EmailService
from .models import Notification

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Core business logic service for creating in-app notifications and triggering
    associated transactional email alerts for TeachLive users.
    Enforces deduplication, recipient isolation, and safe execution.
    """

    @classmethod
    def create_notification(
        cls,
        recipient,
        notification_type: str,
        title: str,
        message: str,
        related_live_class=None,
        check_duplicate_minutes: int = 5
    ) -> Notification:
        """
        Creates an in-app Notification for the specified recipient.
        If check_duplicate_minutes > 0, skips creation if an identical notification
        was already recorded within that time window.
        """
        if not recipient:
            return None

        try:
            if check_duplicate_minutes > 0:
                recent_cutoff = timezone.now() - timedelta(minutes=check_duplicate_minutes)
                existing = Notification.objects.filter(
                    recipient=recipient,
                    notification_type=notification_type,
                    related_live_class=related_live_class,
                    created_at__gte=recent_cutoff
                ).first()
                if existing:
                    logger.debug(
                        "NotificationService: Duplicate notification skipped for user %s (type=%s)",
                        recipient.username,
                        notification_type
                    )
                    return existing

            notification = Notification.objects.create(
                recipient=recipient,
                notification_type=notification_type,
                title=title,
                message=message,
                related_live_class=related_live_class
            )
            return notification
        except Exception as exc:
            logger.error("NotificationService: Failed to create notification: %s", exc)
            return None

    # -------------------------------------------------------------
    # Live Class Lifecycle Notification Handlers
    # -------------------------------------------------------------

    @classmethod
    def notify_class_created(cls, live_class) -> Notification:
        """
        Notifies instructor that their scheduled live class session has been registered.
        Also triggers confirmation email to the instructor.
        """
        teacher = live_class.teacher
        title = f"Class Scheduled: {live_class.title}"
        message = (
            f"Your live class '{live_class.title}' ({live_class.subject}) has been successfully scheduled "
            f"for {live_class.scheduled_date.strftime('%B %d, %Y')} at "
            f"{live_class.scheduled_time.strftime('%I:%M %p')}. "
            f"Room Code: {live_class.room_code}"
        )

        notif = cls.create_notification(
            recipient=teacher,
            notification_type=Notification.Type.CLASS_CREATED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=2
        )

        # Trigger confirmation email
        EmailService.send_class_created_email(live_class)
        return notif

    @classmethod
    def notify_class_updated(cls, live_class, update_summary: str = "") -> list:
        """
        Notifies instructor and any enrolled/registered participants about class updates.
        """
        notifications = []
        teacher = live_class.teacher
        title = f"Class Updated: {live_class.title}"
        message = (
            f"Schedule or details for '{live_class.title}' have been modified. "
            f"Current Schedule: {live_class.scheduled_date} at {live_class.scheduled_time}. "
            f"Room Code: {live_class.room_code}."
        )
        if update_summary:
            message += f" Note: {update_summary}"

        # 1. Notify Teacher
        notif_teacher = cls.create_notification(
            recipient=teacher,
            notification_type=Notification.Type.TEACHER_CLASS_UPDATE,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=1
        )
        if notif_teacher:
            notifications.append(notif_teacher)
        EmailService.send_class_updated_email(live_class, teacher, update_summary)

        # 2. Notify registered participants (if any)
        participants = live_class.participants.filter(user__isnull=False).select_related('user')
        seen_user_ids = {teacher.id}

        for part in participants:
            user = part.user
            if user.id not in seen_user_ids:
                seen_user_ids.add(user.id)
                n = cls.create_notification(
                    recipient=user,
                    notification_type=Notification.Type.TEACHER_CLASS_UPDATE,
                    title=title,
                    message=message,
                    related_live_class=live_class,
                    check_duplicate_minutes=1
                )
                if n:
                    notifications.append(n)
                EmailService.send_class_updated_email(live_class, user, update_summary)

        return notifications

    @classmethod
    def notify_class_cancelled(cls, live_class, cancelled_by=None) -> list:
        """
        Notifies instructor and enrolled students about class cancellation.
        """
        notifications = []
        teacher = live_class.teacher
        canceller_name = (
            getattr(cancelled_by, 'get_full_name', lambda: None)()
            or getattr(cancelled_by, 'username', 'Administrator')
            if cancelled_by else 'Administrator'
        )

        title = f"Class Cancelled: {live_class.title}"
        message = (
            f"The live class '{live_class.title}' scheduled for "
            f"{live_class.scheduled_date} at {live_class.scheduled_time} "
            f"has been cancelled by {canceller_name}."
        )

        # 1. Notify Teacher
        notif_teacher = cls.create_notification(
            recipient=teacher,
            notification_type=Notification.Type.CLASS_CANCELLED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=1
        )
        if notif_teacher:
            notifications.append(notif_teacher)
        EmailService.send_class_cancelled_email(live_class, teacher, canceller_name)

        # 2. Notify registered participants if any exist
        participants = live_class.participants.filter(user__isnull=False).select_related('user')
        seen_user_ids = {teacher.id}

        for part in participants:
            user = part.user
            if user.id not in seen_user_ids:
                seen_user_ids.add(user.id)
                n = cls.create_notification(
                    recipient=user,
                    notification_type=Notification.Type.CLASS_CANCELLED,
                    title=title,
                    message=message,
                    related_live_class=live_class,
                    check_duplicate_minutes=1
                )
                if n:
                    notifications.append(n)
                EmailService.send_class_cancelled_email(live_class, user, canceller_name)

        return notifications

    @classmethod
    def notify_class_started(cls, live_class) -> list:
        """
        Creates notification when a live class transitions to LIVE.
        Guards against duplicate notifications during WebSocket reconnects.
        """
        notifications = []
        teacher = live_class.teacher
        title = f"Live Now: {live_class.title}"
        message = (
            f"Professor {teacher.get_full_name() or teacher.username} has started the live class "
            f"'{live_class.title}'. You can now join the room using Room Code: {live_class.room_code}."
        )

        # Notify participants
        participants = live_class.participants.filter(user__isnull=False).select_related('user')
        seen_user_ids = {teacher.id}

        for part in participants:
            user = part.user
            if user.id not in seen_user_ids:
                seen_user_ids.add(user.id)
                n = cls.create_notification(
                    recipient=user,
                    notification_type=Notification.Type.CLASS_STARTED,
                    title=title,
                    message=message,
                    related_live_class=live_class,
                    check_duplicate_minutes=60  # prevent spam on reconnects
                )
                if n:
                    notifications.append(n)

        return notifications

    @classmethod
    def notify_class_ended(cls, live_class) -> list:
        """
        Creates notification when a live class is concluded.
        """
        notifications = []
        teacher = live_class.teacher
        title = f"Class Concluded: {live_class.title}"
        message = f"The live session for '{live_class.title}' has ended. Attendance records have been compiled."

        # Teacher notification
        notif_teacher = cls.create_notification(
            recipient=teacher,
            notification_type=Notification.Type.CLASS_ENDED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=30
        )
        if notif_teacher:
            notifications.append(notif_teacher)

        # Participant notifications
        participants = live_class.participants.filter(user__isnull=False).select_related('user')
        seen_user_ids = {teacher.id}

        for part in participants:
            user = part.user
            if user.id not in seen_user_ids:
                seen_user_ids.add(user.id)
                n = cls.create_notification(
                    recipient=user,
                    notification_type=Notification.Type.CLASS_ENDED,
                    title=title,
                    message=message,
                    related_live_class=live_class,
                    check_duplicate_minutes=30
                )
                if n:
                    notifications.append(n)

        return notifications

    @classmethod
    def notify_student_joined(cls, live_class, student_user, student_name: str) -> Notification:
        """
        Notifies teacher when a student enters the live classroom.
        De-duplicates to avoid multiple notifications if student reconnects.
        """
        teacher = live_class.teacher
        title = f"Student Joined: {student_name}"
        message = f"{student_name} entered the live classroom for '{live_class.title}'."

        return cls.create_notification(
            recipient=teacher,
            notification_type=Notification.Type.STUDENT_JOINED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=15
        )

    # -------------------------------------------------------------
    # Class Enrollment & Access Control Notifications
    # -------------------------------------------------------------

    @classmethod
    def notify_student_invited(cls, live_class, student) -> Notification:
        """
        Notifies a student that they have been invited to a live class.
        Also dispatches invitation email.
        """
        title = "You have been invited to join a TeachLive class"
        message = (
            f"Professor {live_class.teacher.get_full_name() or live_class.teacher.username} "
            f"has invited you to join '{live_class.title}' ({live_class.subject}). "
            f"Scheduled for {live_class.scheduled_date} at {live_class.scheduled_time}."
        )

        notif = cls.create_notification(
            recipient=student,
            notification_type=Notification.Type.STUDENT_INVITED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=5
        )
        EmailService.send_class_invitation_email(live_class, student)
        return notif

    @classmethod
    def notify_enrollment_approved(cls, live_class, student) -> Notification:
        """
        Notifies a student that their access request has been approved.
        """
        title = "Your access to the class has been approved"
        message = (
            f"Your request to join '{live_class.title}' ({live_class.subject}) "
            f"has been approved. You may now enter the live classroom when active."
        )

        notif = cls.create_notification(
            recipient=student,
            notification_type=Notification.Type.ENROLLMENT_APPROVED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=2
        )
        EmailService.send_access_approved_email(live_class, student)
        return notif

    @classmethod
    def notify_enrollment_rejected(cls, live_class, student) -> Notification:
        """
        Notifies a student that their access request was rejected.
        """
        title = "Your request to join the class was rejected"
        message = (
            f"Your request to join '{live_class.title}' ({live_class.subject}) "
            f"was reviewed and could not be approved by the instructor."
        )

        notif = cls.create_notification(
            recipient=student,
            notification_type=Notification.Type.ENROLLMENT_REJECTED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=2
        )
        EmailService.send_access_rejected_email(live_class, student)
        return notif

    @classmethod
    def notify_enrollment_revoked(cls, live_class, student) -> Notification:
        """
        Notifies a student that their access to a class has been revoked.
        """
        title = "Your access to this class has been revoked"
        message = (
            f"Your access to live class '{live_class.title}' ({live_class.subject}) "
            f"has been revoked by the instructor."
        )

        notif = cls.create_notification(
            recipient=student,
            notification_type=Notification.Type.ENROLLMENT_REVOKED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=2
        )
        EmailService.send_access_revoked_email(live_class, student)
        return notif

    @classmethod
    def notify_access_requested(cls, live_class, student, request_note: str = "") -> Notification:
        """
        Notifies the instructor that a student has submitted an access request for an ENROLLMENT_ONLY class.
        """
        teacher = live_class.teacher
        student_name = student.get_full_name() or student.username
        title = f"Access Request: {student_name}"
        message = (
            f"{student_name} ({student.email}) has requested access to join '{live_class.title}'. "
        )
        if request_note:
            message += f"Note: \"{request_note}\""

        return cls.create_notification(
            recipient=teacher,
            notification_type=Notification.Type.ACCESS_REQUESTED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=5
        )

    @classmethod
    def notify_invitation_accepted(cls, live_class, student) -> Notification:
        """Notifies the instructor that a student accepted the class invitation."""
        teacher = live_class.teacher
        student_name = student.get_full_name() or student.username
        title = f"Invitation Accepted: {student_name}"
        message = f"{student_name} ({student.email}) has accepted the invitation to '{live_class.title}'."
        return cls.create_notification(
            recipient=teacher,
            notification_type=Notification.Type.INVITATION_ACCEPTED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=2
        )

    @classmethod
    def notify_invitation_declined(cls, live_class, student) -> Notification:
        """Notifies the instructor that a student declined the class invitation."""
        teacher = live_class.teacher
        student_name = student.get_full_name() or student.username
        title = f"Invitation Declined: {student_name}"
        message = f"{student_name} ({student.email}) has declined the invitation to '{live_class.title}'."
        return cls.create_notification(
            recipient=teacher,
            notification_type=Notification.Type.INVITATION_DECLINED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=2
        )

    @classmethod
    def notify_student_reinvited(cls, live_class, student) -> Notification:
        """Notifies a student that they have been re-invited to a live class."""
        title = f"Re-invited to Class: {live_class.title}"
        message = (
            f"Professor {live_class.teacher.get_full_name() or live_class.teacher.username} "
            f"has re-invited you to join '{live_class.title}' ({live_class.subject})."
        )
        notif = cls.create_notification(
            recipient=student,
            notification_type=Notification.Type.STUDENT_REINVITED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=2
        )
        try:
            EmailService.send_class_invitation_email(live_class, student)
        except Exception as exc:
            logger.error("Failed to send re-invitation email: %s", exc)
        return notif

    @classmethod
    def notify_student_removed(cls, live_class, student, reason: str = "") -> Notification:
        """Notifies a student that they have been removed/revoked from a class."""
        title = f"Access Revoked: {live_class.title}"
        message = f"Your access to live class '{live_class.title}' ({live_class.subject}) has been revoked by the instructor."
        if reason:
            message += f" Reason: {reason}"
        notif = cls.create_notification(
            recipient=student,
            notification_type=Notification.Type.STUDENT_REMOVED,
            title=title,
            message=message,
            related_live_class=live_class,
            check_duplicate_minutes=2
        )
        try:
            EmailService.send_access_revoked_email(live_class, student)
        except Exception as exc:
            logger.error("Failed to send removal email: %s", exc)
        return notif


class ReminderService:
    """
    Upcoming Class Reminder Service.
    Finds classes scheduled within a specific forward window (e.g. 15-30 minutes)
    and dispatches in-app reminders and reminder emails.
    """

    @classmethod
    def send_upcoming_class_reminders(cls, window_minutes: int = 30) -> int:
        """
        Evaluates upcoming scheduled classes and sends reminders if not already sent.
        Supports configurable windows (e.g., 30 minutes, 10 minutes before start).
        Timezone-aware: handles edge boundaries accurately without naive comparison errors.
        Never sends reminders for CANCELLED or COMPLETED/ENDED classes.
        Returns total number of notifications created.
        Designed to be called periodically via cron or background tasks.
        """
        from classrooms.models import LiveClass, ClassEnrollment

        now = timezone.now()
        local_now = timezone.localtime(now)
        target_cutoff = now + timedelta(minutes=window_minutes)
        local_cutoff = timezone.localtime(target_cutoff)

        # Query SCHEDULED classes on local today +/- 1 day to be fully timezone resilient
        candidate_classes = LiveClass.objects.filter(
            status=LiveClass.Status.SCHEDULED,
            scheduled_date__gte=(local_now - timedelta(days=1)).date(),
            scheduled_date__lte=(local_cutoff + timedelta(days=1)).date()
        ).select_related('teacher')

        total_reminders_sent = 0

        for lc in candidate_classes:
            s_dt = lc.scheduled_datetime
            if not s_dt:
                continue

            # Must be in the future and within the forward window
            if not (now <= s_dt <= target_cutoff):
                continue

            teacher = lc.teacher
            title = f"Starting Soon ({window_minutes}m): {lc.title}"
            message = (
                f"Your class '{lc.title}' ({lc.subject}) begins in approximately {window_minutes} minutes "
                f"at {lc.scheduled_time.strftime('%I:%M %p')}. "
                f"Room Code: {lc.room_code}."
            )

            # 1. Remind Teacher (deduplicated within this window)
            teacher_already_reminded = Notification.objects.filter(
                recipient=teacher,
                related_live_class=lc,
                notification_type=Notification.Type.CLASS_STARTING,
                created_at__gte=now - timedelta(minutes=max(5, window_minutes - 2))
            ).exists()

            if not teacher_already_reminded:
                notif = NotificationService.create_notification(
                    recipient=teacher,
                    notification_type=Notification.Type.CLASS_STARTING,
                    title=title,
                    message=message,
                    related_live_class=lc,
                    check_duplicate_minutes=0
                )
                if notif:
                    total_reminders_sent += 1
                EmailService.send_class_reminder_email(lc, teacher, window_minutes)

            # 2. Remind Enrolled Students and Registered Participants
            seen_user_ids = {teacher.id}

            # Enrolled students
            for enrollment in lc.enrollments.filter(status=ClassEnrollment.Status.ENROLLED).select_related('student'):
                student = enrollment.student
                if student.id not in seen_user_ids:
                    seen_user_ids.add(student.id)
                    already_reminded = Notification.objects.filter(
                        recipient=student,
                        related_live_class=lc,
                        notification_type=Notification.Type.CLASS_STARTING,
                        created_at__gte=now - timedelta(minutes=max(5, window_minutes - 2))
                    ).exists()
                    if not already_reminded:
                        n = NotificationService.create_notification(
                            recipient=student,
                            notification_type=Notification.Type.CLASS_STARTING,
                            title=title,
                            message=message,
                            related_live_class=lc,
                            check_duplicate_minutes=0
                        )
                        if n:
                            total_reminders_sent += 1
                        EmailService.send_class_reminder_email(lc, student, window_minutes)

            # Prior participants
            for part in lc.participants.filter(user__isnull=False).select_related('user'):
                user = part.user
                if user.id not in seen_user_ids:
                    seen_user_ids.add(user.id)
                    already_reminded = Notification.objects.filter(
                        recipient=user,
                        related_live_class=lc,
                        notification_type=Notification.Type.CLASS_STARTING,
                        created_at__gte=now - timedelta(minutes=max(5, window_minutes - 2))
                    ).exists()
                    if not already_reminded:
                        n = NotificationService.create_notification(
                            recipient=user,
                            notification_type=Notification.Type.CLASS_STARTING,
                            title=title,
                            message=message,
                            related_live_class=lc,
                            check_duplicate_minutes=0
                        )
                        if n:
                            total_reminders_sent += 1
                        EmailService.send_class_reminder_email(lc, user, window_minutes)

        logger.info(
            "ReminderService: Processed upcoming window=%dm, sent %d reminders",
            window_minutes,
            total_reminders_sent
        )
        return total_reminders_sent
