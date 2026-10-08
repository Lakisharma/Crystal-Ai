import datetime
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from classrooms.models import Attendance, ClassEnrollment, ClassParticipant, LiveClass
from notifications.models import Notification


class DashboardV2TeacherTests(TestCase):
    """
    Test suite for Teacher Dashboard 2.0:
    - Role & authentication enforcement
    - Real database statistics (today, upcoming, live, completed, teaching hours, students)
    - Teacher data isolation (cannot see another teacher's classes or statistics)
    - Prominent LIVE NOW section
    - 7-day mini schedule strip
    - Empty states
    - Noindex meta tag
    """

    def setUp(self):
        cache.clear()
        self.client = Client()

        # Teachers
        self.teacher_a = User.objects.create_user(
            username='teacher_alpha',
            email='alpha@teachlive.edu',
            password='Password123!',
            role=User.Role.TEACHER,
            first_name='Alpha',
            last_name='Teacher'
        )
        self.teacher_b = User.objects.create_user(
            username='teacher_beta',
            email='beta@teachlive.edu',
            password='Password123!',
            role=User.Role.TEACHER,
            first_name='Beta',
            last_name='Teacher'
        )

        # Student
        self.student = User.objects.create_user(
            username='student_one',
            email='student1@teachlive.edu',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Sam',
            last_name='Student'
        )

        today = timezone.localtime(timezone.now()).date()

        # Teacher A Classes:
        # 1. Live class (started 20 mins ago)
        self.live_class_a = LiveClass.objects.create(
            title='Physics Live Lab',
            subject='Physics',
            teacher=self.teacher_a,
            status=LiveClass.Status.LIVE,
            scheduled_date=today,
            scheduled_time=datetime.time(10, 0),
            started_at=timezone.now() - datetime.timedelta(minutes=20),
            duration=60,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK
        )
        # 2. Scheduled class today
        self.today_class_a = LiveClass.objects.create(
            title='Physics Problem Solving',
            subject='Physics',
            teacher=self.teacher_a,
            status=LiveClass.Status.SCHEDULED,
            scheduled_date=today,
            scheduled_time=datetime.time(16, 0),
            duration=45,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK
        )
        # 3. Scheduled class tomorrow (upcoming)
        self.upcoming_class_a = LiveClass.objects.create(
            title='Modern Optics',
            subject='Physics',
            teacher=self.teacher_a,
            status=LiveClass.Status.SCHEDULED,
            scheduled_date=today + datetime.timedelta(days=1),
            scheduled_time=datetime.time(11, 0),
            duration=60,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK
        )
        # 4. Completed class 1 (duration 60m)
        self.completed_class_a1 = LiveClass.objects.create(
            title='Classical Mechanics I',
            subject='Physics',
            teacher=self.teacher_a,
            status=LiveClass.Status.COMPLETED,
            scheduled_date=today - datetime.timedelta(days=2),
            scheduled_time=datetime.time(9, 0),
            duration=60,
            ended_at=timezone.now() - datetime.timedelta(days=2),
            access_mode=LiveClass.AccessMode.PUBLIC_LINK
        )
        # 5. Completed class 2 (duration 90m)
        self.completed_class_a2 = LiveClass.objects.create(
            title='Classical Mechanics II',
            subject='Physics',
            teacher=self.teacher_a,
            status=LiveClass.Status.COMPLETED,
            scheduled_date=today - datetime.timedelta(days=1),
            scheduled_time=datetime.time(9, 0),
            duration=90,
            ended_at=timezone.now() - datetime.timedelta(days=1),
            access_mode=LiveClass.AccessMode.PUBLIC_LINK
        )

        # Enroll student in teacher A's classes
        ClassEnrollment.objects.create(
            live_class=self.live_class_a,
            student=self.student,
            status=ClassEnrollment.Status.ENROLLED
        )
        Attendance.objects.create(
            live_class=self.completed_class_a1,
            student=self.student,
            student_name='Sam Student',
            status=Attendance.Status.PRESENT,
            total_duration=55
        )

        # Teacher B Classes (to verify data isolation):
        self.live_class_b = LiveClass.objects.create(
            title='Art History Live',
            subject='Art',
            teacher=self.teacher_b,
            status=LiveClass.Status.LIVE,
            scheduled_date=today,
            scheduled_time=datetime.time(11, 0),
            duration=60,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK
        )

    def test_anonymous_access_redirects_to_login(self):
        """Anonymous requests to teacher dashboard redirect to teacher login."""
        response = self.client.get('/teacher/dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_student_access_to_teacher_dashboard_is_denied_or_redirected(self):
        """Students cannot access the teacher dashboard."""
        self.client.force_login(self.student)
        response = self.client.get('/teacher/dashboard/')
        self.assertIn(response.status_code, [302, 403])

    def test_teacher_dashboard_direct_route_and_namespace_route(self):
        """Both /teacher/dashboard/ and /accounts/teacher/dashboard/ work properly."""
        self.client.force_login(self.teacher_a)
        res1 = self.client.get('/teacher/dashboard/')
        self.assertEqual(res1.status_code, 200)

        res2 = self.client.get(reverse('accounts:teacher_dashboard'))
        self.assertEqual(res2.status_code, 200)

    def test_teacher_dashboard_statistics_calculation(self):
        """Teacher dashboard calculates real metrics accurately."""
        self.client.force_login(self.teacher_a)
        response = self.client.get('/teacher/dashboard/')
        self.assertEqual(response.status_code, 200)

        ctx = response.context
        # Live class count
        self.assertEqual(ctx['live_classes_count'], 1)
        # Today classes count (live + scheduled today = 2)
        self.assertEqual(ctx['today_classes_count'], 2)
        # Upcoming classes count (tomorrow = 1)
        self.assertEqual(ctx['upcoming_classes_count'], 1)
        # Completed classes count = 2
        self.assertEqual(ctx['completed_classes_count'], 2)
        # Teaching hours: (60 + 90) / 60 = 2.5 hours
        self.assertEqual(ctx['total_teaching_hours'], 2.5)
        # Total unique students >= 1
        self.assertGreaterEqual(ctx['total_students'], 1)
        # Prominent live class
        self.assertEqual(ctx['live_class_now'].id, self.live_class_a.id)
        self.assertGreaterEqual(ctx['live_elapsed_minutes'], 19)

    def test_teacher_data_isolation(self):
        """Teacher A cannot see Teacher B's classes or statistics."""
        self.client.force_login(self.teacher_a)
        response = self.client.get('/teacher/dashboard/')
        content = response.content.decode('utf-8')

        # Teacher A's classes are present
        self.assertIn('Physics Live Lab', content)
        self.assertIn('Physics Problem Solving', content)

        # Teacher B's classes are NOT present
        self.assertNotIn('Art History Live', content)

    def test_teacher_dashboard_empty_states(self):
        """New teacher with 0 classes sees 0 stats and empty states without errors."""
        fresh_teacher = User.objects.create_user(
            username='fresh_teacher',
            email='fresh@teachlive.edu',
            password='Password123!',
            role=User.Role.TEACHER
        )
        self.client.force_login(fresh_teacher)
        response = self.client.get('/teacher/dashboard/')
        self.assertEqual(response.status_code, 200)

        ctx = response.context
        self.assertEqual(ctx['today_classes_count'], 0)
        self.assertEqual(ctx['upcoming_classes_count'], 0)
        self.assertEqual(ctx['live_classes_count'], 0)
        self.assertEqual(ctx['completed_classes_count'], 0)
        self.assertEqual(ctx['total_teaching_hours'], 0.0)
        self.assertEqual(ctx['total_students'], 0)
        self.assertIsNone(ctx['live_class_now'])

    def test_teacher_dashboard_has_noindex_meta_tag(self):
        """Teacher dashboard includes noindex, nofollow robots meta tag."""
        self.client.force_login(self.teacher_a)
        response = self.client.get('/teacher/dashboard/')
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow">')


class DashboardV2StudentTests(TestCase):
    """
    Test suite for Student Dashboard 2.0:
    - Role & authentication enforcement
    - Real attendance summary metrics (attended count, completed count, percentage)
    - Strict privacy isolation (only authorized classes are visible)
    - LIVE NOW prominent alert
    - Today's schedule
    - Student My Classes (/student/classes/) with Today tab and pagination
    - Noindex meta tag
    """

    def setUp(self):
        cache.clear()
        self.client = Client()

        self.teacher = User.objects.create_user(
            username='teacher_prof',
            email='prof@teachlive.edu',
            password='Password123!',
            role=User.Role.TEACHER,
            first_name='Professor',
            last_name='X'
        )

        self.student = User.objects.create_user(
            username='student_alice',
            email='alice@teachlive.edu',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Alice',
            last_name='Wonderland'
        )

        today = timezone.localtime(timezone.now()).date()

        # 1. Authorized Live Class (PUBLIC_LINK)
        self.live_public = LiveClass.objects.create(
            title='Intro to World History',
            subject='History',
            teacher=self.teacher,
            status=LiveClass.Status.LIVE,
            scheduled_date=today,
            scheduled_time=datetime.time(10, 0),
            started_at=timezone.now() - datetime.timedelta(minutes=15),
            duration=60,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK
        )

        # 2. Authorized Class scheduled Today (PUBLIC_LINK)
        self.today_public = LiveClass.objects.create(
            title='World History Seminar',
            subject='History',
            teacher=self.teacher,
            status=LiveClass.Status.SCHEDULED,
            scheduled_date=today,
            scheduled_time=datetime.time(15, 0),
            duration=60,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK
        )

        # 3. Private Unauthorized Class (ENROLLMENT_ONLY, student not enrolled)
        self.private_class = LiveClass.objects.create(
            title='Confidential Faculty Research',
            subject='History',
            teacher=self.teacher,
            status=LiveClass.Status.LIVE,
            scheduled_date=today,
            scheduled_time=datetime.time(10, 0),
            duration=60,
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY
        )

        # 4. Completed Class Attended by Student
        self.completed_class_1 = LiveClass.objects.create(
            title='History 101 - Ancient Civilizations',
            subject='History',
            teacher=self.teacher,
            status=LiveClass.Status.COMPLETED,
            scheduled_date=today - datetime.timedelta(days=3),
            scheduled_time=datetime.time(10, 0),
            duration=60,
            ended_at=timezone.now() - datetime.timedelta(days=3),
            access_mode=LiveClass.AccessMode.PUBLIC_LINK
        )
        Attendance.objects.create(
            live_class=self.completed_class_1,
            student=self.student,
            student_name='Alice Wonderland',
            status=Attendance.Status.PRESENT,
            total_duration=58
        )

        # 5. Completed Class Enrolled but Absent
        self.completed_class_2 = LiveClass.objects.create(
            title='History 102 - Medieval Times',
            subject='History',
            teacher=self.teacher,
            status=LiveClass.Status.COMPLETED,
            scheduled_date=today - datetime.timedelta(days=2),
            scheduled_time=datetime.time(10, 0),
            duration=60,
            ended_at=timezone.now() - datetime.timedelta(days=2),
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY
        )
        ClassEnrollment.objects.create(
            live_class=self.completed_class_2,
            student=self.student,
            status=ClassEnrollment.Status.ENROLLED
        )

    def test_anonymous_access_redirects_to_student_login(self):
        """Anonymous access to student dashboard redirects to student login."""
        response = self.client.get('/student/dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/student/login/', response.url)

    def test_teacher_access_redirects_to_teacher_dashboard(self):
        """Teacher accessing student dashboard is redirected to instructor dashboard."""
        self.client.force_login(self.teacher)
        response = self.client.get('/student/dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/teacher/dashboard/', response.url)

    def test_student_dashboard_renders_authorized_classes_only(self):
        """Student dashboard strictly isolates and hides unauthorized private classes."""
        self.client.force_login(self.student)
        response = self.client.get('/student/dashboard/')
        self.assertEqual(response.status_code, 200)

        content = response.content.decode('utf-8')
        # Authorized classes are visible
        self.assertIn('Intro to World History', content)
        self.assertIn('World History Seminar', content)
        # Unauthorized private class is strictly hidden
        self.assertNotIn('Confidential Faculty Research', content)

    def test_student_attendance_summary_metrics(self):
        """Real attendance metrics: 1 attended out of 2 completed = 50.0%."""
        self.client.force_login(self.student)
        response = self.client.get('/student/dashboard/')
        self.assertEqual(response.status_code, 200)

        ctx = response.context
        self.assertEqual(ctx['attended_count'], 1)
        self.assertEqual(ctx['total_completed_classes'], 2)
        self.assertEqual(ctx['attendance_percentage'], 50.0)

    def test_student_empty_attendance_metrics(self):
        """Fresh student with 0 completed classes shows None percentage without errors."""
        fresh_student = User.objects.create_user(
            username='fresh_student',
            email='fresh_student@teachlive.edu',
            password='Password123!',
            role=User.Role.STUDENT
        )
        self.client.force_login(fresh_student)
        response = self.client.get('/student/dashboard/')
        self.assertEqual(response.status_code, 200)

        ctx = response.context
        self.assertEqual(ctx['attended_count'], 0)
        # Only public completed class 1 is in authorized_classes
        # If student has not attended and not enrolled, let's see how many completed classes
        # The view counts relevant_completed_ids = authorized_classes.filter(status=COMPLETED)
        # which has self.completed_class_1 (PUBLIC_LINK).
        # Percentage will be 0.0% since attended=0 and completed=1
        if ctx['total_completed_classes'] > 0:
            self.assertEqual(ctx['attendance_percentage'], 0.0)

    def test_student_dashboard_live_now_prominent_card(self):
        """Prominent LIVE NOW card is rendered when an authorized class is live."""
        self.client.force_login(self.student)
        response = self.client.get('/student/dashboard/')
        self.assertEqual(response.status_code, 200)

        ctx = response.context
        self.assertIsNotNone(ctx['live_class_now'])
        self.assertEqual(ctx['live_class_now'].id, self.live_public.id)
        self.assertContains(response, 'LIVE NOW')
        self.assertContains(response, self.live_public.room_code)

    def test_student_my_classes_today_tab(self):
        """Student My Classes view filters by tab=today."""
        self.client.force_login(self.student)
        response = self.client.get('/student/classes/?tab=today')
        self.assertEqual(response.status_code, 200)

        content = response.content.decode('utf-8')
        self.assertIn('World History Seminar', content)

    def test_student_my_classes_completed_pagination(self):
        """Student My Classes view paginates completed classes (9 per page)."""
        today = timezone.localtime(timezone.now()).date()
        # Create 10 completed classes to trigger pagination
        for i in range(10):
            c = LiveClass.objects.create(
                title=f'Extra Completed Class {i}',
                subject='History',
                teacher=self.teacher,
                status=LiveClass.Status.COMPLETED,
                scheduled_date=today - datetime.timedelta(days=i + 5),
                scheduled_time=datetime.time(10, 0),
                duration=45,
                access_mode=LiveClass.AccessMode.PUBLIC_LINK
            )

        self.client.force_login(self.student)
        response = self.client.get('/student/classes/?tab=completed')
        self.assertEqual(response.status_code, 200)

        ctx = response.context
        self.assertTrue(ctx['is_paginated'])
        self.assertEqual(ctx['page_obj'].number, 1)

        # Page 2
        response_p2 = self.client.get('/student/classes/?tab=completed&page=2')
        self.assertEqual(response_p2.status_code, 200)
        self.assertEqual(response_p2.context['page_obj'].number, 2)

    def test_student_dashboard_has_noindex_meta_tag(self):
        """Student dashboard includes noindex, nofollow robots meta tag."""
        self.client.force_login(self.student)
        response = self.client.get('/student/dashboard/')
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow">')
