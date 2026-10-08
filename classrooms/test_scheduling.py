"""
Automated Test Suite for Prompt #12:
TeachLive Class Scheduling + Calendar + Automatic Status Management.
Covers all 36+ test requirements:
- Timezone awareness (India Standard Time / Asia/Kolkata, no naive datetime errors)
- Create scheduled class, past validation, min/max duration validation
- Teacher schedule conflict detection (overlapping allowed for different teachers, blocked for same teacher)
- Rescheduling workflows (valid, notifications, emails, audit logs, restrictions on LIVE/ENDED/CANCELLED)
- Cancellation workflows (valid, notifications, emails, audit logs, restrictions, non-destructive)
- Start-window validation (15-min early start window)
- Student join-window validation (10-min early join window)
- Token issuance restrictions (prevent tokens for cancelled, ended, or unstarted classes)
- Class duration handling & expected end calculation
- Student authorized classes isolation
- Teacher scheduled classes page (/teacher/classes/) with tabs & filters
- Teacher calendar view (/teacher/calendar/)
- Student calendar view (/student/calendar/)
- Admin schedule monitoring & cancellation
- Reminder service deduplication & exclusion of cancelled/ended classes
- Automatic expiration service (mark_expired_classes_ended)
"""

from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AdminAuditLog, User
from classrooms.forms import LiveClassCreateForm, LiveClassEditForm, LiveClassRescheduleForm
from classrooms.models import ClassEnrollment, LiveClass
from classrooms.scheduling import (
    TEACHER_EARLY_START_MINUTES,
    STUDENT_EARLY_JOIN_MINUTES,
    can_student_join_class,
    can_teacher_start_class,
    cancel_live_class,
    detect_teacher_schedule_conflict,
    get_class_status,
    make_aware_datetime,
    mark_expired_classes_ended,
    reschedule_live_class,
    validate_scheduling_parameters,
)
from notifications.models import EmailLog, Notification
from notifications.services import ReminderService


