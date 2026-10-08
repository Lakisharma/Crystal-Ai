"""
Security Hardening and Production Readiness Test Suite for TeachLive.
Tests verify:
- Production security configurations (DEBUG, SECRET_KEY, ALLOWED_HOSTS, Cookies, CSP)
- Authentication security and brute-force rate-limiting
- IDOR protection across teachers and students
- LiveKit token generation security and role boundaries
- Open redirect defenses
- Error handling without debug leakage (400, 403, 404, 500)
- Health probe endpoint integrity (/health/)
"""

from datetime import date, time, timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.student_views import is_safe_redirect_url
from classrooms.livekit_service import (
    generate_livekit_access_token,
    get_livekit_room_name,
)
from classrooms.models import Attendance, ClassEnrollment, Classroom, LiveClass
from core.rate_limit import (
    clear_failed_attempts,
    get_client_ip,
    is_rate_limited,
    record_failed_attempt,
)

User = get_user_model()


class SecurityHardeningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Teacher 1
        cls.teacher1 = User.objects.create_user(
            username='prof_hardened_1',
            email='teacher1.security@teachlive.edu',
            password='TeacherPass123!@#',
            role=User.Role.TEACHER,
            first_name='Prof One',
        )
        # Teacher 2
        cls.teacher2 = User.objects.create_user(
            username='prof_hardened_2',
            email='teacher2.security@teachlive.edu',
            password='TeacherPass123!@#',
            role=User.Role.TEACHER,
            first_name='Prof Two',
        )
        # Student 1
        cls.student1 = User.objects.create_user(
            username='student_hardened_1',
            email='student1.security@teachlive.edu',
            password='StudentPass123!@#',
            role=User.Role.STUDENT,
            first_name='Student One',
        )
        # Student 2
        cls.student2 = User.objects.create_user(
            username='student_hardened_2',
            email='student2.security@teachlive.edu',
            password='StudentPass123!@#',
            role=User.Role.STUDENT,
            first_name='Student Two',
        )

        today = timezone.localdate()
        # Teacher 1's Live Class
        cls.class_t1 = LiveClass.objects.create(
            title="Advanced Security Hardening Class",
            teacher=cls.teacher1,
            room_code="SEC101",
            scheduled_date=today,
            scheduled_time=time(10, 0),
            duration=60,
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY,
            status=LiveClass.Status.LIVE,
        )

        # Teacher 2's Live Class
        cls.class_t2 = LiveClass.objects.create(
            title="Teacher Two's Private Class",
            teacher=cls.teacher2,
            room_code="SEC202",
            scheduled_date=today,
            scheduled_time=time(11, 0),
            duration=60,
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY,
            status=LiveClass.Status.LIVE,
        )

        # Enroll Student 1 in Teacher 1's Class
        ClassEnrollment.objects.create(
            live_class=cls.class_t1,
            student=cls.student1,
            status=ClassEnrollment.Status.ENROLLED,
        )

    def setUp(self):
        self.client = Client()
        cache.clear()

    def tearDown(self):
        cache.clear()

    # -------------------------------------------------------------------------
    # 1. Health Endpoint Tests
    # -------------------------------------------------------------------------
    def test_health_check_endpoint(self):
        """Verifies /health/ returns 200 with status ok and CSP/security headers."""
        response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'ok')
        self.assertIn(data.get('service'), ['Crystal AI', 'TeachLive'])
        self.assertNotIn('password', str(data))
        self.assertNotIn('secret', str(data))
        self.assertIn('Content-Security-Policy', response.headers)
        self.assertIn('Permissions-Policy', response.headers)

    # -------------------------------------------------------------------------
    # 2. Production Security Settings & Cookie Flags
    # -------------------------------------------------------------------------
    def test_security_headers_middleware(self):
        """Verifies that SecurityHeadersMiddleware attaches CSP and Permissions-Policy."""
        response = self.client.get(reverse('core:home'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Content-Security-Policy', response.headers)
        csp = response.headers['Content-Security-Policy']
        self.assertIn("default-src 'self'", csp)
        self.assertIn("script-src", csp)
        self.assertIn("connect-src 'self' wss:", csp)
        self.assertIn('Permissions-Policy', response.headers)

    def test_allowed_hosts_includes_render(self):
        """Verifies ALLOWED_HOSTS contains the Render production domain."""
        self.assertIn('live-class-1.onrender.com', settings.ALLOWED_HOSTS)
        self.assertIn('.onrender.com', settings.ALLOWED_HOSTS)

    # -------------------------------------------------------------------------
    # 3. Open Redirect Defenses
    # -------------------------------------------------------------------------
    def test_open_redirect_defense(self):
        """Verifies that external malicious redirect URLs are rejected."""
        class MockRequest:
            def get_host(self):
                return 'live-class-1.onrender.com'
            def is_secure(self):
                return True

        req = MockRequest()
        self.assertFalse(is_safe_redirect_url('https://malicious-site.com', req))
        self.assertFalse(is_safe_redirect_url('//evil.org/phish', req))
        self.assertFalse(is_safe_redirect_url('javascript:alert(1)', req))
        self.assertTrue(is_safe_redirect_url('/student/dashboard/', req))
        self.assertTrue(is_safe_redirect_url('/classrooms/list/?tab=active', req))

    # -------------------------------------------------------------------------
    # 4. Brute-Force Rate Limiting on Login
    # -------------------------------------------------------------------------
    def test_login_brute_force_rate_limiting(self):
        """Verifies that 5 failed attempts trigger rate-limiting without crashing."""
        login_url = reverse('accounts:teacher_login')
        ip = "192.168.100.1"

        # Simulate 5 failed login attempts
        for _ in range(5):
            self.client.post(
                login_url,
                {'username': 'prof_hardened_1', 'password': 'WrongPassword1!'},
                REMOTE_ADDR=ip
            )

        # 6th attempt should be blocked by rate limiter
        resp = self.client.post(
            login_url,
            {'username': 'prof_hardened_1', 'password': 'TeacherPass123!@#'},
            REMOTE_ADDR=ip
        )
        self.assertContains(resp, "Too many failed sign-in attempts")

        # Clear rate limit manually and verify login succeeds
        clear_failed_attempts(f"auth_login_fail_{ip}")
        resp_success = self.client.post(
            login_url,
            {'username': 'prof_hardened_1', 'password': 'TeacherPass123!@#'},
            REMOTE_ADDR=ip
        )
        self.assertEqual(resp_success.status_code, 302)

    # -------------------------------------------------------------------------
    # 5. IDOR & Authorization Boundaries
    # -------------------------------------------------------------------------
    def test_teacher_cannot_access_other_teacher_class_students(self):
        """IDOR Defense: Teacher 1 must NOT access Teacher 2's student management."""
        self.client.force_login(self.teacher1)
        url = reverse('teacher_class_students_id_direct', kwargs={'class_id': self.class_t2.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_end_other_teacher_class(self):
        """IDOR Defense: Teacher 1 must NOT end Teacher 2's live class."""
        self.client.force_login(self.teacher1)
        url = reverse('live_room:end', kwargs={'room_code': self.class_t2.room_code})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)
        self.class_t2.refresh_from_db()
        self.assertEqual(self.class_t2.status, LiveClass.Status.LIVE)

    def test_student_cannot_access_teacher_live_room(self):
        """Role Boundary: Student must NOT access Teacher live room."""
        self.client.force_login(self.student1)
        url = reverse('live_room:teacher_live', kwargs={'room_code': self.class_t1.room_code})
        response = self.client.get(url)
        # Should redirect to teacher login or return 403
        self.assertIn(response.status_code, (302, 403))

    def test_student_cannot_access_other_student_attendance(self):
        """Privacy Defense: Student 1 must NOT access Student 2's detailed attendance."""
        self.client.force_login(self.student1)
        url = reverse('teacher_student_attendance_direct', kwargs={'student_id': self.student2.id})
        response = self.client.get(url)
        self.assertIn(response.status_code, (302, 403))

    def test_teacher_attendance_export_strictly_scoped(self):
        """IDOR Defense: Attendance CSV export must only include the teacher's own classes."""
        self.client.force_login(self.teacher1)
        url = reverse('teacher_attendance_export')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        content = response.content.decode('utf-8')
        # Teacher 2's class title must not appear
        self.assertNotIn("Teacher Two's Private Class", content)

    # -------------------------------------------------------------------------
    # 6. LiveKit Token Security
    # -------------------------------------------------------------------------
    @patch.dict('os.environ', {
        'LIVEKIT_URL': 'wss://test.livekit.cloud',
        'LIVEKIT_API_KEY': 'devkey',
        'LIVEKIT_API_SECRET': 'secret_1234567890_super_secret',
    })
    def test_livekit_token_generation_boundaries(self):
        """Verifies LiveKit access tokens never expose the secret and respect roles."""
        # Teacher token
        teacher_token = generate_livekit_access_token(self.class_t1, self.teacher1, is_teacher=True)
        self.assertIn('token', teacher_token)
        self.assertTrue(teacher_token['is_teacher'])
        self.assertTrue(teacher_token['can_publish'])
        self.assertNotIn('secret_1234567890', str(teacher_token))

        # Student token
        student_token = generate_livekit_access_token(self.class_t1, self.student1, is_teacher=False)
        self.assertIn('token', student_token)
        self.assertFalse(student_token['is_teacher'])
        self.assertFalse(student_token['can_publish'])
        self.assertNotIn('secret_1234567890', str(student_token))

    # -------------------------------------------------------------------------
    # 7. Unauthenticated LiveKit Token Attempt
    # -------------------------------------------------------------------------
    def test_unauthenticated_livekit_token_denied(self):
        """Verifies anonymous requests cannot generate LiveKit access tokens."""
        url = reverse('live_room:token', kwargs={'room_code': self.class_t1.room_code})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)

    # -------------------------------------------------------------------------
    # 8. Custom Error Views Do Not Leak Debug Tracebacks
    # -------------------------------------------------------------------------
    def test_custom_error_templates(self):
        """Verifies custom error views render branded TeachLive error pages without stack traces."""
        # 404
        r404 = self.client.get('/nonexistent-page-url-12345/')
        self.assertEqual(r404.status_code, 404)
        self.assertContains(r404, "404", status_code=404)
        self.assertNotContains(r404, "Traceback", status_code=404)

        # 400
        r400 = self.client.get('/health/', HTTP_HOST='invalid-host-attack.com')
        self.assertEqual(r400.status_code, 400)
