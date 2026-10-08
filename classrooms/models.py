import random
import secrets
import string
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


def generate_classroom_code():
    """Generates a random 6-character alphanumeric classroom code."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=6))


def generate_secure_room_code():
    """
    Generates a cryptographically secure, human-friendly random room code.
    Format: XXX-YYY-ZZZ (e.g. ABC-472-X9M)
    """
    alphabet = string.ascii_uppercase + string.digits
    part1 = ''.join(secrets.choice(alphabet) for _ in range(3))
    part2 = ''.join(secrets.choice(alphabet) for _ in range(3))
    part3 = ''.join(secrets.choice(alphabet) for _ in range(3))
    return f"{part1}-{part2}-{part3}"


# =====================================================================
# Virtual Classroom (Course Level)
# =====================================================================

class Classroom(models.Model):
    """
    Core virtual classroom connecting teachers and enrolled students.
    """
    name = models.CharField(max_length=150, help_text="e.g. Introduction to Computer Science")
    code = models.CharField(
        max_length=10,
        unique=True,
        default=generate_classroom_code,
        blank=True,
        help_text="Unique join code for students (e.g., MATH101 or 6-char code)."
    )
    subject = models.CharField(max_length=100, help_text="e.g. Mathematics, Physics, History")
    description = models.TextField(blank=True, help_text="Course syllabus or classroom summary.")
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='teaching_classes',
        help_text="Teacher who owns and instructs this classroom."
    )
    students = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='enrolled_classes',
        blank=True,
        help_text="Students enrolled in this classroom."
    )
    class Status(models.TextChoices):
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        LIVE = 'LIVE', 'Live'
        COMPLETED = 'COMPLETED', 'Completed'

    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.SCHEDULED,
        help_text="Class status: Scheduled, Live, or Completed."
    )
    scheduled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Scheduled start date and time for the class."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Classroom'
        verbose_name_plural = 'Classrooms'

    def __str__(self):
        return f"{self.name} ({self.code})"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('classrooms:detail', kwargs={'pk': self.pk})

    @property
    def is_live(self) -> bool:
        return self.status == self.Status.LIVE

    @property
    def is_scheduled(self) -> bool:
        return self.status == self.Status.SCHEDULED

    @property
    def is_completed(self) -> bool:
        return self.status == self.Status.COMPLETED

    @property
    def student_count(self) -> int:
        return self.students.count()

    @property
    def participant_count(self) -> int:
        return self.students.count()

    @property
    def title(self) -> str:
        return self.name

    @property
    def room_code(self) -> str:
        return self.code


class ClassSchedule(models.Model):
    """
    Recurring or upcoming class timetable entries.
    """
    class DayOfWeek(models.IntegerChoices):
        MONDAY = 1, 'Monday'
        TUESDAY = 2, 'Tuesday'
        WEDNESDAY = 3, 'Wednesday'
        THURSDAY = 4, 'Thursday'
        FRIDAY = 5, 'Friday'
        SATURDAY = 6, 'Saturday'
        SUNDAY = 7, 'Sunday'

    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.CASCADE,
        related_name='schedules'
    )
    title = models.CharField(max_length=150, help_text="e.g. Lecture, Lab, or Seminar")
    day_of_week = models.IntegerField(choices=DayOfWeek.choices, default=DayOfWeek.MONDAY)
    start_time = models.TimeField()
    end_time = models.TimeField()
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['day_of_week', 'start_time']
        verbose_name = 'Class Schedule'
        verbose_name_plural = 'Class Schedules'

    def __str__(self):
        return f"{self.classroom.code} - {self.get_day_of_week_display()} {self.start_time.strftime('%H:%M')}"


# =====================================================================
# Complete Classroom Database System: LiveClass & Related Models
# =====================================================================

class LiveClass(models.Model):
    """
    A specific live class session hosted by a teacher.
    Supports secure room codes, password hashing, scheduled timestamps,
    and lifecycle management (SCHEDULED -> LIVE -> COMPLETED/CANCELLED).
    """
    class Status(models.TextChoices):
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        LIVE = 'LIVE', 'Live'
        COMPLETED = 'COMPLETED', 'Ended'
        CANCELLED = 'CANCELLED', 'Cancelled'

    # Alias ENDED to COMPLETED for backwards compatibility with DB and tests
    Status.ENDED = Status.COMPLETED

    class AccessMode(models.TextChoices):
        PUBLIC_LINK = 'PUBLIC_LINK', 'Public Link'
        ENROLLMENT_ONLY = 'ENROLLMENT_ONLY', 'Enrollment Only'
        PASSWORD_PROTECTED = 'PASSWORD_PROTECTED', 'Password Protected'

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='live_classes',
        help_text="Instructor hosting this live class session."
    )
    title = models.CharField(max_length=200, help_text="e.g. Advanced Operating Systems Lecture 4")
    subject = models.CharField(max_length=100, help_text="e.g. Computer Science")
    description = models.TextField(blank=True, help_text="Syllabus, topics, or instructions for attendees.")
    room_code = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        default=generate_secure_room_code,
        help_text="Unique secure room code for participants to join."
    )
    room_password_hash = models.CharField(
        max_length=255,
        blank=True,
        help_text="Cryptographically hashed room password (never stored in plain text)."
    )
    scheduled_date = models.DateField(help_text="Date when class will occur.")
    scheduled_time = models.TimeField(help_text="Start time of the class.")
    duration = models.PositiveIntegerField(default=60, help_text="Class duration in minutes.")
    max_students = models.PositiveIntegerField(default=50, help_text="Maximum allowed student capacity.")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
        db_index=True
    )
    access_mode = models.CharField(
        max_length=30,
        choices=AccessMode.choices,
        default=AccessMode.PUBLIC_LINK,
        db_index=True,
        help_text="Class security access model (Public Link, Enrollment Only, Password Protected)."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-scheduled_date', '-scheduled_time', '-created_at']
        verbose_name = 'Live Class'
        verbose_name_plural = 'Live Classes'
        indexes = [
            models.Index(fields=['teacher', 'status']),
            models.Index(fields=['scheduled_date', 'scheduled_time']),
            models.Index(fields=['room_code']),
        ]

    def __str__(self):
        return f"{self.title} ({self.room_code}) - {self.get_status_display()}"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('classrooms:live_detail', kwargs={'pk': self.pk})

    def set_room_password(self, raw_password: str):
        """Hashes and sets the room password using Django's make_password."""
        if raw_password:
            self.room_password_hash = make_password(raw_password)
        else:
            self.room_password_hash = ''

    def check_room_password(self, raw_password: str) -> bool:
        """Verifies if the given password matches the hashed room password."""
        if not self.room_password_hash:
            return True
        return check_password(raw_password, self.room_password_hash)

    @property
    def has_password(self) -> bool:
        return bool(self.room_password_hash)

    @property
    def is_scheduled(self) -> bool:
        return self.status == self.Status.SCHEDULED

    @property
    def is_live(self) -> bool:
        return self.status == self.Status.LIVE

    @property
    def is_completed(self) -> bool:
        return self.status == self.Status.COMPLETED

    @property
    def is_ended(self) -> bool:
        return self.status in (self.Status.COMPLETED, 'COMPLETED', 'ENDED')

    @property
    def is_cancelled(self) -> bool:
        return self.status == self.Status.CANCELLED

    @property
    def scheduled_datetime(self):
        """
        Combines scheduled_date and scheduled_time into a timezone-aware datetime in the active timezone.
        Never returns a naive datetime.
        """
        if not self.scheduled_date or not self.scheduled_time:
            return None
        tz = timezone.get_current_timezone()
        naive_dt = timezone.datetime.combine(self.scheduled_date, self.scheduled_time)
        return timezone.make_aware(naive_dt, tz)

    @property
    def expected_end_datetime(self):
        """
        Returns the expected completion datetime.
        If class was started, started_at + duration.
        Otherwise, scheduled_datetime + duration.
        """
        duration_delta = timezone.timedelta(minutes=self.duration or 60)
        if self.started_at:
            return self.started_at + duration_delta
        s_dt = self.scheduled_datetime
        if s_dt:
            return s_dt + duration_delta
        return None

    @property
    def countdown_seconds(self) -> int:
        """
        Seconds remaining until scheduled start. Negative if past start.
        """
        s_dt = self.scheduled_datetime
        if not s_dt:
            return 0
        diff = (s_dt - timezone.now()).total_seconds()
        return int(diff)

    @property
    def is_within_start_window(self) -> bool:
        """
        True if current time is within CLASS_EARLY_START_MINUTES of scheduled_datetime
        and class is still SCHEDULED.
        """
        if self.status != self.Status.SCHEDULED:
            return False
        s_dt = self.scheduled_datetime
        if not s_dt:
            return False
        early_mins = getattr(settings, 'CLASS_EARLY_START_MINUTES', 15)
        now = timezone.now()
        start_allowed_at = s_dt - timezone.timedelta(minutes=early_mins)
        return now >= start_allowed_at

    @property
    def is_within_join_window(self) -> bool:
        """
        True if class is LIVE or within CLASS_EARLY_JOIN_MINUTES of scheduled start.
        """
        if self.status == self.Status.LIVE:
            return True
        if self.status != self.Status.SCHEDULED:
            return False
        s_dt = self.scheduled_datetime
        if not s_dt:
            return False
        early_join_mins = getattr(settings, 'CLASS_EARLY_JOIN_MINUTES', 10)
        now = timezone.now()
        join_allowed_at = s_dt - timezone.timedelta(minutes=early_join_mins)
        return now >= join_allowed_at

    @property
    def is_public_link(self) -> bool:
        return self.access_mode == self.AccessMode.PUBLIC_LINK

    @property
    def is_enrollment_only(self) -> bool:
        return self.access_mode == self.AccessMode.ENROLLMENT_ONLY

    @property
    def is_password_protected(self) -> bool:
        return self.access_mode == self.AccessMode.PASSWORD_PROTECTED

    @property
    def participant_count(self) -> int:
        return self.participants.count()

    def start_class(self):
        """Starts the live class session."""
        self.status = self.Status.LIVE
        if not self.started_at:
            self.started_at = timezone.now()
        self.save()

    def end_class(self):
        """Concludes the live class session."""
        self.status = self.Status.COMPLETED
        self.ended_at = timezone.now()
        self.save()

    def cancel_class(self):
        """Cancels the class session."""
        self.status = self.Status.CANCELLED
        self.save()

    @property
    def enrolled_count(self) -> int:
        return self.enrollments.filter(status='ENROLLED').count()

    @property
    def available_seats(self) -> int:
        return max(0, self.max_students - self.enrolled_count)

    def get_or_create_invite_token(self, user=None):
        """Returns the current active invite token, or creates one if none exists."""
        active = self.invite_tokens.filter(is_revoked=False).order_by('-created_at').first()
        if active and active.is_active:
            return active
        exp = None
        if self.expected_end_datetime:
            exp = self.expected_end_datetime + timezone.timedelta(days=7)
        else:
            exp = timezone.now() + timezone.timedelta(days=7)
        return ClassInviteToken.objects.create(
            live_class=self,
            created_by=user or self.teacher,
            expires_at=exp
        )