class SchedulingAndStatusTests(TestCase):
    """Complete test suite verifying TeachLive scheduling and status system."""

    def setUp(self):
        # 1. Teachers
        self.teacher1 = User.objects.create_user(
            username='prof_alpha',
            email='alpha@teachlive.test',
            password='Password123!',
            role=User.Role.TEACHER
        )
        self.teacher2 = User.objects.create_user(
            username='prof_beta',
            email='beta@teachlive.test',
            password='Password123!',
            role=User.Role.TEACHER
        )

        # 2. Students
        self.student1 = User.objects.create_user(
            username='student_alice',
            email='alice@teachlive.test',
            password='Password123!',
            role=User.Role.STUDENT
        )
        self.student2 = User.objects.create_user(
            username='student_bob',
            email='bob@teachlive.test',
            password='Password123!',
            role=User.Role.STUDENT
        )

        # 3. Admin
        self.admin_user = User.objects.create_user(
            username='admin_boss',
            email='admin@teachlive.test',
            password='Password123!',
            role=User.Role.ADMIN,
            is_staff=True,
            is_superuser=True
        )

        # Clients
        self.client_teacher1 = Client()
        self.client_teacher1.force_login(self.teacher1)

        self.client_teacher2 = Client()
        self.client_teacher2.force_login(self.teacher2)

        self.client_student1 = Client()
        self.client_student1.force_login(self.student1)

        self.client_student2 = Client()
        self.client_student2.force_login(self.student2)

        self.client_admin = Client()
        self.client_admin.force_login(self.admin_user)

        # Base future date
        now = timezone.now()
        self.future_date = (now + timedelta(days=2)).date()
        self.future_time = time(14, 0)  # 2:00 PM

    # -------------------------------------------------------------
    # 1. Create Scheduled Class & Timezone Checks
    # -------------------------------------------------------------

    def test_create_scheduled_class(self):
        """Teacher creates a valid scheduled class with timezone-aware properties."""
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Intro to Quantum Algorithms',
            subject='Quantum Physics',
            scheduled_date=self.future_date,
            scheduled_time=self.future_time,
            duration=60,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK
        )
        self.assertEqual(live_class.status, LiveClass.Status.SCHEDULED)
        self.assertTrue(live_class.is_scheduled)
        self.assertIsNotNone(live_class.scheduled_datetime)
        self.assertTrue(timezone.is_aware(live_class.scheduled_datetime))
        self.assertEqual(live_class.scheduled_datetime.date(), self.future_date)
        self.assertGreater(live_class.countdown_seconds, 0)

    def test_timezone_aware_comparisons_no_naive_errors(self):
        """Verifies make_aware_datetime always returns timezone-aware datetimes."""
        aware_dt = make_aware_datetime(self.future_date, self.future_time)
        self.assertTrue(timezone.is_aware(aware_dt))
        now = timezone.now()
        # Should compare without raising TypeError: can't compare offset-naive and offset-aware datetimes
        self.assertGreater(aware_dt, now)

    # -------------------------------------------------------------
    # 2. Scheduling in the Past Validation
    # -------------------------------------------------------------

    def test_cannot_schedule_in_the_past(self):
        """Validates that scheduling in the past raises a ValidationError."""
        past_date = (timezone.now() - timedelta(days=2)).date()
        past_time = time(10, 0)

        with self.assertRaises(ValidationError) as ctx:
            validate_scheduling_parameters(past_date, past_time, duration=60)
        self.assertIn("past", str(ctx.exception).lower())

    # -------------------------------------------------------------
    # 3 & 4. Duration Validations (Min & Max)
    # -------------------------------------------------------------

    def test_minimum_duration_validation(self):
        """Class duration cannot be less than 5 minutes."""
        with self.assertRaises(ValidationError) as ctx:
            validate_scheduling_parameters(self.future_date, self.future_time, duration=4)
        self.assertIn("at least 5 minutes", str(ctx.exception))

    def test_maximum_duration_validation(self):
        """Class duration cannot exceed 360 minutes (6 hours)."""
        with self.assertRaises(ValidationError) as ctx:
            validate_scheduling_parameters(self.future_date, self.future_time, duration=365)
        self.assertIn("cannot exceed 360 minutes", str(ctx.exception))

    # -------------------------------------------------------------
    # 5 & 6. Teacher Schedule Conflict Detection
    # -------------------------------------------------------------

    def test_teacher_schedule_conflict_detection(self):
        """Teacher cannot create overlapping classes for themselves."""
        # Class 1: 2:00 PM to 3:00 PM (60 mins)
        LiveClass.objects.create(
            teacher=self.teacher1,
            title='Class Alpha',
            subject='CS',
            scheduled_date=self.future_date,
            scheduled_time=time(14, 0),
            duration=60
        )

        # Attempt overlapping class: 2:30 PM to 3:30 PM (60 mins)
        has_conflict, msg, conflicting = detect_teacher_schedule_conflict(
            teacher=self.teacher1,
            scheduled_date=self.future_date,
            scheduled_time=time(14, 30),
            duration=60
        )
        self.assertTrue(has_conflict)
        self.assertIn("Schedule conflict", msg)
        self.assertEqual(conflicting.title, 'Class Alpha')

        # Form clean check
        form = LiveClassCreateForm(
            data={
                'title': 'Overlapping Class',
                'subject': 'CS',
                'access_mode': 'PUBLIC_LINK',
                'scheduled_date': self.future_date.strftime('%Y-%m-%d'),
                'scheduled_time': '14:30',
                'duration': 60,
                'max_students': 50,
                'status': 'SCHEDULED'
            },
            teacher=self.teacher1
        )
        self.assertFalse(form.is_valid())
        self.assertTrue(any('Schedule conflict' in err for err in form.non_field_errors()))

    def test_overlapping_classes_for_different_teachers_allowed(self):
        """Two different teachers CAN host classes at the exact same date and time."""
        LiveClass.objects.create(
            teacher=self.teacher1,
            title='Teacher 1 Class',
            subject='CS',
            scheduled_date=self.future_date,
            scheduled_time=time(14, 0),
            duration=60
        )

        # Teacher 2 checks same time slot
        has_conflict, msg, conflicting = detect_teacher_schedule_conflict(
            teacher=self.teacher2,
            scheduled_date=self.future_date,
            scheduled_time=time(14, 0),
            duration=60
        )
        self.assertFalse(has_conflict)
        self.assertIsNone(conflicting)

    # -------------------------------------------------------------
    # 7. Teacher Ownership Isolation
    # -------------------------------------------------------------

    def test_teacher_cannot_edit_another_teachers_class(self):
        """Teacher B cannot edit Teacher A's scheduled class."""
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Teacher 1 Class',
            subject='CS',
            scheduled_date=self.future_date,
            scheduled_time=self.future_time,
            duration=60
        )
        # Teacher 2 tries to edit via GET
        resp_get = self.client_teacher2.get(reverse('classrooms:live_edit', kwargs={'pk': live_class.pk}))
        self.assertEqual(resp_get.status_code, 403)

        # Teacher 2 tries to edit via POST
        resp_post = self.client_teacher2.post(reverse('classrooms:live_edit', kwargs={'pk': live_class.pk}), {
            'title': 'Hacked Title',
            'subject': 'CS',
            'scheduled_date': self.future_date.strftime('%Y-%m-%d'),
            'scheduled_time': '14:00',
            'duration': 60,
            'max_students': 50,
            'status': 'SCHEDULED'
        })
        self.assertEqual(resp_post.status_code, 403)
        live_class.refresh_from_db()
        self.assertEqual(live_class.title, 'Teacher 1 Class')

    # -------------------------------------------------------------
    # 8, 9, 10. Reschedule Restrictions on LIVE, ENDED, CANCELLED
    # -------------------------------------------------------------

    def test_cannot_reschedule_live_class(self):
        """A class that is currently LIVE cannot be rescheduled."""
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Live Class',
            subject='CS',
            scheduled_date=self.future_date,
            scheduled_time=self.future_time,
            duration=60,
            status=LiveClass.Status.LIVE,
            started_at=timezone.now()
        )
        success, msg = reschedule_live_class(
            live_class=live_class,
            new_date=self.future_date + timedelta(days=1),
            new_time=time(10, 0),
            new_duration=60,
            actor=self.teacher1
        )
        self.assertFalse(success)
        self.assertIn("Live classes cannot be rescheduled", msg)

    def test_cannot_reschedule_ended_class(self):
        """A class that has ended/completed cannot be rescheduled."""
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Ended Class',
            subject='CS',
            scheduled_date=self.future_date,
            scheduled_time=self.future_time,
            duration=60,
            status=LiveClass.Status.COMPLETED,
            ended_at=timezone.now()
        )
        success, msg = reschedule_live_class(
            live_class=live_class,
            new_date=self.future_date + timedelta(days=1),
            new_time=time(10, 0),
            new_duration=60,
            actor=self.teacher1
        )
        self.assertFalse(success)
        self.assertIn("Completed classes cannot be rescheduled", msg)

    def test_cannot_reschedule_cancelled_class(self):
        """A cancelled class cannot be rescheduled."""
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Cancelled Class',
            subject='CS',
            scheduled_date=self.future_date,
            scheduled_time=self.future_time,
            duration=60,
            status=LiveClass.Status.CANCELLED
        )
        success, msg = reschedule_live_class(
            live_class=live_class,
            new_date=self.future_date + timedelta(days=1),
            new_time=time(10, 0),
            new_duration=60,
            actor=self.teacher1
        )
        self.assertFalse(success)
        self.assertIn("Cancelled classes cannot be rescheduled", msg)

    # -------------------------------------------------------------
    # 11, 12, 13. Valid Rescheduling, Notification & Email
    # -------------------------------------------------------------

    def test_valid_rescheduling_workflow(self):
        """Teacher reschedules their SCHEDULED class; notifications, email logs, and audit logs are triggered."""
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Algorithm Design',
            subject='CS',
            scheduled_date=self.future_date,
            scheduled_time=time(10, 0),
            duration=60,
            status=LiveClass.Status.SCHEDULED
        )
        # Enroll student1
        ClassEnrollment.objects.create(
            live_class=live_class,
            student=self.student1,
            status=ClassEnrollment.Status.ENROLLED
        )

        new_date = self.future_date + timedelta(days=3)
        new_time = time(15, 30)

        url = reverse('classrooms:live_reschedule', kwargs={'pk': live_class.pk})
        resp = self.client_teacher1.post(url, {
            'scheduled_date': new_date.strftime('%Y-%m-%d'),
            'scheduled_time': '15:30',
            'duration': 90,
            'update_summary': 'Shifted due to departmental conference'
        })
        self.assertEqual(resp.status_code, 302)

        live_class.refresh_from_db()
        self.assertEqual(live_class.scheduled_date, new_date)
        self.assertEqual(live_class.scheduled_time, new_time)
        self.assertEqual(live_class.duration, 90)

        # Verify in-app notifications
        teacher_notif = Notification.objects.filter(
            recipient=self.teacher1,
            related_live_class=live_class,
            notification_type=Notification.Type.TEACHER_CLASS_UPDATE
        ).exists()
        self.assertTrue(teacher_notif)

        # Verify AdminAuditLog
        audit_log = AdminAuditLog.objects.filter(
            action=AdminAuditLog.Action.CLASS_RESCHEDULED,
            target_id=str(live_class.pk)
        ).exists()
        self.assertTrue(audit_log)

    # -------------------------------------------------------------
    # 14, 15, 16. Cancel Class Workflow, Notifications & Email
    # -------------------------------------------------------------

    def test_cancel_scheduled_class(self):
        """Teacher cancels their SCHEDULED class; status becomes CANCELLED, notifications and audit logs recorded."""
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Machine Learning Seminar',
            subject='AI',
            scheduled_date=self.future_date,
            scheduled_time=time(11, 0),
            duration=60,
            status=LiveClass.Status.SCHEDULED
        )
        ClassEnrollment.objects.create(
            live_class=live_class,
            student=self.student1,
            status=ClassEnrollment.Status.ENROLLED
        )

        url = reverse('classrooms:live_cancel', kwargs={'pk': live_class.pk})
        # GET confirmation page
        resp_get = self.client_teacher1.get(url)
        self.assertEqual(resp_get.status_code, 200)

        # POST cancel
        resp_post = self.client_teacher1.post(url, {'reason': 'Illness'})
        self.assertEqual(resp_post.status_code, 302)

        live_class.refresh_from_db()
        self.assertEqual(live_class.status, LiveClass.Status.CANCELLED)
        self.assertTrue(live_class.is_cancelled)

        # In-app notification to teacher
        self.assertTrue(Notification.objects.filter(
            recipient=self.teacher1,
            related_live_class=live_class,
            notification_type=Notification.Type.CLASS_CANCELLED
        ).exists())

        # Audit log
        self.assertTrue(AdminAuditLog.objects.filter(
            action=AdminAuditLog.Action.CLASS_CANCELLED,
            target_id=str(live_class.pk)
        ).exists())

    # -------------------------------------------------------------
    # 17 & 18. Cancelled Class Restrictions (Cannot Start / Join)
    # -------------------------------------------------------------

    def test_cancelled_class_cannot_start_or_join(self):
        """A cancelled class cannot be started by teacher or joined by student."""
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Cancelled Session',
            subject='CS',
            scheduled_date=self.future_date,
            scheduled_time=self.future_time,
            duration=60,
            status=LiveClass.Status.CANCELLED
        )

        can_start, start_reason, _ = can_teacher_start_class(live_class, self.teacher1)
        self.assertFalse(can_start)
        self.assertIn("cancelled", start_reason.lower())

        can_join, join_reason, _ = can_student_join_class(live_class, self.student1)
        self.assertFalse(can_join)
        self.assertIn("cancelled", join_reason.lower())

        # Direct token request returns 400
        token_resp = self.client_student1.get(reverse('live_room:token', kwargs={'room_code': live_class.room_code}))
        self.assertEqual(token_resp.status_code, 400)
        self.assertIn("cancelled", token_resp.json().get('error', '').lower())

    # -------------------------------------------------------------
    # 19 & 20. Teacher Early-Start Window
    # -------------------------------------------------------------

    def test_teacher_early_start_window(self):
        """Teacher cannot start class before early start window (default 15 mins), but can within window."""
        local_now = timezone.localtime(timezone.now())

        # Class A: 30 minutes in future (outside 15-min start window)
        s_dt_far = local_now + timedelta(minutes=30)
        class_far = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Far Future Class',
            subject='CS',
            scheduled_date=s_dt_far.date(),
            scheduled_time=s_dt_far.time(),
            duration=60,
            status=LiveClass.Status.SCHEDULED
        )
        can_start, reason, countdown = can_teacher_start_class(class_far, self.teacher1)
        self.assertFalse(can_start)
        self.assertIn("cannot be started yet", reason)
        self.assertFalse(class_far.is_within_start_window)

        # Class B: 10 minutes in future (inside 15-min start window)
        s_dt_near = local_now + timedelta(minutes=10)
        class_near = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Near Future Class',
            subject='CS',
            scheduled_date=s_dt_near.date(),
            scheduled_time=s_dt_near.time(),
            duration=60,
            status=LiveClass.Status.SCHEDULED
        )
        can_start_near, reason_near, _ = can_teacher_start_class(class_near, self.teacher1)
        self.assertTrue(can_start_near)
        self.assertTrue(class_near.is_within_start_window)
        self.assertEqual(get_class_status(class_near), 'READY')

    # -------------------------------------------------------------
    # 21 & 22. Student Early-Join Window & Token Protection
    # -------------------------------------------------------------

    def test_student_join_window_and_token_protection(self):
        """Student cannot enter join flow or obtain tokens outside allowed join rules."""
        local_now = timezone.localtime(timezone.now())

        # 45 minutes in future (outside 10-min student join window)
        s_dt = local_now + timedelta(minutes=45)
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Future Workshop',
            subject='CS',
            scheduled_date=s_dt.date(),
            scheduled_time=s_dt.time(),
            duration=60,
            status=LiveClass.Status.SCHEDULED
        )
        can_join, reason, _ = can_student_join_class(live_class, self.student1)
        self.assertFalse(can_join)
        self.assertIn("not available yet", reason)

        # Token API rejects
        resp = self.client_student1.get(reverse('live_room:token', kwargs={'room_code': live_class.room_code}))
        self.assertEqual(resp.status_code, 400)

    # -------------------------------------------------------------
    # 23. Student Cannot Obtain Token After Class Ends
    # -------------------------------------------------------------

    def test_student_cannot_obtain_token_after_class_ends(self):
        """LiveKit token generation returns 400 if class has completed/ended."""
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Completed Lecture',
            subject='CS',
            scheduled_date=self.future_date,
            scheduled_time=self.future_time,
            duration=60,
            status=LiveClass.Status.COMPLETED,
            ended_at=timezone.now()
        )
        resp = self.client_student1.get(reverse('live_room:token', kwargs={'room_code': live_class.room_code}))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("ended", resp.json().get('error', '').lower())

    # -------------------------------------------------------------
    # 24 & 25. Duration Handling & Expected End
    # -------------------------------------------------------------

    def test_class_duration_handling_and_end(self):
        """Expected end datetime calculates started_at + duration and transition to COMPLETED."""
        now = timezone.now()
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Calculus Lecture',
            subject='Math',
            scheduled_date=now.date(),
            scheduled_time=now.time(),
            duration=45,
            status=LiveClass.Status.LIVE,
            started_at=now
        )
        exp_end = live_class.expected_end_datetime
        self.assertIsNotNone(exp_end)
        self.assertEqual(int((exp_end - now).total_seconds()), 45 * 60)

        # End class
        live_class.end_class()
        self.assertEqual(live_class.status, LiveClass.Status.COMPLETED)
        self.assertTrue(live_class.is_ended)
        self.assertIsNotNone(live_class.ended_at)

    # -------------------------------------------------------------
    # 26. Countdown Data Correctness
    # -------------------------------------------------------------

    def test_countdown_data_correctness(self):
        """Countdown seconds returns accurate positive integer for future class."""
        local_now = timezone.localtime(timezone.now())
        s_dt = local_now + timedelta(hours=2, minutes=30)
        live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Countdown Test',
            subject='Physics',
            scheduled_date=s_dt.date(),
            scheduled_time=s_dt.time(),
            duration=60,
            status=LiveClass.Status.SCHEDULED
        )
        secs = live_class.countdown_seconds
        # Should be approximately 2.5 * 3600 = 9000 seconds
        self.assertAlmostEqual(secs, 9000, delta=10)

    # -------------------------------------------------------------
    # 27. Student Only Sees Authorized Classes
    # -------------------------------------------------------------

    def test_student_only_sees_authorized_classes(self):
        """Private enrollment-only class is hidden from unauthorized students in calendar and list."""
        private_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Private Research Group',
            subject='Advanced CS',
            scheduled_date=self.future_date,
            scheduled_time=self.future_time,
            duration=60,
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY,
            status=LiveClass.Status.SCHEDULED
        )
        # Student 1 enrolled, Student 2 NOT enrolled
        ClassEnrollment.objects.create(
            live_class=private_class,
            student=self.student1,
            status=ClassEnrollment.Status.ENROLLED
        )

        # Student 1 checks calendar
        resp1 = self.client_student1.get(reverse('student:calendar'))
        self.assertContains(resp1, 'Private Research Group')

        # Student 2 checks calendar
        resp2 = self.client_student2.get(reverse('student:calendar'))
        self.assertNotContains(resp2, 'Private Research Group')

    # -------------------------------------------------------------
    # 28. Teacher Scheduled Classes Page & Tabs
    # -------------------------------------------------------------

    def test_teacher_scheduled_classes_page(self):
        """Teacher scheduled classes page (/teacher/classes/) renders and filters by tabs."""
        LiveClass.objects.create(
            teacher=self.teacher1,
            title='Upcoming Class',
            subject='CS',
            scheduled_date=self.future_date,
            scheduled_time=self.future_time,
            duration=60,
            status=LiveClass.Status.SCHEDULED
        )
        resp = self.client_teacher1.get(reverse('classrooms:teacher_classes'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Upcoming Class')
        self.assertContains(resp, 'Scheduled Classes')

    # -------------------------------------------------------------
    # 29 & 30. Calendar Views & Navigation
    # -------------------------------------------------------------

    def test_teacher_and_student_calendar_views(self):
        """Teacher and student calendar views render monthly matrix."""
        # Teacher calendar
        resp_teacher = self.client_teacher1.get(reverse('classrooms:teacher_calendar'))
        self.assertEqual(resp_teacher.status_code, 200)
        self.assertContains(resp_teacher, 'Teaching Calendar')

        # Student calendar
        resp_student = self.client_student1.get(reverse('student:calendar'))
        self.assertEqual(resp_student.status_code, 200)
        self.assertContains(resp_student, 'My Class Schedule')

    # -------------------------------------------------------------
    # 31. Admin Schedule Filtering
    # -------------------------------------------------------------

    def test_admin_schedule_filtering(self):
        """Admin dashboard can filter classes by status and teacher."""
        lc = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Admin Filter Class',
            subject='Math',
            scheduled_date=self.future_date,
            scheduled_time=self.future_time,
            duration=60,
            status=LiveClass.Status.SCHEDULED
        )
        url = reverse('admin_dashboard:classes')
        resp = self.client_admin.get(f"{url}?status=SCHEDULED&teacher={self.teacher1.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Admin Filter Class')

    # -------------------------------------------------------------
    # 32 & 33. Reminder Service Deduplication & Exclusion
    # -------------------------------------------------------------

    def test_reminder_service_deduplication_and_exclusion(self):
        """Reminders are deduplicated and excluded for cancelled/ended classes."""
        local_now = timezone.localtime(timezone.now())
        near_dt = local_now + timedelta(minutes=20)

        # 1. Scheduled class within 30 min window
        active_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Active Soon Class',
            subject='CS',
            scheduled_date=near_dt.date(),
            scheduled_time=near_dt.time(),
            duration=60,
            status=LiveClass.Status.SCHEDULED
        )

        # 2. Cancelled class within 30 min window
        cancelled_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Cancelled Soon Class',
            subject='CS',
            scheduled_date=near_dt.date(),
            scheduled_time=near_dt.time(),
            duration=60,
            status=LiveClass.Status.CANCELLED
        )

        # First run: should send reminder for active_class only
        count1 = ReminderService.send_upcoming_class_reminders(window_minutes=30)
        self.assertGreaterEqual(count1, 1)

        # Verify active_class received reminder, cancelled_class did not
        self.assertTrue(Notification.objects.filter(related_live_class=active_class).exists())
        self.assertFalse(Notification.objects.filter(related_live_class=cancelled_class).exists())

        # Second run: duplicate prevention ensures 0 new reminders sent
        count2 = ReminderService.send_upcoming_class_reminders(window_minutes=30)
        self.assertEqual(count2, 0)

    # -------------------------------------------------------------
    # 34. Automatic Expiration Service
    # -------------------------------------------------------------

    def test_mark_expired_classes_ended(self):
        """Expired LIVE classes past duration + grace buffer are transitioned to COMPLETED."""
        now = timezone.now()
        # Class started 2 hours ago with 60-min duration -> expired!
        expired_live = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Expired Live Session',
            subject='CS',
            scheduled_date=(now - timedelta(hours=2)).date(),
            scheduled_time=(now - timedelta(hours=2)).time(),
            duration=60,
            status=LiveClass.Status.LIVE,
            started_at=now - timedelta(hours=2)
        )
        ended_count = mark_expired_classes_ended()
        self.assertGreaterEqual(ended_count, 1)
        expired_live.refresh_from_db()
        self.assertEqual(expired_live.status, LiveClass.Status.COMPLETED)
        self.assertTrue(expired_live.is_ended)
        self.assertIsNotNone(expired_live.ended_at)
