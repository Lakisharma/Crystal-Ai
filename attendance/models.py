from django.conf import settings
from django.db import models
from django.utils import timezone
from classrooms.models import Classroom


class AttendanceSession(models.Model):
    """
    An individual class session or lecture where attendance is logged.
    """
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.CASCADE,
        related_name='attendance_sessions'
    )
    date = models.DateField(default=timezone.now)
    topic = models.CharField(max_length=200, blank=True, help_text="Session topic or lecture title")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_attendance_sessions'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']
        verbose_name = 'Attendance Session'
        verbose_name_plural = 'Attendance Sessions'

    def __str__(self):
        return f"{self.classroom.code} - {self.date} ({self.topic or 'Lecture'})"

    @property
    def total_present(self) -> int:
        return self.records.filter(status=AttendanceRecord.Status.PRESENT).count()

    @property
    def total_records(self) -> int:
        return self.records.count()


class AttendanceRecord(models.Model):
    """
    The attendance status of a single student in a specific session.
    """
    class Status(models.TextChoices):
        PRESENT = 'PRESENT', 'Present'
        ABSENT = 'ABSENT', 'Absent'
        LATE = 'LATE', 'Late'
        EXCUSED = 'EXCUSED', 'Excused'

    session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.CASCADE,
        related_name='records'
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='attendance_records'
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PRESENT
    )
    remarks = models.CharField(max_length=255, blank=True)
    marked_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['student__username']
        unique_together = ('session', 'student')
        verbose_name = 'Attendance Record'
        verbose_name_plural = 'Attendance Records'

    def __str__(self):
        return f"{self.student.username}: {self.get_status_display()} ({self.session})"
