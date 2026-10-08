from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user model supporting distinct roles:
    ADMIN, TEACHER, and STUDENT.
    """
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Administrator'
        TEACHER = 'TEACHER', 'Teacher'
        STUDENT = 'STUDENT', 'Student'

    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.STUDENT,
        help_text="Designates the role for permissions, dashboards, and features."
    )
    bio = models.TextField(
        blank=True,
        help_text="Short bio or subject specialization."
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="Contact telephone number."
    )
    profile_picture = models.ImageField(
        upload_to='avatars/',
        blank=True,
        null=True,
        help_text="Optional profile photo."
    )
    google_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="Google OAuth unique subject ID (sub)."
    )
    google_picture_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Google account profile photo URL."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['username']
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    @property
    def avatar_url(self) -> str:
        """Returns the profile picture URL, Google avatar URL, or empty string."""
        if self.profile_picture:
            return self.profile_picture.url
        if self.google_picture_url:
            return self.google_picture_url
        return ""

    @property
    def created_date(self):
        return self.date_joined

    @property
    def updated_date(self):
        return self.updated_at

    def __str__(self):
        full_name = self.get_full_name()
        if full_name:
            return f"{full_name} ({self.username}) [{self.get_role_display()}]"
        return f"{self.username} [{self.get_role_display()}]"

    @property
    def is_admin_role(self) -> bool:
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def is_teacher(self) -> bool:
        return self.role == self.Role.TEACHER

    @property
    def is_student(self) -> bool:
        return self.role == self.Role.STUDENT

    def save(self, *args, **kwargs):
        # Ensure superusers automatically get ADMIN role
        if self.is_superuser and self.role != self.Role.ADMIN:
            self.role = self.Role.ADMIN
        super().save(*args, **kwargs)


class AdminAuditLog(models.Model):
    """
    Tracks administrative security and management events in TeachLive.
    """
    class Action(models.TextChoices):
        TEACHER_ACTIVATED = 'TEACHER_ACTIVATED', 'Teacher Activated'
        TEACHER_DEACTIVATED = 'TEACHER_DEACTIVATED', 'Teacher Deactivated'
        STUDENT_ACTIVATED = 'STUDENT_ACTIVATED', 'Student Activated'
        STUDENT_DEACTIVATED = 'STUDENT_DEACTIVATED', 'Student Deactivated'
        CLASS_CREATED = 'CLASS_CREATED', 'Class Created'
        CLASS_CANCELLED = 'CLASS_CANCELLED', 'Class Cancelled'
        CLASS_RESCHEDULED = 'CLASS_RESCHEDULED', 'Class Rescheduled'
        CLASS_STARTED = 'CLASS_STARTED', 'Class Started'
        CLASS_ENDED = 'CLASS_ENDED', 'Class Ended'
        ENROLLMENT_CREATED = 'ENROLLMENT_CREATED', 'Enrollment Created'
        ENROLLMENT_REVOKED = 'ENROLLMENT_REVOKED', 'Enrollment Revoked'
        ACCESS_REQUEST_APPROVED = 'ACCESS_REQUEST_APPROVED', 'Access Request Approved'
        ACCESS_REQUEST_REJECTED = 'ACCESS_REQUEST_REJECTED', 'Access Request Rejected'
        CLASS_ACCESS_MODE_CHANGED = 'CLASS_ACCESS_MODE_CHANGED', 'Class Access Mode Changed'
        STUDENT_INVITED = 'STUDENT_INVITED', 'Student Invited'
        INVITATION_ACCEPTED = 'INVITATION_ACCEPTED', 'Invitation Accepted'
        INVITATION_DECLINED = 'INVITATION_DECLINED', 'Invitation Declined'
        STUDENT_REMOVED = 'STUDENT_REMOVED', 'Student Removed'
        STUDENT_REINVITED = 'STUDENT_REINVITED', 'Student Re-invited'
        INVITE_LINK_GENERATED = 'INVITE_LINK_GENERATED', 'Invite Link Generated'
        INVITE_LINK_REVOKED = 'INVITE_LINK_REVOKED', 'Invite Link Revoked'
        OTHER = 'OTHER', 'Other Action'

    admin = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='admin_audit_logs',
        help_text="Administrator who performed this action."
    )
    action = models.CharField(
        max_length=50,
        choices=Action.choices,
        default=Action.OTHER,
        db_index=True
    )
    target_type = models.CharField(max_length=50, db_index=True)
    target_id = models.CharField(max_length=50, blank=True)
    description = models.TextField()
    ip_address = models.CharField(max_length=45, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Admin Audit Log'
        verbose_name_plural = 'Admin Audit Logs'
        indexes = [
            models.Index(fields=['action', 'created_at']),
            models.Index(fields=['target_type', 'target_id']),
        ]

    def __str__(self):
        admin_name = self.admin.username if self.admin else 'System'
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M')}] {admin_name} - {self.get_action_display()}: {self.description[:40]}"

    @classmethod
    def log_action(cls, user=None, admin=None, action=None, target_model=None, target_type=None, target_id=None, details='', description='', request=None):
        actor = user or admin
        t_type = target_model or target_type or ''
        desc = details or description or ''
        return log_admin_action(admin=actor, action=action, target_type=t_type, target_id=target_id, description=desc, request=request)


def log_admin_action(admin, action, target_type, target_id, description, request=None):
    """Utility to record an admin audit trail."""
    ip = None
    if request:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
    return AdminAuditLog.objects.create(
        admin=admin if (admin and admin.is_authenticated) else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        description=description,
        ip_address=ip
    )
