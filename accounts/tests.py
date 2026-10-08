from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import is_password_usable, check_password
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from classrooms.models import Classroom


class AccountsAuthAndPermissionTests(TestCase):
    def setUp(self):
        self.teacher_password = 'TeacherSecretPassword123!'
        self.teacher = User.objects.create_user(
            username='teacher_jane',
            email='jane@faculty.edu',
            password=self.teacher_password,
            role=User.Role.TEACHER,
            first_name='Jane',
            last_name='Doe',
            bio='Department of Mathematics'
        )

        self.student_password = 'StudentSecretPassword123!'
        self.student = User.objects.create_user(
            username='student_bob',
            email='bob@student.edu',
            password=self.student_password,
            role=User.Role.STUDENT,
            first_name='Bob',
            last_name='Dylan'
        )

        # Create classrooms for teacher with various statuses
        self.class_scheduled = Classroom.objects.create(
            name='Algebra I',
            code='ALG101',
            subject='Math',
            teacher=self.teacher,
            status=Classroom.Status.SCHEDULED
        )
        self.class_live = Classroom.objects.create(
            name='Calculus II',
            code='CALC202',
            subject='Math',
            teacher=self.teacher,
            status=Classroom.Status.LIVE
        )
        self.class_completed = Classroom.objects.create(
            name='Geometry',
            code='GEO303',
            subject='Math',
            teacher=self.teacher,
            status=Classroom.Status.COMPLETED
        )

    def test_password_is_properly_hashed_and_never_plain_text(self):
        """Security: Passwords must be hashed with PBKDF2/Django hasher and not stored plain."""
        self.assertNotEqual(self.teacher.password, self.teacher_password)
        self.assertTrue(self.teacher.password.startswith('pbkdf2_sha256$'))
        self.assertTrue(check_password(self.teacher_password, self.teacher.password))
        self.assertFalse(check_password('WrongPassword!', self.teacher.password))

    def test_teacher_registration_flow(self):
        """1. Teacher registration with session login and password validation."""
        client = Client()
        post_data = {
            'username': 'prof_newton',
            'first_name': 'Isaac',
            'last_name': 'Newton',
            'email': 'newton@cambridge.edu',
            'password1': 'GravityLaw2026!#',
            'password2': 'GravityLaw2026!#',
            'bio': 'Natural Philosophy & Mathematics'
        }
        res = client.post(reverse('accounts:teacher_register'), post_data, follow=True)
        self.assertEqual(res.status_code, 200)

        new_teacher = User.objects.get(username='prof_newton')
        self.assertTrue(new_teacher.is_teacher)
        self.assertTrue(new_teacher.password.startswith('pbkdf2_sha256$'))
        # Session should be authenticated
        self.assertEqual(int(client.session['_auth_user_id']), new_teacher.id)

    def test_teacher_registration_password_validation(self):
        """4. Password validation fails on weak passwords."""
        client = Client()
        post_data = {
            'username': 'weak_user',
            'first_name': 'Weak',
            'last_name': 'Pass',
            'email': 'weak@test.edu',
            'password1': '123',  # Too short
            'password2': '123',
        }
        res = client.post(reverse('accounts:teacher_register'), post_data)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(User.objects.filter(username='weak_user').exists())

    def test_teacher_login_and_session_authentication(self):
        """2 & 5. Teacher login establishes session authentication."""
        client = Client()
        res = client.post(reverse('accounts:teacher_login'), {
            'username': 'teacher_jane',
            'password': self.teacher_password,
        }, follow=True)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(int(client.session['_auth_user_id']), self.teacher.id)
        self.assertTrue(res.context['user'].is_authenticated)

    def test_student_cannot_login_through_teacher_portal(self):
        """Security: Students must not authenticate via teacher login portal."""
        client = Client()
        res = client.post(reverse('accounts:teacher_login'), {
            'username': 'student_bob',
            'password': self.student_password,
        }, follow=True)

        # Should redirect to student login and not be logged in as teacher
        self.assertRedirects(res, reverse('accounts:student_login'))
        self.assertFalse('_auth_user_id' in client.session)

    def test_teacher_logout_terminates_session(self):
        """3. Teacher logout flushes session securely."""
        client = Client()
        client.login(username='teacher_jane', password=self.teacher_password)
        self.assertTrue('_auth_user_id' in client.session)

        res = client.get(reverse('accounts:logout'), follow=True)
        self.assertEqual(res.status_code, 200)
        self.assertFalse('_auth_user_id' in client.session)

    def test_student_must_never_access_teacher_dashboard(self):
        """6 & Security: A student must NEVER access the teacher dashboard (HTTP 403)."""
        client = Client()
        client.login(username='student_bob', password=self.student_password)

        res = client.get(reverse('accounts:teacher_dashboard'))
        self.assertEqual(res.status_code, 403)

    def test_unauthenticated_user_redirected_from_teacher_dashboard(self):
        """Security: Anonymous user redirected to login."""
        client = Client()
        res = client.get(reverse('accounts:teacher_dashboard'))
        self.assertEqual(res.status_code, 302)
        self.assertIn(reverse('accounts:login'), res.url)

    def test_teacher_dashboard_content_and_metrics(self):
        """7. Teacher dashboard shows all required metrics and clean context."""
        client = Client()
        client.login(username='teacher_jane', password=self.teacher_password)

        res = client.get(reverse('accounts:teacher_dashboard'))
        self.assertEqual(res.status_code, 200)

        # Validate required dashboard metrics
        self.assertEqual(res.context['total_classes'], 3)
        self.assertEqual(res.context['scheduled_classes'], 1)
        self.assertEqual(res.context['live_classes'], 1)
        self.assertEqual(res.context['completed_classes'], 1)
        self.assertEqual(len(res.context['recent_classes']), 3)

        # Validate UI output contains Create Live Class and metrics
        self.assertContains(res, 'Create Live Class')
        self.assertContains(res, 'Total Classes')
        self.assertContains(res, 'Scheduled Classes')
        self.assertContains(res, 'Live Classes')
        self.assertContains(res, 'Completed Classes')
        self.assertContains(res, 'Recent Classes')

    def test_teacher_profile_view_and_editing(self):
        """8 & 9. Teacher profile viewing and editing."""
        client = Client()
        client.login(username='teacher_jane', password=self.teacher_password)

        # View profile
        res = client.get(reverse('accounts:teacher_profile'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Jane')
        self.assertContains(res, 'Department of Mathematics')

        # Edit profile via POST
        edit_data = {
            'first_name': 'Janet',
            'last_name': 'Doeman',
            'email': 'janet.doeman@faculty.edu',
            'phone_number': '+1-800-555-4321',
            'bio': 'Professor of Advanced Applied Topology'
        }
        post_res = client.post(reverse('accounts:teacher_profile'), edit_data, follow=True)
        self.assertEqual(post_res.status_code, 200)

        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.first_name, 'Janet')
        self.assertEqual(self.teacher.last_name, 'Doeman')
        self.assertEqual(self.teacher.email, 'janet.doeman@faculty.edu')
        self.assertEqual(self.teacher.bio, 'Professor of Advanced Applied Topology')

    def test_teacher_change_password_flow(self):
        """10. Teacher changes password with validation and session persistence."""
        client = Client()
        client.login(username='teacher_jane', password=self.teacher_password)

        new_password = 'BrandNewSecurePassword2026!#'
        post_data = {
            'old_password': self.teacher_password,
            'new_password1': new_password,
            'new_password2': new_password,
        }
        res = client.post(reverse('accounts:teacher_change_password'), post_data, follow=True)
        self.assertEqual(res.status_code, 200)

        self.teacher.refresh_from_db()
        self.assertTrue(check_password(new_password, self.teacher.password))

        # Teacher should still be authenticated in this session
        self.assertEqual(int(client.session['_auth_user_id']), self.teacher.id)

        # Old password must no longer work
        login_old = client.login(username='teacher_jane', password=self.teacher_password)
        self.assertFalse(login_old)

        # New password must work
        login_new = client.login(username='teacher_jane', password=new_password)
        self.assertTrue(login_new)

    def test_student_cannot_access_teacher_password_change(self):
        """Security: Students cannot access teacher change password endpoint."""
        client = Client()
        client.login(username='student_bob', password=self.student_password)

        res = client.get(reverse('accounts:teacher_change_password'))
        self.assertEqual(res.status_code, 403)

    def test_csrf_protection_enabled(self):
        """Security: POST requests are protected by CSRF middleware."""
        csrf_client = Client(enforce_csrf_checks=True)
        # Attempt POST without CSRF token
        res = csrf_client.post(reverse('accounts:teacher_login'), {
            'username': 'teacher_jane',
            'password': self.teacher_password,
        })
        self.assertEqual(res.status_code, 403)


class StudentAuthenticationAndGoogleTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.student = User.objects.create_user(
            username='emma_student',
            email='emma.watson@gmail.com',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Emma',
            last_name='Watson',
            google_id='goog_emma_101',
        )

    def test_student_login_page_renders(self):
        """Student login page renders with TeachLive branding and Google login button."""
        res = self.client.get(reverse('student:login'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Student Login')
        self.assertContains(res, 'Continue with Google')
        self.assertContains(res, 'TeachLive')
        self.assertContains(res, 'Terms')

    def test_unauthenticated_student_redirect_preserves_next_url(self):
        """Unauthenticated student visiting live class link gets redirected to login with next parameter."""
        target_url = '/live/TL-X7K92P/'
        res = self.client.get(target_url)
        self.assertEqual(res.status_code, 302)
        self.assertIn('/student/login/', res.url)
        self.assertIn('next=/live/TL-X7K92P/', res.url)

    def test_new_student_creation_via_google_oauth(self):
        """Google OAuth creates new student account with role STUDENT and unusable password."""
        from accounts.oauth import get_or_create_student_from_google
        google_payload = {
            'sub': 'google_sub_99998888',
            'email': 'new.user@gmail.com',
            'name': 'Daniel Radcliffe',
            'given_name': 'Daniel',
            'family_name': 'Radcliffe',
            'picture': 'https://example.com/avatar.jpg'
        }
        user, created = get_or_create_student_from_google(google_payload)
        self.assertTrue(created)
        self.assertEqual(user.email, 'new.user@gmail.com')
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertEqual(user.google_id, 'google_sub_99998888')
        self.assertEqual(user.google_picture_url, 'https://example.com/avatar.jpg')
        self.assertEqual(user.first_name, 'Daniel')
        self.assertFalse(user.has_usable_password())

    def test_existing_student_login_via_google_oauth(self):
        """Google OAuth logs into existing student account without duplicating it."""
        from accounts.oauth import get_or_create_student_from_google
        google_payload = {
            'sub': 'goog_emma_101',
            'email': 'emma.watson@gmail.com',
            'name': 'Emma Watson',
            'given_name': 'Emma',
            'family_name': 'Watson',
            'picture': 'https://example.com/new_emma.jpg'
        }
        user, created = get_or_create_student_from_google(google_payload)
        self.assertFalse(created)
        self.assertEqual(user.id, self.student.id)
        self.assertEqual(user.google_picture_url, 'https://example.com/new_emma.jpg')

    def test_duplicate_google_email_handling(self):
        """Duplicate Google email matches existing account instead of creating duplicate."""
        from accounts.oauth import get_or_create_student_from_google
        # User already exists with email 'emma.watson@gmail.com'
        initial_count = User.objects.filter(email__iexact='emma.watson@gmail.com').count()
        self.assertEqual(initial_count, 1)

        payload = {
            'sub': 'different_google_sub_id',
            'email': 'emma.watson@gmail.com',
            'name': 'Emma W.',
        }
        user, created = get_or_create_student_from_google(payload)
        self.assertFalse(created)
        self.assertEqual(user.id, self.student.id)
        final_count = User.objects.filter(email__iexact='emma.watson@gmail.com').count()
        self.assertEqual(final_count, 1)

    def test_student_dashboard_access_for_authenticated_student(self):
        """Authenticated student accesses /student/dashboard/ successfully."""
        self.client.force_login(self.student)
        res = self.client.get(reverse('student:dashboard'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Student Dashboard')
        self.assertContains(res, 'Emma Watson')
        self.assertContains(res, 'Join Live Class')

    def test_student_cannot_access_teacher_dashboard(self):
        """Security: Student attempting to access teacher dashboard receives HTTP 403."""
        self.client.force_login(self.student)
        res = self.client.get(reverse('accounts:teacher_dashboard'))
        self.assertEqual(res.status_code, 403)

    def test_student_cannot_access_django_admin(self):
        """Security: Student cannot access Django admin."""
        self.client.force_login(self.student)
        res = self.client.get('/admin/')
        # Non-staff users get redirected to admin login or receive 302/403
        self.assertIn(res.status_code, (302, 403))

    def test_student_tab_shows_google_button_above_form(self):
        """Student login tab displays 'Continue with Google' button clearly above username/password form."""
        res = self.client.get(reverse('accounts:student_login'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Student Login')
        self.assertContains(res, 'Continue with Google')
        # Check that Google button appears before username field in HTML content
        google_idx = res.content.find(b'Continue with Google')
        username_idx = res.content.find(b'Username or Email')
        self.assertNotEqual(google_idx, -1)
        self.assertNotEqual(username_idx, -1)
        self.assertLess(google_idx, username_idx)

    def test_teacher_login_does_not_show_google_button(self):
        """Teacher login page does NOT show Google Login button."""
        res = self.client.get(reverse('accounts:teacher_login'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Teacher Portal Login')
        # Google container must be hidden
        self.assertContains(res, 'id="student-google-container" class="d-none mb-3"')

    def test_admin_login_does_not_show_google_button(self):
        """Admin / All login page does NOT show Google Login button."""
        res = self.client.get(reverse('accounts:login'))
        self.assertEqual(res.status_code, 200)
        # Google container must be hidden
        self.assertContains(res, 'id="student-google-container" class="d-none mb-3"')

    def test_account_conflict_error_handling_for_teacher_email(self):
        """Google login rejects email belonging to an existing teacher with AccountConflictError."""
        from accounts.oauth import get_or_create_student_from_google, AccountConflictError
        # Create teacher
        teacher = User.objects.create_user(
            username='prof_smith',
            email='smith@faculty.edu',
            password='Password123!',
            role=User.Role.TEACHER
        )
        payload = {
            'sub': 'google_teacher_sub',
            'email': 'smith@faculty.edu',
            'name': 'Professor Smith'
        }
        with self.assertRaises(AccountConflictError):
            get_or_create_student_from_google(payload)

    def test_google_oauth_cancelled_error_handling(self):
        """Callback handles Google sign-in cancellation with clean user warning and redirect."""
        res = self.client.get(reverse('student:google_callback') + '?error=access_denied', follow=True)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Google sign-in was cancelled')

    def test_google_oauth_redirect_preserves_live_class_url(self):
        """Google OAuth simulation preserves next URL and redirects to the requested live-class."""
        post_data = {
            'next': '/live/TL-MATH-101/',
            'email': 'test.redirect@gmail.com',
            'name': 'Redirect Student',
            'google_id': 'goog_redirect_101',
        }
        res = self.client.post(reverse('student:google_login'), post_data, follow=False)
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, '/live/TL-MATH-101/')
