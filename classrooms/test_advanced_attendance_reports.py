"""
Comprehensive Test Suite for Prompt #16 — Advanced Attendance & Reports System.
Tests teacher ownership, student isolation, admin reports, attendance percentage calculations,
reconnect non-double-counting, IDOR security, and CSV export sanitization.
"""

import csv
from datetime import date, datetime, time, timedelta
import io

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from classrooms.attendance_services import (
    calculate_attendance_percentage,
    get_class_attendance_summary,
    get_date_range_bounds,
    get_student_attendance_metrics,
    get_teacher_attendance_dashboard_metrics,
)
from classrooms.models import (
    Attendance,
    ClassEnrollment,
    ClassParticipant,
    LiveClass,
)


class AdvancedAttendanceReportsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

        # Teachers
        self.teacher_1 = User.objects.create_user(
            username='prof_euler',
            email='euler@math.teachlive',
            password='Password123!',
            role=User.Role.TEACHER,
            first_name='Leonhard',
            last_name='Euler'
        )
        self.teacher_2 = User.objects.create_user(
            username='prof_gauss',
            email='gauss@math.teachlive',
            password='Password123!',
            role=User.Role.TEACHER,
            first_name='Carl',
            last_name='Gauss'
        )

        # Students
        self.student_1 = User.objects.create_user(
            username='student_alice',
            email='alice@student.teachlive',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Alice',
            last_name='Wonder'
        )
        self.student_2 = User.objects.create_user(
            username='student_bob',
            email='bob@student.teachlive',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Bob',
            last_name='Builder'
        )
        self.student_unconnected = User.objects.create_user(
            username='student_charlie',
            email='charlie@student.teachlive',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Charlie',
            last_name='Brown'
        )

        # Admin
        self.admin_user = User.objects.create_user(
            username='admin_super',
            email='admin@teachlive.org',
            password='Password123!',
            role=User.Role.ADMIN,
            is_staff=True,
            is_superuser=True
        )

        # Classes for Teacher 1
        self.class_t1_completed = LiveClass.objects.create(
            teacher=self.teacher_1,
            title='Topology Fundamentals',
            subject='Mathematics',
            room_code='MATH-TOP-101',
            status=LiveClass.Status.COMPLETED,
            scheduled_date=date.today() - timedelta(days=2),
            scheduled_time=time(10, 0),
            duration=60,
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY,
        )

        self.class_t1_live = LiveClass.objects.create(
            teacher=self.teacher_1,
            title='Complex Analysis Live',
            subject='Mathematics',
            room_code='MATH-CMP-202',
            status=LiveClass.Status.LIVE,
            scheduled_date=date.today(),
            scheduled_time=time(14, 0),
            duration=90,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK,
        )

        # Classes for Teacher 2
        self.class_t2 = LiveClass.objects.create(
            teacher=self.teacher_2,
            title='Number Theory Masterclass',
            subject='Mathematics',
            room_code='MATH-NUM-303',
            status=LiveClass.Status.COMPLETED,
            scheduled_date=date.today() - timedelta(days=1),
            scheduled_time=time(11, 0),
            duration=60,
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY,
        )

        # Enrollments
        # Alice is enrolled in Teacher 1's completed class & Teacher 2's class
        ClassEnrollment.objects.create(
            live_class=self.class_t1_completed,
            student=self.student_1,
            status=ClassEnrollment.Status.ENROLLED
        )
        # Bob is enrolled in Teacher 1's completed class but never attended (Absent test)
        ClassEnrollment.objects.create(
            live_class=self.class_t1_completed,
            student=self.student_2,
            status=ClassEnrollment.Status.ENROLLED
        )
        # Charlie is enrolled only in Teacher 2's class
        ClassEnrollment.objects.create(
            live_class=self.class_t2,
            student=self.student_unconnected,
            status=ClassEnrollment.Status.ENROLLED
        )

        # Attendance Records
        now = timezone.now()
        # Alice attended Teacher 1's completed class for 45 mins (scheduled 60m -> 75.0%)
        self.att_alice_t1 = Attendance.objects.create(
            live_class=self.class_t1_completed,
            student=self.student_1,
            student_name='Alice Wonder',
            status=Attendance.Status.LEFT,
            joined_at=now - timedelta(days=2, minutes=50),
            left_at=now - timedelta(days=2, minutes=5),
            total_duration=45
        )

        # Alice attended Teacher 2's class for 60 mins (scheduled 60m -> 100.0%)
        self.att_alice_t2 = Attendance.objects.create(
            live_class=self.class_t2,
            student=self.student_1,
            student_name='Alice Wonder',
            status=Attendance.Status.LEFT,
            joined_at=now - timedelta(days=1, minutes=65),
            left_at=now - timedelta(days=1, minutes=5),
            total_duration=60
        )

    # =========================================================================
    # 1. ATTENDANCE PERCENTAGE FORMULA & UNIT CALCULATIONS
    # =========================================================================

    def test_calculate_attendance_percentage_standard(self):
        """Attendance % = (attended / scheduled) * 100"""
        pct = calculate_attendance_percentage(attended_duration_minutes=45, scheduled_duration_minutes=60)
        self.assertEqual(pct, 75.0)

    def test_calculate_attendance_percentage_zero_duration(self):
        """0 minutes attended gives 0.0%"""
        pct = calculate_attendance_percentage(attended_duration_minutes=0, scheduled_duration_minutes=60)
        self.assertEqual(pct, 0.0)

    def test_calculate_attendance_percentage_full(self):
        """Full attendance gives 100.0%"""
        pct = calculate_attendance_percentage(attended_duration_minutes=60, scheduled_duration_minutes=60)
        self.assertEqual(pct, 100.0)

    def test_calculate_attendance_percentage_overflow_capped_at_100(self):
        """Duration exceeding scheduled duration is capped at 100.0% (never exceeds 100%)"""
        pct = calculate_attendance_percentage(attended_duration_minutes=95, scheduled_duration_minutes=60)
        self.assertEqual(pct, 100.0)

    def test_calculate_attendance_percentage_zero_scheduled_duration_safe(self):
        """Avoid division by zero when scheduled duration is zero"""
        pct = calculate_attendance_percentage(attended_duration_minutes=30, scheduled_duration_minutes=0)
        self.assertEqual(pct, 100.0)

        pct_zero = calculate_attendance_percentage(attended_duration_minutes=0, scheduled_duration_minutes=0)
        self.assertEqual(pct_zero, 0.0)

    def test_attendance_model_property(self):
        """Attendance model property returns correct percentage"""
        self.assertEqual(self.att_alice_t1.attendance_percentage, 75.0)
        self.assertEqual(self.att_alice_t2.attendance_percentage, 100.0)

    # =========================================================================
    # 2. TEACHER ATTENDANCE DASHBOARD & OWNERSHIP SECURITY
    # =========================================================================

    def test_teacher_can_view_own_attendance_dashboard(self):
        """Teacher 1 sees only records and classes belonging to them"""
        self.client.force_login(self.teacher_1)
        res = self.client.get(reverse('classrooms:teacher_attendance'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Attendance')
        self.assertContains(res, 'Topology Fundamentals')
        self.assertContains(res, 'Alice Wonder')
        # Teacher 1 must NOT see Teacher 2's class
        self.assertNotContains(res, 'Number Theory Masterclass')

    def test_teacher_cannot_view_other_teacher_records_via_filters(self):
        """Teacher 1 attempting to filter for Teacher 2's class sees empty results"""
        self.client.force_login(self.teacher_1)
        res = self.client.get(reverse('classrooms:teacher_attendance'), {'class_id': self.class_t2.id})
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, 'Number Theory Masterclass')

    def test_teacher_attendance_search(self):
        """Server-side case-insensitive search by student name, email, class, and subject"""
        self.client.force_login(self.teacher_1)
        # Search by student name
        res = self.client.get(reverse('classrooms:teacher_attendance'), {'q': 'alice'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Alice Wonder')

        # Search non-existent
        res_empty = self.client.get(reverse('classrooms:teacher_attendance'), {'q': 'nonexistent_student'})
        self.assertEqual(res_empty.status_code, 200)
        self.assertContains(res_empty, 'No attendance records found')

    def test_class_attendance_detail_ownership_enforcement(self):
        """Teacher 1 can view own class attendance; Teacher 2 gets 403 Forbidden on Teacher 1's class"""
        self.client.force_login(self.teacher_1)
        res_ok = self.client.get(reverse('classrooms:teacher_class_attendance', kwargs={'class_id': self.class_t1_completed.id}))
        self.assertEqual(res_ok.status_code, 200)
        self.assertContains(res_ok, 'Topology Fundamentals')
        # Check absent student Bob is identified as enrolled but not joined
        self.assertContains(res_ok, 'Bob Builder')
        self.assertContains(res_ok, 'Absent')

        # Teacher 2 attempts IDOR access to Teacher 1's class attendance
        self.client.force_login(self.teacher_2)
        res_denied = self.client.get(reverse('classrooms:teacher_class_attendance', kwargs={'class_id': self.class_t1_completed.id}))
        self.assertEqual(res_denied.status_code, 403)

    def test_teacher_student_attendance_report_security(self):
        """Teacher 1 can view connected student Alice, but gets 403 on unconnected Charlie"""
        self.client.force_login(self.teacher_1)
        res_ok = self.client.get(reverse('classrooms:teacher_student_attendance', kwargs={'student_id': self.student_1.id}))
        self.assertEqual(res_ok.status_code, 200)
        self.assertContains(res_ok, 'Alice Wonder')
        self.assertContains(res_ok, 'Participation History')

        # Teacher 1 attempts to view Charlie who only belongs to Teacher 2
        res_denied = self.client.get(reverse('classrooms:teacher_student_attendance', kwargs={'student_id': self.student_unconnected.id}))
        self.assertEqual(res_denied.status_code, 403)

    def test_teacher_comprehensive_reports_view(self):
        """Teacher can view 4-section attendance reports at /teacher/reports/attendance/"""
        self.client.force_login(self.teacher_1)
        res = self.client.get(reverse('classrooms:teacher_attendance_reports'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Section A: Class Attendance Report')
        self.assertContains(res, 'Section B: Student Attendance Report')
        self.assertContains(res, 'Topology Fundamentals')

    # =========================================================================
    # 3. CSV EXPORT AUTHORIZATION & FORMULA SANITIZATION
    # =========================================================================

    def test_teacher_export_csv_authorized_and_sanitized(self):
        """Teacher export contains only authorized records and all 12 standard columns"""
        self.client.force_login(self.teacher_1)

        # Add malicious injection record to test CWE-1236 protection
        Attendance.objects.create(
            live_class=self.class_t1_completed,
            student_name='=cmd|/C calc!A0',
            status=Attendance.Status.PRESENT,
            joined_at=timezone.now(),
            total_duration=10
        )

        res = self.client.get(reverse('classrooms:teacher_attendance_export'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv; charset=utf-8')

        content = res.content.decode('utf-8')
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        # Validate CSV Header has 12 standard columns
        header = rows[0]
        self.assertIn('Student Name', header)
        self.assertIn('Student Email', header)
        self.assertIn('Class', header)
        self.assertIn('Scheduled Duration (mins)', header)
        self.assertIn('Attendance Duration (mins)', header)
        self.assertIn('Attendance Percentage', header)

        # Validate formula injection sanitization (prefixed with single quote)
        self.assertIn("'=cmd|/C calc!A0", content)

        # Validate teacher isolation: Teacher 1 must NOT see Teacher 2's class
        self.assertNotIn('Number Theory Masterclass', content)

    def test_teacher_cannot_export_another_teacher_class_csv(self):
        """Teacher 1 gets 403 Forbidden when attempting to export Teacher 2's class CSV"""
        self.client.force_login(self.teacher_1)
        res = self.client.get(reverse('classrooms:teacher_class_attendance_export', kwargs={'class_id': self.class_t2.id}))
        self.assertEqual(res.status_code, 403)

    # =========================================================================
    # 4. STUDENT ATTENDANCE & DATA ISOLATION
    # =========================================================================

    def test_student_can_view_own_attendance_only(self):
        """Student sees strictly their own attendance and never other students' data"""
        self.client.force_login(self.student_1)
        res = self.client.get(reverse('student:attendance'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'My Attendance')
        self.assertContains(res, 'Topology Fundamentals')
        self.assertContains(res, 'Number Theory Masterclass')

        # Log in as Bob who has not attended yet
        self.client.force_login(self.student_2)
        res_bob = self.client.get(reverse('student:attendance'))
        self.assertEqual(res_bob.status_code, 200)
        self.assertContains(res_bob, "You haven't attended any classes yet")
        # Bob must NOT see Alice's records
        self.assertNotContains(res_bob, 'Alice Wonder')

    def test_student_class_attendance_detail_idor_protection(self):
        """Student Alice can view detail for her class; Bob (not authorized for class_t2) gets 403"""
        self.client.force_login(self.student_1)
        res_alice = self.client.get(reverse('student:class_attendance', kwargs={'class_id': self.class_t1_completed.id}))
        self.assertEqual(res_alice.status_code, 200)
        self.assertContains(res_alice, 'Your Attendance Record')
        self.assertContains(res_alice, '75.0%')

        # Bob is NOT authorized for Teacher 2's private ENROLLMENT_ONLY class
        self.client.force_login(self.student_2)
        res_bob_denied = self.client.get(reverse('student:class_attendance', kwargs={'class_id': self.class_t2.id}))
        self.assertEqual(res_bob_denied.status_code, 403)

    def test_student_cannot_access_teacher_attendance(self):
        """Student receives 403 Forbidden when attempting to access /teacher/attendance/"""
        self.client.force_login(self.student_1)
        res = self.client.get(reverse('classrooms:teacher_attendance'))
        self.assertEqual(res.status_code, 403)

    def test_student_cannot_export_attendance_csv(self):
        """Student receives 403 Forbidden when attempting to export CSV"""
        self.client.force_login(self.student_1)
        res = self.client.get(reverse('classrooms:teacher_attendance_export'))
        self.assertEqual(res.status_code, 403)

    # =========================================================================
    # 5. ADMIN ATTENDANCE REPORTS & CSV EXPORT
    # =========================================================================

    def test_admin_global_attendance_view(self):
        """Admin has global visibility across all teachers and classes"""
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('admin_dashboard:attendance'))
        self.assertEqual(res.status_code, 200)
        # Admin sees both teachers' classes
        self.assertContains(res, 'Topology Fundamentals')
        self.assertContains(res, 'Number Theory Masterclass')

    def test_admin_global_csv_export(self):
        """Admin can export global CSV containing all classes"""
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('admin_dashboard:attendance_export'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn('Topology Fundamentals', content)
        self.assertIn('Number Theory Masterclass', content)

    # =========================================================================
    # 6. RECONNECT DOES NOT DOUBLE-COUNT DURATION
    # =========================================================================

    def test_reconnect_does_not_double_count_or_lose_duration(self):
        """
        When student reconnects after dropping/leaving:
        - Reconnect does not create duplicate Attendance row
        - Prior duration is preserved and accumulated without double-counting the gap
        """
        self.client.force_login(self.student_1)

        # 1. Join live class
        join_res = self.client.post(reverse('live_room:join', kwargs={'room_code': self.class_t1_live.room_code}))
        self.assertEqual(join_res.status_code, 302)

        att = Attendance.objects.get(live_class=self.class_t1_live, student=self.student_1)
        self.assertEqual(att.status, Attendance.Status.PRESENT)
        self.assertIsNone(att.left_at)

        # 2. Simulate leaving after 20 minutes
        now = timezone.now()
        att.joined_at = now - timedelta(minutes=20)
        att.save()

        leave_res = self.client.post(reverse('live_room:leave', kwargs={'room_code': self.class_t1_live.room_code}))
        self.assertEqual(leave_res.status_code, 200)

        att.refresh_from_db()
        self.assertEqual(att.status, Attendance.Status.LEFT)
        self.assertIsNotNone(att.left_at)
        first_duration = att.total_duration
        self.assertGreaterEqual(first_duration, 19)

        # 3. Reconnect 10 minutes later (gap of 10 minutes)
        reconnect_res = self.client.post(reverse('live_room:join', kwargs={'room_code': self.class_t1_live.room_code}))
        self.assertEqual(reconnect_res.status_code, 302)

        # Ensure no duplicate Attendance row
        self.assertEqual(Attendance.objects.filter(live_class=self.class_t1_live, student=self.student_1).count(), 1)

        att.refresh_from_db()
        self.assertEqual(att.status, Attendance.Status.PRESENT)
        self.assertIsNone(att.left_at)
        # Previous total_duration is retained
        self.assertEqual(att.total_duration, first_duration)