class ClassParticipant(models.Model):
    """
    Tracks students joining and leaving a specific live class session.
    """
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        JOINED = 'JOINED', 'Joined'
        LEFT = 'LEFT', 'Left'
        DISCONNECTED = 'DISCONNECTED', 'Disconnected'
        KICKED = 'KICKED', 'Kicked'

    live_class = models.ForeignKey(
        LiveClass,
        on_delete=models.CASCADE,
        related_name='participants'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='live_class_participations',
        help_text="Registered user account if participant is logged in."
    )
    student_name = models.CharField(max_length=150)
    student_email = models.EmailField()
    join_time = models.DateTimeField(default=timezone.now)
    leave_time = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(default=timezone.now, null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE
    )
    is_hand_raised = models.BooleanField(default=False, db_index=True)
    hand_raised_at = models.DateTimeField(null=True, blank=True)
    is_muted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        ordering = ['-join_time']
        verbose_name = 'Class Participant'
        verbose_name_plural = 'Class Participants'
        indexes = [
            models.Index(fields=['live_class', 'student_email']),
            models.Index(fields=['live_class', 'status']),
            models.Index(fields=['live_class', 'user']),
            models.Index(fields=['live_class', 'last_seen_at']),
            models.Index(fields=['live_class', 'is_hand_raised']),
        ]

    def __str__(self):
        return f"{self.student_name} in {self.live_class.room_code} ({self.get_status_display()})"


