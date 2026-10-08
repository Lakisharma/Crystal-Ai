from datetime import date, time, timedelta
import json

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AdminAuditLog, User
from classrooms.models import Attendance, ChatMessage, ClassParticipant, LiveClass


class TeachLiveAdminDashboardSecurityAndFeatureTests(TestCase):
    """
    Automated test suite verifying the TeachLive Admin Dashboard:
    - Role-based security (Admin only, 403 for Teacher/Student, 302 for Anonymous)
    - Teacher and Student directory, search, detail, and activation/deactivation
    - Deactivated user login prevention
    - Live Class management, detail, and scheduled cancellation
    - Protection: LIVE class cannot be cancelled via scheduled cancellation
    - Attendance management and filtering
    - Chat reports monitoring
    - Summary reports and analytics
    - AdminAuditLog creation on administrative actions
    - Audit log protection (403 for Teacher/Student)
    - Pagination & search
    """

    def setUp(self):
        # 1. Admin user
        self.admin_user = User.objects.create_user(
            username='super_admin',
            email='admin@teachlive.com',
            password='AdminPassword123!',
            role=User.Role.ADMIN,
            first_name='Head',
            last_name='Admin'
        )

        # 2. Teacher user
        self.teacher_user = User.objects.create_user(
            username='prof_turing',
            email='turing@teachlive.com',
            password='TeacherPassword123!',
            role=User.Role.TEACHER,
            first_name='Alan',
            last_name='Turing'
        )

        # 3. Student user
        self.student_user = User.objects.create_user(
            username='ada_student',
            email='ada@teachlive.com',
            password='StudentPassword123!',
            role=User.Role.STUDENT,
            first_name='Ada',
            last_name='Lovelace'
        )

        # 4. Scheduled Live Class
        self.scheduled_class = LiveClass.objects.create(
            teacher=self.teacher_user,
            title='Intro to Cryptography',
            subject='Cybersecurity',
            room_code='TL-CRYPTO-101',
            status=LiveClass.Status.SCHEDULED,
            scheduled_date=date.today(),
            scheduled_time=time(14, 0),
            max_students=30,
        )

        # 5. Active Live Class
        self.live_class = LiveClass.objects.create(
            teacher=self.teacher_user,
            title='Realtime Operating Systems',
            subject='Computer Engineering',
            room_code='TL-RTOS-202',
            status=LiveClass.Status.LIVE,
            scheduled_date=date.today(),
            scheduled_time=time(10, 0),
            max_students=40,
        )

    # 1. Admin can access dashboard
    def test_admin_can_access_dashboard(self):
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Administrative Overview')
        self.assertContains(res, 'Verified Instructors')

    # 2. Teacher cannot access admin dashboard (403)
    def test_teacher_cannot_access_admin_dashboard(self):
        self.client.force_login(self.teacher_user)
        res = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(res.status_code, 403)

    # 3. Student cannot access admin dashboard (403)
    def test_student_cannot_access_admin_dashboard(self):
        self.client.force_login(self.student_user)
        res = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(res.status_code, 403)

    # 4. Anonymous user redirected to login
    def test_anonymous_user_redirected_to_login(self):
        anon_client = Client()
        res = anon_client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(res.status_code, 302)
        self.assertIn('/accounts/login/', res.url)

    # 5. Admin can view teachers
    def test_admin_can_view_teachers(self):
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('admin_dashboard:teachers'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Teacher Management')
        self.assertContains(res, 'prof_turing')
        self.assertContains(res, 'turing@teachlive.com')

    # 6. Admin can view students
    def test_admin_can_view_students(self):
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('admin_dashboard:students'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Student Management')
        self.assertContains(res, 'ada_student')
        self.assertContains(res, 'ada@teachlive.com')

    # 7. Admin can view classes
    def test_admin_can_view_classes(self):
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('admin_dashboard:classes'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Live Class Management')
        self.assertContains(res, 'Intro to Cryptography')
        self.assertContains(res, 'Realtime Operating Systems')

    # 8. Admin can view attendance
    def test_admin_can_view_attendance(self):
        # Create attendance record
        Attendance.objects.create(
            live_class=self.live_class,
            student=self.student_user,
            student_name='Ada Lovelace',
            status=Attendance.Status.PRESENT,
            joined_at=timezone.now(),
            total_duration=25
        )
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('admin_dashboard:attendance'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Attendance Management')
        self.assertContains(res, 'Ada Lovelace')
        self.assertContains(res, 'Realtime Operating Systems')

    # 9. Admin can view chat reports
    def test_admin_can_view_chat_reports(self):
        ChatMessage.objects.create(
            live_class=self.live_class,
            sender=self.student_user,
            sender_name='Ada Lovelace',
            sender_role=ChatMessage.SenderRole.STUDENT,
            message='Hello instructor, I have a question.'
        )
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('admin_dashboard:chat'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Classroom Chat Reports')
        self.assertContains(res, 'Hello instructor, I have a question.')

    # 10 & 11. Admin can activate and deactivate teacher
    def test_admin_can_deactivate_and_activate_teacher(self):
        self.client.force_login(self.admin_user)
        toggle_url = reverse('admin_dashboard:teacher_toggle_status', kwargs={'pk': self.teacher_user.pk})

        # Deactivate
        deact_res = self.client.post(toggle_url, follow=True)
        self.assertEqual(deact_res.status_code, 200)
        self.teacher_user.refresh_from_db()
        self.assertFalse(self.teacher_user.is_active)

        # Audit log verification
        log_deact = AdminAuditLog.objects.filter(
            action=AdminAuditLog.Action.TEACHER_DEACTIVATED,
            target_id=str(self.teacher_user.pk)
        ).first()
        self.assertIsNotNone(log_deact)
        self.assertEqual(log_deact.admin, self.admin_user)

        # Re-activate
        act_res = self.client.post(toggle_url, follow=True)
        self.assertEqual(act_res.status_code, 200)
        self.teacher_user.refresh_from_db()
        self.assertTrue(self.teacher_user.is_active)

        log_act = AdminAuditLog.objects.filter(
            action=AdminAuditLog.Action.TEACHER_ACTIVATED,
            target_id=str(self.teacher_user.pk)
        ).first()
        self.assertIsNotNone(log_act)

    # 12 & 13. Admin can activate and deactivate student
    def test_admin_can_deactivate_and_activate_student(self):
        self.client.force_login(self.admin_user)
        toggle_url = reverse('admin_dashboard:student_toggle_status', kwargs={'pk': self.student_user.pk})

        # Deactivate
        deact_res = self.client.post(toggle_url, follow=True)
        self.assertEqual(deact_res.status_code, 200)
        self.student_user.refresh_from_db()
        self.assertFalse(self.student_user.is_active)

        # Re-activate
        act_res = self.client.post(toggle_url, follow=True)
        self.assertEqual(act_res.status_code, 200)
        self.student_user.refresh_from_db()
        self.assertTrue(self.student_user.is_active)

    # 14. Deactivated teacher cannot log in
    def test_deactivated_teacher_cannot_log_in(self):
        self.teacher_user.is_active = False
        self.teacher_user.save(update_fields=['is_active'])

        login_res = self.client.post(reverse('accounts:teacher_login'), {
            'username': self.teacher_user.username,
            'password': 'TeacherPassword123!'
        })
        # Login form fails for inactive user
        self.assertEqual(login_res.status_code, 200)
        self.assertFalse(self.client.session.get('_auth_user_id'))

    # 15. Deactivated student cannot log in
    def test_deactivated_student_cannot_log_in(self):
        self.student_user.is_active = False
        self.student_user.save(update_fields=['is_active'])

        login_res = self.client.post(reverse('accounts:login'), {
            'username': self.student_user.username,
            'password': 'StudentPassword123!'
        })
        self.assertEqual(login_res.status_code, 200)
        self.assertFalse(self.client.session.get('_auth_user_id'))

    # 16. Admin can cancel scheduled class
    def test_admin_can_cancel_scheduled_class(self):
        self.client.force_login(self.admin_user)
        cancel_url = reverse('admin_dashboard:class_cancel', kwargs={'pk': self.scheduled_class.pk})

        cancel_res = self.client.post(cancel_url, follow=True)
        self.assertEqual(cancel_res.status_code, 200)

        self.scheduled_class.refresh_from_db()
        self.assertEqual(self.scheduled_class.status, LiveClass.Status.CANCELLED)

        # Audit log created
        log = AdminAuditLog.objects.filter(
            action=AdminAuditLog.Action.CLASS_CANCELLED,
            target_id=str(self.scheduled_class.pk)
        ).first()
        self.assertIsNotNone(log)

    # 17. Admin cannot cancel LIVE class using scheduled cancellation
    def test_admin_cannot_cancel_live_class_via_scheduled_cancel(self):
        self.client.force_login(self.admin_user)
        cancel_url = reverse('admin_dashboard:class_cancel', kwargs={'pk': self.live_class.pk})

        cancel_res = self.client.post(cancel_url, follow=True)
        self.assertEqual(cancel_res.status_code, 200)
        self.assertContains(cancel_res, 'Live classes cannot be cancelled from this action')

        # Status must remain LIVE
        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.status, LiveClass.Status.LIVE)

    # 18. Admin actions create audit logs
    def test_admin_actions_create_audit_logs(self):
        self.client.force_login(self.admin_user)
        # Deactivate student
        self.client.post(reverse('admin_dashboard:student_toggle_status', kwargs={'pk': self.student_user.pk}))

        log = AdminAuditLog.objects.filter(
            action=AdminAuditLog.Action.STUDENT_DEACTIVATED,
            target_id=str(self.student_user.pk)
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.admin, self.admin_user)
        self.assertIn('Ada Lovelace', log.description)

    # 19 & 20. Teacher and Student cannot view admin audit logs
    def test_teacher_and_student_cannot_view_admin_audit_logs(self):
        # Teacher
        self.client.force_login(self.teacher_user)
        res_teacher = self.client.get(reverse('admin_dashboard:audit_logs'))
        self.assertEqual(res_teacher.status_code, 403)

        # Student
        self.client.force_login(self.student_user)
        res_student = self.client.get(reverse('admin_dashboard:audit_logs'))
        self.assertEqual(res_student.status_code, 403)

    # 21. Search works across teachers, students, and classes
    def test_search_functionality(self):
        self.client.force_login(self.admin_user)

        # Teacher search
        res_t = self.client.get(reverse('admin_dashboard:teachers') + '?q=turing')
        self.assertContains(res_t, 'Alan')

        # Student search
        res_s = self.client.get(reverse('admin_dashboard:students') + '?q=lovelace')
        self.assertContains(res_s, 'Ada')

        # Class search
        res_c = self.client.get(reverse('admin_dashboard:classes') + '?q=Cryptography')
        self.assertContains(res_c, 'TL-CRYPTO-101')

    # 22. Pagination works
    def test_pagination(self):
        self.client.force_login(self.admin_user)
        # Create 30 students to test 25-per-page pagination
        for i in range(30):
            User.objects.create_user(
                username=f'page_student_{i}',
                email=f'page_student_{i}@test.com',
                password='Password123!',
                role=User.Role.STUDENT
            )

        res_p1 = self.client.get(reverse('admin_dashboard:students') + '?page=1')
        self.assertEqual(res_p1.status_code, 200)
        self.assertEqual(len(res_p1.context['students']), 25)

        res_p2 = self.client.get(reverse('admin_dashboard:students') + '?page=2')
        self.assertEqual(res_p2.status_code, 200)
        self.assertGreaterEqual(len(res_p2.context['students']), 5)

    # 23. Filters work
    def test_filters(self):
        self.client.force_login(self.admin_user)

        # Filter live classes by status=LIVE
        res_live = self.client.get(reverse('admin_dashboard:classes') + '?status=LIVE')
        self.assertContains(res_live, 'Realtime Operating Systems')
        self.assertNotIn('Intro to Cryptography', res_live.content.decode('utf-8'))

    # 24. Dashboard statistics use real data
    def test_dashboard_statistics_use_real_data(self):
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('admin_dashboard:dashboard'))
        self.assertEqual(res.status_code, 200)

        # Real DB counts
        self.assertEqual(res.context['total_teachers'], 1)
        self.assertEqual(res.context['total_students'], 1)
        self.assertEqual(res.context['total_classes'], 2)
        self.assertEqual(res.context['currently_live'], 1)
        self.assertEqual(res.context['completed_classes'], 0)
        self.assertEqual(res.context['cancelled_classes'], 0)
