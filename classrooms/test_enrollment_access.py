"""
Comprehensive test suite for Prompt #10:
Secure Class Enrollment, Access Control Modes, LiveKit Token Authorization,
Capacity Handling, Access Requests, In-App Notifications, Transactional Emails,
and Teacher/Admin Ownership Isolation.
"""

import json
from datetime import date, time, timedelta
from unittest.mock import patch

from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AdminAuditLog, User
from classrooms.models import (
    ClassAccessRequest,
    ClassEnrollment,
    ClassParticipant,
    LiveClass,
)
from notifications.models import Notification


class ClassAccessAndEnrollmentTests(TestCase):
    """
    Validates all Prompt #10 test criteria:
    - Enrollment creation & uniqueness
    - Public link, Enrollment-only, and Password-protected modes
    - Server-side join verification & token gating
    - Capacity limits & reconnection handling
    - Teacher and Admin permissions & audit logging
    - In-app notifications & email dispatch
    """

    def setUp(self):
        # 1. Teachers
        self.teacher1 = User.objects.create_user(
            username='teacher_alice',
            email='alice@teachlive.test',
            password='Password123!',
            role=User.Role.TEACHER
        )
        self.teacher2 = User.objects.create_user(
            username='teacher_bob',
            email='bob@teachlive.test',
            password='Password123!',
            role=User.Role.TEACHER
        )

        # 2. Students
        self.student1 = User.objects.create_user(
            username='student_carol',
            email='carol@teachlive.test',
            password='Password123!',
            role=User.Role.STUDENT
        )
        self.student2 = User.objects.create_user(
            username='student_dave',
            email='dave@teachlive.test',
            password='Password123!',
            role=User.Role.STUDENT
        )
        self.inactive_student = User.objects.create_user(
            username='student_eve',
            email='eve@teachlive.test',
            password='Password123!',
            role=User.Role.STUDENT,
            is_active=False
        )

        # 3. Admin
        self.admin_user = User.objects.create_superuser(
            username='admin_boss',
            email='admin@teachlive.test',
            password='AdminPassword123!'
        )

        # 4. Classes
        self.public_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Public Python Seminar',
            subject='Computer Science',
            room_code='PUB-PY-001',
            scheduled_date=date.today(),
            scheduled_time=time(14, 0),
            status=LiveClass.Status.LIVE,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK,
            max_students=5
        )

        self.enrollment_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Private Advanced Algorithms',
            subject='Computer Science',
            room_code='ALGO-PRIV-001',
            scheduled_date=date.today(),
            scheduled_time=time(15, 0),
            status=LiveClass.Status.LIVE,
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY,
            max_students=2
        )

        self.password_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Secret Cryptography Workshop',
            subject='Cybersecurity',
            room_code='CRYPTO-SEC-001',
            scheduled_date=date.today(),
            scheduled_time=time(16, 0),
            status=LiveClass.Status.LIVE,
            access_mode=LiveClass.AccessMode.PASSWORD_PROTECTED,
            room_password_hash=make_password('SuperSecretPass123'),
            max_students=10
        )

        self.cancelled_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Cancelled Physics Lab',
            subject='Physics',
            room_code='PHYS-CANC-001',
            scheduled_date=date.today(),
            scheduled_time=time(17, 0),
            status=LiveClass.Status.CANCELLED,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK
        )

        # HTTP Clients
        self.client_alice = Client()
        self.client_alice.force_login(self.teacher1)

        self.client_bob = Client()
        self.client_bob.force_login(self.teacher2)

        self.client_carol = Client()
        self.client_carol.force_login(self.student1)

        self.client_dave = Client()
        self.client_dave.force_login(self.student2)

        self.client_admin = Client()
        self.client_admin.force_login(self.admin_user)

    # -------------------------------------------------------------
    # 1. Enrollment Creation & Constraints
    # -------------------------------------------------------------
    def test_01_enrollment_creation(self):
        """Test enrollment can be created with valid fields and relationships."""
        enrollment = ClassEnrollment.objects.create(
            live_class=self.enrollment_class,
            student=self.student1,
            status=ClassEnrollment.Status.ENROLLED
        )
        self.assertEqual(enrollment.status, ClassEnrollment.Status.ENROLLED)
        self.assertIsNotNone(enrollment.enrolled_at)
        self.assertTrue(enrollment.is_active)

    def test_02_duplicate_enrollment_prevention(self):
        """Test that database constraint rejects duplicate enrollments for the same student and class."""
        ClassEnrollment.objects.create(
            live_class=self.enrollment_class,
            student=self.student1,
            status=ClassEnrollment.Status.ENROLLED
        )
        with self.assertRaises(IntegrityError):
            ClassEnrollment.objects.create(
                live_class=self.enrollment_class,
                student=self.student1,
                status=ClassEnrollment.Status.INVITED
            )

    # -------------------------------------------------------------
    # 2. Student Dashboard Privacy & Visibility
    # -------------------------------------------------------------
    def test_03_student_can_see_own_enrolled_class(self):
        """Student sees enrolled classes and public classes in their class list."""
        ClassEnrollment.objects.create(
            live_class=self.enrollment_class,
            student=self.student1,
            status=ClassEnrollment.Status.ENROLLED
        )
        resp = self.client_carol.get(reverse('student:classes'))
        self.assertEqual(resp.status_code, 200)
        classes_in_context = resp.context['classes']
        class_ids = [c.id for c in classes_in_context]
        self.assertIn(self.enrollment_class.id, class_ids)
        self.assertIn(self.public_class.id, class_ids)

    def test_04_student_cannot_see_unauthorized_private_class(self):
        """Student does NOT see an enrollment-only class they are not enrolled in."""
        resp = self.client_dave.get(reverse('student:classes'))
        self.assertEqual(resp.status_code, 200)
        classes_in_context = resp.context['classes']
        class_ids = [c.id for c in classes_in_context]
        self.assertNotIn(self.enrollment_class.id, class_ids)

    # -------------------------------------------------------------
    # 3. Teacher Management & Ownership Isolation
    # -------------------------------------------------------------
    def test_05_teacher_can_manage_own_class_students(self):
        """Teacher can view students management page for their own class."""
        url = reverse('classrooms:teacher_class_students', kwargs={'room_code': self.enrollment_class.room_code})
        resp = self.client_alice.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Manage Students")

    def test_06_teacher_cannot_manage_another_teachers_class(self):
        """Teacher cannot access or manage another teacher's class (returns 403 Forbidden)."""
        url = reverse('classrooms:teacher_class_students', kwargs={'room_code': self.enrollment_class.room_code})
        resp = self.client_bob.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_07_admin_can_access_teacher_student_management(self):
        """Administrator can access student management for any class."""
        url = reverse('classrooms:teacher_class_students', kwargs={'room_code': self.enrollment_class.room_code})
        resp = self.client_admin.get(url)
        self.assertEqual(resp.status_code, 200)

    # -------------------------------------------------------------
    # 4. Access Modes Verification
    # -------------------------------------------------------------
    def test_08_public_link_access_allowed_for_authenticated_student(self):
        """Authenticated student can join a PUBLIC_LINK class."""
        url = reverse('live_room:classroom', kwargs={'room_code': self.public_class.room_code})
        resp = self.client_carol.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_09_enrollment_only_blocks_unenrolled_student(self):
        """Unenrolled student is redirected or blocked with an access warning."""
        url = reverse('live_room:classroom', kwargs={'room_code': self.enrollment_class.room_code})
        resp = self.client_dave.get(url)
        self.assertEqual(resp.status_code, 302)
        # Redirected to join screen which shows access required
        join_screen_url = reverse('live_room:detail', kwargs={'room_code': self.enrollment_class.room_code})
        self.assertIn(join_screen_url, resp.url)

    def test_10_enrollment_only_allows_enrolled_student(self):
        """Enrolled student can enter classroom."""
        ClassEnrollment.objects.create(
            live_class=self.enrollment_class,
            student=self.student1,
            status=ClassEnrollment.Status.ENROLLED
        )
        url = reverse('live_room:classroom', kwargs={'room_code': self.enrollment_class.room_code})
        resp = self.client_carol.get(url)
        self.assertEqual(resp.status_code, 200)

    # -------------------------------------------------------------
    # 5. Password Security
    # -------------------------------------------------------------
    def test_11_password_protected_rejects_without_password(self):
        """Accessing password-protected class without password redirects to join screen."""
        url = reverse('live_room:classroom', kwargs={'room_code': self.password_class.room_code})
        resp = self.client_carol.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_12_password_protected_rejects_wrong_password(self):
        """Join action rejects incorrect password."""
        url = reverse('live_room:join', kwargs={'room_code': self.password_class.room_code})
        resp = self.client_carol.post(url, {'password': 'WrongPassword999!'})
        self.assertEqual(resp.status_code, 302)
        # Should redirect back to detail screen
        self.assertIn(reverse('live_room:detail', kwargs={'room_code': self.password_class.room_code}), resp.url)

    def test_13_password_protected_accepts_valid_password(self):
        """Join action accepts correct password and sets session auth."""
        url = reverse('live_room:join', kwargs={'room_code': self.password_class.room_code})
        resp = self.client_carol.post(url, {'password': 'SuperSecretPass123'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('live_room:classroom', kwargs={'room_code': self.password_class.room_code}), resp.url)

    # -------------------------------------------------------------
    # 6. Inactive User & Cancelled Class Rejection
    # -------------------------------------------------------------
    def test_14_inactive_student_rejected(self):
        """Inactive student account is denied access."""
        client_eve = Client()
        client_eve.force_login(self.inactive_student)
        url = reverse('live_room:classroom', kwargs={'room_code': self.public_class.room_code})
        resp = client_eve.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_15_cancelled_class_rejected(self):
        """Cancelled class cannot be joined."""
        url = reverse('live_room:classroom', kwargs={'room_code': self.cancelled_class.room_code})
        resp = self.client_carol.get(url)
        self.assertEqual(resp.status_code, 302)

    # -------------------------------------------------------------
    # 7. Capacity Control & Reconnect Handling
    # -------------------------------------------------------------
    def test_16_capacity_limit_blocks_new_students(self):
        """When active participants == max_students, new students are blocked with capacity warning."""
        # Class max_students = 2
        ClassEnrollment.objects.create(live_class=self.enrollment_class, student=self.student1, status=ClassEnrollment.Status.ENROLLED)
        ClassEnrollment.objects.create(live_class=self.enrollment_class, student=self.student2, status=ClassEnrollment.Status.ENROLLED)

        # Simulate 2 other active students occupying all slots
        user3 = User.objects.create_user(username='extra1', role=User.Role.STUDENT)
        user4 = User.objects.create_user(username='extra2', role=User.Role.STUDENT)
        ClassParticipant.objects.create(live_class=self.enrollment_class, user=user3, status=ClassParticipant.Status.ACTIVE)
        ClassParticipant.objects.create(live_class=self.enrollment_class, user=user4, status=ClassParticipant.Status.ACTIVE)

        url = reverse('live_room:classroom', kwargs={'room_code': self.enrollment_class.room_code})
        resp = self.client_carol.get(url)
        self.assertEqual(resp.status_code, 302)
        # Should redirect to join screen with capacity error
        join_url = reverse('live_room:detail', kwargs={'room_code': self.enrollment_class.room_code})
        self.assertIn(join_url, resp.url)

    def test_17_duplicate_participant_reconnect_does_not_double_count(self):
        """Reconnecting student's existing active record does not block themselves from capacity."""
        # Class max_students = 1
        one_slot_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Single Slot Class',
            room_code='ONE-SLOT-001',
            scheduled_date=date.today(),
            scheduled_time=time(18, 0),
            status=LiveClass.Status.LIVE,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK,
            max_students=1
        )
        # Student1 already has an active participant record (e.g. from previous tab)
        ClassParticipant.objects.create(live_class=one_slot_class, user=self.student1, status=ClassParticipant.Status.ACTIVE)

        # Student1 reconnects
        url = reverse('live_room:classroom', kwargs={'room_code': one_slot_class.room_code})
        resp = self.client_carol.get(url)
        self.assertEqual(resp.status_code, 200)

    # -------------------------------------------------------------
    # 8. LiveKit Token Security
    # -------------------------------------------------------------
    @patch('classrooms.live_views.generate_livekit_access_token')
    def test_18_livekit_token_authorized_for_valid_student(self, mock_gen_token):
        """Authorized student receives 200 and a token from /live/<room_code>/token/."""
        mock_gen_token.return_value = {'token': 'mocked.jwt.token'}
        url = reverse('live_room:token', kwargs={'room_code': self.public_class.room_code})
        resp = self.client_carol.post(url, data=json.dumps({}), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get('token'), 'mocked.jwt.token')

    def test_19_livekit_token_rejected_for_unauthorized_student(self):
        """Unauthorized student cannot obtain token for enrollment-only class (returns 403)."""
        url = reverse('live_room:token', kwargs={'room_code': self.enrollment_class.room_code})
        resp = self.client_dave.post(url, data=json.dumps({}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    # -------------------------------------------------------------
    # 9. In-App Notifications & Emails
    # -------------------------------------------------------------
    @patch('notifications.email_service.EmailService.send_class_invitation_email')
    def test_20_teacher_invite_student_creates_enrollment_and_notification(self, mock_email):
        """Teacher inviting a student creates ClassEnrollment, Notification, and calls email service."""
        url = reverse('classrooms:teacher_add_student', kwargs={'room_code': self.enrollment_class.room_code})
        resp = self.client_alice.post(url, {'student_query': self.student1.email, 'action_type': 'INVITE'})
        self.assertEqual(resp.status_code, 302)

        # Check enrollment exists
        enrollment = ClassEnrollment.objects.filter(live_class=self.enrollment_class, student=self.student1).first()
        self.assertIsNotNone(enrollment)
        self.assertEqual(enrollment.status, ClassEnrollment.Status.INVITED)

        # Check notification was created
        notif = Notification.objects.filter(
            recipient=self.student1,
            related_live_class=self.enrollment_class,
            notification_type=Notification.Type.STUDENT_INVITED
        ).first()
        self.assertIsNotNone(notif)

        # Check email service was invoked
        mock_email.assert_called_once()

    # -------------------------------------------------------------
    # 10. Access Request Flow
    # -------------------------------------------------------------
    def test_21_student_can_request_access(self):
        """Student requests access to enrollment-only class; teacher gets notification."""
        url = reverse('live_room:request_access', kwargs={'room_code': self.enrollment_class.room_code})
        resp = self.client_dave.post(url, {'request_note': 'Please allow me to attend!'})
        self.assertEqual(resp.status_code, 302)

        req_obj = ClassAccessRequest.objects.filter(live_class=self.enrollment_class, student=self.student2).first()
        self.assertIsNotNone(req_obj)
        self.assertEqual(req_obj.status, ClassAccessRequest.Status.PENDING)

        # Teacher receives ACCESS_REQUESTED notification
        teacher_notif = Notification.objects.filter(
            recipient=self.teacher1,
            related_live_class=self.enrollment_class,
            notification_type=Notification.Type.ACCESS_REQUESTED
        ).first()
        self.assertIsNotNone(teacher_notif)

    def test_22_teacher_can_approve_access_request(self):
        """Teacher approves request: status becomes APPROVED, enrollment created, student notified."""
        req_obj = ClassAccessRequest.objects.create(
            live_class=self.enrollment_class,
            student=self.student2,
            status=ClassAccessRequest.Status.PENDING
        )
        url = reverse('classrooms:teacher_approve_access_request', kwargs={
            'room_code': self.enrollment_class.room_code,
            'request_id': req_obj.id
        })
        resp = self.client_alice.post(url)
        self.assertEqual(resp.status_code, 302)

        req_obj.refresh_from_db()
        self.assertEqual(req_obj.status, ClassAccessRequest.Status.APPROVED)

        enrollment = ClassEnrollment.objects.filter(live_class=self.enrollment_class, student=self.student2).first()
        self.assertIsNotNone(enrollment)
        self.assertEqual(enrollment.status, ClassEnrollment.Status.ENROLLED)

    # -------------------------------------------------------------
    # 11. Revoked Student Handling
    # -------------------------------------------------------------
    def test_23_revoking_student_blocks_further_access(self):
        """Revoking student marks enrollment REVOKED and blocks classroom access."""
        enrollment = ClassEnrollment.objects.create(
            live_class=self.enrollment_class,
            student=self.student1,
            status=ClassEnrollment.Status.ENROLLED
        )
        url = reverse('classrooms:teacher_revoke_student', kwargs={
            'room_code': self.enrollment_class.room_code,
            'enrollment_id': enrollment.id
        })
        resp = self.client_alice.post(url)
        self.assertEqual(resp.status_code, 302)

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, ClassEnrollment.Status.REVOKED)

        # Try entering classroom now
        classroom_url = reverse('live_room:classroom', kwargs={'room_code': self.enrollment_class.room_code})
        resp = self.client_carol.get(classroom_url)
        self.assertEqual(resp.status_code, 302)

    # -------------------------------------------------------------
    # 12. Admin Actions & Audit Logging
    # -------------------------------------------------------------
    def test_24_admin_can_revoke_enrollment_with_audit_log(self):
        """Admin revokes enrollment from admin dashboard and creates AdminAuditLog entry."""
        enrollment = ClassEnrollment.objects.create(
            live_class=self.enrollment_class,
            student=self.student1,
            status=ClassEnrollment.Status.ENROLLED
        )
        url = reverse('admin_dashboard:class_enrollment_revoke', kwargs={
            'pk': self.enrollment_class.pk,
            'enrollment_id': enrollment.id
        })
        resp = self.client_admin.post(url)
        self.assertEqual(resp.status_code, 302)

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, ClassEnrollment.Status.REVOKED)

        # Verify audit log
        audit = AdminAuditLog.objects.filter(
            admin=self.admin_user,
            action=AdminAuditLog.Action.ENROLLMENT_REVOKED
        ).first()
        self.assertIsNotNone(audit)