class Attendance(models.Model):
    """
    Logged attendance metrics for each participant in a live class session.
    """
    class Status(models.TextChoices):
        PRESENT = 'PRESENT', 'Present'
        ABSENT = 'ABSENT', 'Absent'
        LEFT = 'LEFT', 'Left'
        DISCONNECTED = 'DISCONNECTED', 'Disconnected'

    live_class = models.ForeignKey(
        LiveClass,
        on_delete=models.CASCADE,
        related_name='attendances'
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='live_attendances',
        help_text="Registered student account if available."
    )
    student_name = models.CharField(max_length=150)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PRESENT,
        help_text="Attendance status e.g. PRESENT, ABSENT, LEFT, DISCONNECTED"
    )
    joined_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True)
    total_duration = models.PositiveIntegerField(
        default=0,
        help_text="Total attendance duration in minutes"
    )
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        ordering = ['student_name']
        verbose_name = 'Live Class Attendance'
        verbose_name_plural = 'Live Class Attendances'
        indexes = [
            models.Index(fields=['live_class', 'student_name']),
            models.Index(fields=['live_class', 'student']),
            models.Index(fields=['live_class', 'status']),
            models.Index(fields=['student', 'status']),
        ]

    def __str__(self):
        return f"{self.student_name} - {self.live_class.title} ({self.total_duration} mins)"

    @property
    def attendance_percentage(self) -> float:
        from classrooms.attendance_services import calculate_attendance_percentage
        scheduled = self.live_class.duration if self.live_class else 0
        return calculate_attendance_percentage(self.total_duration, scheduled)

    @property
    def duration_display(self) -> str:
        if self.total_duration >= 60:
            hrs = self.total_duration // 60
            mins = self.total_duration % 60
            return f"{hrs}h {mins}m" if mins else f"{hrs}h"
        return f"{self.total_duration}m"

    @property
    def is_active_now(self) -> bool:
        return self.left_at is None and self.status == self.Status.PRESENT


