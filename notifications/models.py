from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    """
    In-app notification for TeachLive users (Teachers, Students, Administrators).
    Tracks real-time system, classroom lifecycle, and enrollment updates.
    """
    class Type(models.TextChoices):
        CLASS_CREATED = 'CLASS_CREATED', 'Class Created'
        CLASS_SCHEDULED = 'CLASS_SCHEDULED', 'Class Scheduled'
        CLASS_STARTING = 'CLASS_STARTING', 'Class Starting Soon'
        CLASS_STARTED = 'CLASS_STARTED', 'Class Started'
        CLASS_ENDED = 'CLASS_ENDED', 'Class Ended'
        CLASS_CANCELLED = 'CLASS_CANCELLED', 'Class Cancelled'
        STUDENT_JOINED = 'STUDENT_JOINED', 'Student Joined'
        TEACHER_CLASS_UPDATE = 'TEACHER_CLASS_UPDATE', 'Class Updated'
        STUDENT_INVITED = 'STUDENT_INVITED', 'Student Invited'
        ENROLLMENT_APPROVED = 'ENROLLMENT_APPROVED', 'Enrollment Approved'
        ENROLLMENT_REJECTED = 'ENROLLMENT_REJECTED', 'Enrollment Rejected'
        ENROLLMENT_REVOKED = 'ENROLLMENT_REVOKED', 'Enrollment Revoked'
        ACCESS_REQUESTED = 'ACCESS_REQUESTED', 'Access Requested'
        INVITATION_ACCEPTED = 'INVITATION_ACCEPTED', 'Invitation Accepted'
        INVITATION_DECLINED = 'INVITATION_DECLINED', 'Invitation Declined'
        STUDENT_REINVITED = 'STUDENT_REINVITED', 'Student Re-invited'
        STUDENT_REMOVED = 'STUDENT_REMOVED', 'Student Removed'
        CLASS_ACCESS_CHANGED = 'CLASS_ACCESS_CHANGED', 'Class Access Changed'
        SYSTEM = 'SYSTEM', 'System Notification'

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
        help_text="User receiving the notification."
    )
    notification_type = models.CharField(
        max_length=50,
        choices=Type.choices,
        default=Type.SYSTEM,
        help_text="Category of notification."
    )
    title = models.CharField(
        max_length=255,
        help_text="Concise summary header."
    )
    message = models.TextField(
        help_text="Detailed notification body content."
    )
    related_live_class = models.ForeignKey(
        'classrooms.LiveClass',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
        help_text="Associated live class session if applicable."
    )
    is_read = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Read/unread receipt flag."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when notification was issued."
    )
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when recipient marked as read."
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Notification'
        verbose_name_plural = 'Notifications'
        indexes = [
            models.Index(fields=['recipient', 'is_read']),
            models.Index(fields=['recipient', 'created_at']),
            models.Index(fields=['notification_type', 'created_at']),
        ]

    def __str__(self):
        return f"[{self.get_notification_type_display()}] {self.title} -> {self.recipient.username}"

    def mark_as_read(self):
        """Marks this notification as read with current timestamp."""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])

    @property
    def icon_class(self) -> str:
        """Returns Bootstrap icon class corresponding to notification type."""
        mapping = {
            self.Type.CLASS_CREATED: 'bi-calendar-plus text-primary',
            self.Type.CLASS_SCHEDULED: 'bi-calendar-event text-info',
            self.Type.CLASS_STARTING: 'bi-alarm text-warning',
            self.Type.CLASS_STARTED: 'bi-broadcast text-danger',
            self.Type.CLASS_ENDED: 'bi-check2-circle text-success',
            self.Type.CLASS_CANCELLED: 'bi-x-circle text-danger',
            self.Type.STUDENT_JOINED: 'bi-person-check text-success',
            self.Type.TEACHER_CLASS_UPDATE: 'bi-pencil-square text-primary',
            self.Type.STUDENT_INVITED: 'bi-envelope-paper text-primary',
            self.Type.ENROLLMENT_APPROVED: 'bi-patch-check text-success',
            self.Type.ENROLLMENT_REJECTED: 'bi-patch-minus text-warning',
            self.Type.ENROLLMENT_REVOKED: 'bi-shield-slash text-danger',
            self.Type.ACCESS_REQUESTED: 'bi-key text-info',
            self.Type.INVITATION_ACCEPTED: 'bi-person-check-fill text-success',
            self.Type.INVITATION_DECLINED: 'bi-person-x-fill text-warning',
            self.Type.STUDENT_REINVITED: 'bi-envelope-check text-primary',
            self.Type.STUDENT_REMOVED: 'bi-person-dash text-danger',
            self.Type.CLASS_ACCESS_CHANGED: 'bi-gear-wide-connected text-info',
            self.Type.SYSTEM: 'bi-bell text-secondary',
        }
        return mapping.get(self.notification_type, 'bi-bell text-primary')


class EmailLog(models.Model):
    """
    Safe audit and monitoring log for all transactional emails dispatched by TeachLive.
    Never stores SMTP passwords, OAuth secrets, or user credentials.
    """
    class Status(models.TextChoices):
        SENT = 'SENT', 'Sent'
        FAILED = 'FAILED', 'Failed'

    recipient = models.EmailField(
        help_text="Target recipient email address."
    )
    email_type = models.CharField(
        max_length=60,
        help_text="Email category (e.g. WELCOME_TEACHER, CLASS_CREATED, REMINDER)."
    )
    subject = models.CharField(
        max_length=255,
        help_text="Rendered email subject header."
    )
    related_live_class = models.ForeignKey(
        'classrooms.LiveClass',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='email_logs',
        help_text="Associated live class session if applicable."
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SENT,
        db_index=True,
        help_text="Delivery dispatch status."
    )
    error_message = models.TextField(
        blank=True,
        help_text="Sanitized error summary if dispatch failed."
    )
    sent_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when email attempt occurred."
    )

    class Meta:
        ordering = ['-sent_at']
        verbose_name = 'Email Log'
        verbose_name_plural = 'Email Logs'
        indexes = [
            models.Index(fields=['status', 'sent_at']),
            models.Index(fields=['email_type', 'sent_at']),
        ]

    def __str__(self):
        return f"[{self.status}] {self.email_type} to {self.recipient} at {self.sent_at}"