class ChatMessage(models.Model):
    """
    Real-time chat messages inside a live class session.
    """
    class SenderRole(models.TextChoices):
        TEACHER = 'TEACHER', 'Teacher'
        STUDENT = 'STUDENT', 'Student'
        ADMIN = 'ADMIN', 'Admin'

    live_class = models.ForeignKey(
        LiveClass,
        on_delete=models.CASCADE,
        related_name='chat_messages'
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='live_chat_messages',
        help_text="Authenticated user who sent this message."
    )
    sender_name = models.CharField(max_length=150)
    sender_role = models.CharField(
        max_length=20,
        choices=SenderRole.choices,
        default=SenderRole.STUDENT
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Class Chat Message'
        verbose_name_plural = 'Class Chat Messages'
        indexes = [
            models.Index(fields=['live_class', 'created_at']),
            models.Index(fields=['live_class', 'sender']),
        ]


    def __str__(self):
        return f"[{self.live_class.room_code}] {self.sender_name} ({self.sender_role}): {self.message[:30]}"


# =====================================================================
# Secure Class Enrollment & Access Control Models
# =====================================================================

class ClassEnrollment(models.Model):
    """
    Tracks formal enrollment and invitation records for LiveClasses.
    Enforces student isolation and prevents duplicate enrollment for the same student and class.
    """
    class Status(models.TextChoices):
        INVITED = 'INVITED', 'Invited'
        ENROLLED = 'ENROLLED', 'Enrolled'
        REVOKED = 'REVOKED', 'Revoked'
        COMPLETED = 'COMPLETED', 'Completed'

    live_class = models.ForeignKey(
        LiveClass,
        on_delete=models.CASCADE,
        related_name='enrollments',
        help_text="Live class the student is enrolled in or invited to."
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='class_enrollments',
        help_text="Enrolled or invited student user account."
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ENROLLED,
        db_index=True
    )
    invited_at = models.DateTimeField(null=True, blank=True)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=255, blank=True, help_text="Optional reason for revocation/removal.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Class Enrollment'
        verbose_name_plural = 'Class Enrollments'
        constraints = [
            models.UniqueConstraint(
                fields=['live_class', 'student'],
                name='unique_liveclass_student_enrollment'
            )
        ]
        indexes = [
            models.Index(fields=['live_class', 'status']),
            models.Index(fields=['student', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f"{self.student.username} -> {self.live_class.room_code} ({self.get_status_display()})"

    @property
    def is_active(self) -> bool:
        return self.status in (self.Status.ENROLLED, self.Status.INVITED)

    def save(self, *args, **kwargs):
        now = timezone.now()
        if self.status == self.Status.ENROLLED and not self.enrolled_at:
            self.enrolled_at = now
        elif self.status == self.Status.INVITED and not self.invited_at:
            self.invited_at = now
        elif self.status == self.Status.REVOKED and not self.revoked_at:
            self.revoked_at = now
        super().save(*args, **kwargs)


class ClassAccessRequest(models.Model):
    """
    Student access request for enrollment-only classes.
    Enforces one pending request per student per live class.
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
        CANCELLED = 'CANCELLED', 'Cancelled'

    live_class = models.ForeignKey(
        LiveClass,
        on_delete=models.CASCADE,
        related_name='access_requests',
        help_text="Live class requested."
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='class_access_requests',
        help_text="Student requesting access."
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True
    )
    request_note = models.TextField(blank=True, help_text="Optional note from student requesting access.")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_access_requests'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Class Access Request'
        verbose_name_plural = 'Class Access Requests'
        indexes = [
            models.Index(fields=['live_class', 'status']),
            models.Index(fields=['student', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f"Request by {self.student.username} for {self.live_class.room_code} ({self.get_status_display()})"


class LiveClassParticipantModeration(models.Model):
    """
    Session-level moderation actions and restriction tracking.
    Actions: MUTED, REMOVED, HAND_RAISED, HAND_LOWERED.
    When action='REMOVED' and active=True, the student is blocked from entering/token/heartbeat
    for the active session without revoking their enrollment.
    """
    class Action(models.TextChoices):
        MUTED = 'MUTED', 'Muted'
        REMOVED = 'REMOVED', 'Removed'
        HAND_RAISED = 'HAND_RAISED', 'Hand Raised'
        HAND_LOWERED = 'HAND_LOWERED', 'Hand Lowered'

    live_class = models.ForeignKey(
        LiveClass,
        on_delete=models.CASCADE,
        related_name='moderation_events',
        help_text="Live class session."
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='moderation_records',
        help_text="Target student."
    )
    action = models.CharField(
        max_length=20,
        choices=Action.choices,
        db_index=True
    )
    reason = models.CharField(max_length=255, blank=True, help_text="Optional moderation reason.")
    active = models.BooleanField(default=True, db_index=True, help_text="True if moderation restriction is currently active.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='moderation_actions_taken',
        help_text="User who initiated moderation."
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Live Class Moderation Event'
        verbose_name_plural = 'Live Class Moderation Events'
        indexes = [
            models.Index(fields=['live_class', 'active']),
            models.Index(fields=['live_class', 'student', 'action', 'active']),
            models.Index(fields=['live_class', 'created_at']),
        ]

    def __str__(self):
        return f"{self.get_action_display()} - {self.student.username} in {self.live_class.room_code}"


def generate_invite_token():
    return secrets.token_urlsafe(32)


class ClassInviteToken(models.Model):
    """
    Cryptographically secure shareable invitation tokens for LiveClass sessions.
    Allows teachers to share a secure token link with prospective students.
    """
    live_class = models.ForeignKey(
        LiveClass,
        on_delete=models.CASCADE,
        related_name='invite_tokens',
        help_text="Live class associated with this secure invite token."
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        default=generate_invite_token,
        help_text="Cryptographically secure URL-safe random token."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_invite_tokens'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_revoked = models.BooleanField(default=False, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(default=0, help_text="0 for unlimited uses until capacity/expiration.")
    uses_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Class Invite Token'
        verbose_name_plural = 'Class Invite Tokens'
        indexes = [
            models.Index(fields=['token', 'is_revoked']),
            models.Index(fields=['live_class', 'is_revoked']),
        ]

    def __str__(self):
        return f"InviteToken({self.live_class.room_code}, active={self.is_active})"

    @property
    def times_used(self) -> int:
        return self.uses_count

    @times_used.setter
    def times_used(self, value: int):
        self.uses_count = value

    @property
    def is_valid(self) -> bool:
        return self.is_active

    @classmethod
    def create_for_class(cls, live_class, created_by=None, max_uses=0, expires_at=None):
        if created_by is None:
            created_by = live_class.teacher
        return cls.objects.create(
            live_class=live_class,
            created_by=created_by,
            max_uses=max_uses,
            expires_at=expires_at
        )

    @property
    def is_active(self) -> bool:
        if self.is_revoked:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        if self.max_uses > 0 and self.uses_count >= self.max_uses:
            return False
        if self.live_class.is_cancelled or self.live_class.is_ended:
            return False
        return True

    def revoke(self):
        self.is_revoked = True
        self.revoked_at = timezone.now()
        self.save(update_fields=['is_revoked', 'revoked_at'])
