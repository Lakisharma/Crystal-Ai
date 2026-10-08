"""
Comprehensive unit and integration tests for Prompt #15:
Advanced Teacher Student & Class Management.
"""

from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import AdminAuditLog
from classrooms.models import (
    Attendance,
    ClassAccessRequest,
    ClassEnrollment,
    ClassInviteToken,
    ClassParticipant,
    LiveClass,
    LiveClassParticipantModeration,
)
from notifications.models import Notification

User = get_user_model()


class TeacherStudentManagementTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Teachers
        self.teacher1 = User.objects.create_user(
            username='prof_alpha',
            email='alpha@teachlive.edu',
            password='password123',
            role=User.Role.TEACHER,
            first_name='Alan',
            last_name='Turing'
        )
        self.teacher2 = User.objects.create_user(
            username='prof_beta',
            email='beta@teachlive.edu',
            password='password123',
            role=User.Role.TEACHER,
            first_name='Grace',
            last_name='Hopper'
        )

        # Students
        self.student1 = User.objects.create_user(
            username='student_alice',
            email='alice@student.teachlive.edu',
            password='password123',
            role=User.Role.STUDENT,
            first_name='Alice',
            last_name='Wonder'
        )
        self.student2 = User.objects.create_user(
            username='student_bob',
            email='bob@student.teachlive.edu',
            password='password123',
            role=User.Role.STUDENT,
            first_name='Bob',
            last_name='Builder'
        )
        self.student3 = User.objects.create_user(
            username='student_charlie',
            email='charlie@student.teachlive.edu',
            password='password123',
            role=User.Role.STUDENT,
            first_name='Charlie',
            last_name='Chaplin'
        )

        # Classes for Teacher 1
        now = timezone.now()
        self.class1 = LiveClass.objects.create(
            title='Intro to Algorithms',
            subject='Computer Science',
            teacher=self.teacher1,
            room_code='ALGO-101',
            scheduled_date=now.date(),
            scheduled_time=now.time(),
            duration=60,
            status=LiveClass.Status.SCHEDULED,
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY,
            max_students=2
        )
        self.class2 = LiveClass.objects.create(
            title='Advanced Graph Theory',
            subject='Computer Science',
            teacher=self.teacher1,
            room_code='GRAPH-202',
            scheduled_date=(now + timedelta(days=2)).date(),
            scheduled_time=now.time(),
            duration=90,
            status=LiveClass.Status.SCHEDULED,
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY,
            max_students=10
        )

        # Class for Teacher 2 (Isolation test)
        self.class_t2 = LiveClass.objects.create(
            title='Compiler Design',
            subject='Computer Science',
            teacher=self.teacher2,
            room_code='COMP-303',
            scheduled_date=now.date(),
            scheduled_time=now.time(),
            duration=60,
            status=LiveClass.Status.SCHEDULED,
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY,
            max_students=5
        )

        # Enroll Alice in Teacher 1's class
        self.enr_alice = ClassEnrollment.objects.create(
            live_class=self.class1,
            student=self.student1,
            status=ClassEnrollment.Status.ENROLLED
        )
        # Invite Bob to Teacher 1's class
        self.enr_bob = ClassEnrollment.objects.create(
            live_class=self.class1,
            student=self.student2,
            status=ClassEnrollment.Status.INVITED
        )
        # Charlie is enrolled in Teacher 2's class ONLY
        self.enr_charlie = ClassEnrollment.objects.create(
            live_class=self.class_t2,
            student=self.student3,
            status=ClassEnrollment.Status.ENROLLED
        )

    def test_teacher_students_list_isolation(self):
        """Teacher 1 sees Alice and Bob, but NOT Charlie (Teacher 2's student)."""
        self.client.force_login(self.teacher1)
        response = self.client.get(reverse('classrooms:teacher_students'))
        self.assertEqual(response.status_code, 200)

        students_in_context = [item['student'] for item in response.context['student_items']]
        self.assertIn(self.student1, students_in_context)
        self.assertIn(self.student2, students_in_context)
        self.assertNotIn(self.student3, students_in_context)

        # Summary metrics
        self.assertEqual(response.context['total_students'], 2)
        self.assertEqual(response.context['active_students'], 1)
        self.assertEqual(response.context['pending_invitations'], 1)

    def test_teacher_students_search_and_filter(self):
        """Teacher can search by name/email and filter by status."""
        self.client.force_login(self.teacher1)

        # Search for 'alice'
        resp_search = self.client.get(reverse('classrooms:teacher_students') + '?q=alice')
        self.assertEqual(resp_search.status_code, 200)
        students = [item['student'] for item in resp_search.context['student_items']]
        self.assertIn(self.student1, students)
        self.assertNotIn(self.student2, students)

        # Filter by active
        resp_filter = self.client.get(reverse('classrooms:teacher_students') + '?filter=active')
        self.assertEqual(resp_filter.status_code, 200)
        active_students = [item['student'] for item in resp_filter.context['student_items']]
        self.assertIn(self.student1, active_students)
        self.assertNotIn(self.student2, active_students)

    def test_teacher_student_detail_access_control(self):
        """Teacher 1 can view Alice's details, but 403s on Charlie (Teacher 2's student)."""
        self.client.force_login(self.teacher1)

        # Alice -> 200 OK
        resp_alice = self.client.get(reverse('classrooms:teacher_student_detail', kwargs={'student_id': self.student1.id}))
        self.assertEqual(resp_alice.status_code, 200)
        self.assertEqual(resp_alice.context['student'], self.student1)
        self.assertEqual(resp_alice.context['total_classes'], 1)

        # Charlie -> 403 Forbidden
        resp_charlie = self.client.get(reverse('classrooms:teacher_student_detail', kwargs={'student_id': self.student3.id}))
        self.assertEqual(resp_charlie.status_code, 403)

    def test_teacher_add_student_by_email_and_capacity(self):
        """Adding a student via email checks capacity and creates enrollment."""
        self.client.force_login(self.teacher1)

        # class1 capacity is 2. Currently Alice (ENROLLED) and Bob (INVITED) are 2.
        # Adding Charlie to class1 should fail capacity!
        resp_cap = self.client.post(
            reverse('classrooms:teacher_add_student', kwargs={'room_code': self.class1.room_code}),
            {'student_query': self.student3.email, 'action_type': 'ENROLL'}
        )
        self.assertEqual(resp_cap.status_code, 302)
        # Charlie should not be enrolled in class1
        self.assertFalse(ClassEnrollment.objects.filter(live_class=self.class1, student=self.student3).exists())

        # Now add Charlie to class2 (capacity 10)
        resp_success = self.client.post(
            reverse('classrooms:teacher_add_student', kwargs={'room_code': self.class2.room_code}),
            {'student_query': self.student3.email, 'action_type': 'ENROLL'}
        )
        self.assertEqual(resp_success.status_code, 302)
        self.assertTrue(ClassEnrollment.objects.filter(live_class=self.class2, student=self.student3, status=ClassEnrollment.Status.ENROLLED).exists())

        # Audit log verification
        audit = AdminAuditLog.objects.filter(
            action__in=[AdminAuditLog.Action.ENROLLMENT_CREATED, AdminAuditLog.Action.STUDENT_INVITED],
            admin=self.teacher1
        ).first()
        self.assertIsNotNone(audit)

    def test_teacher_revoke_and_restore_student(self):
        """Revoking records reason and prevents live entry; restoring clears revocation."""
        self.client.force_login(self.teacher1)

        # Revoke Alice with reason
        revoke_url = reverse('classrooms:teacher_revoke_student', kwargs={
            'room_code': self.class1.room_code,
            'enrollment_id': self.enr_alice.id
        })
        resp = self.client.post(revoke_url, {'reason': 'Repeated disruptive behavior'})
        self.assertEqual(resp.status_code, 302)

        self.enr_alice.refresh_from_db()
        self.assertEqual(self.enr_alice.status, ClassEnrollment.Status.REVOKED)
        self.assertEqual(self.enr_alice.revocation_reason, 'Repeated disruptive behavior')

        # Check moderation record created
        mod = LiveClassParticipantModeration.objects.filter(
            live_class=self.class1, student=self.student1, action=LiveClassParticipantModeration.Action.REMOVED
        ).first()
        self.assertIsNotNone(mod)
        self.assertTrue(mod.active)

        # Re-invite Alice
        restore_url = reverse('classrooms:teacher_restore_student', kwargs={
            'room_code': self.class1.room_code,
            'enrollment_id': self.enr_alice.id
        })
        resp_restore = self.client.post(restore_url, {'action_type': 'INVITE'})
        self.assertEqual(resp_restore.status_code, 302)

        self.enr_alice.refresh_from_db()
        self.assertEqual(self.enr_alice.status, ClassEnrollment.Status.INVITED)
        self.assertEqual(self.enr_alice.revocation_reason, '')

        mod.refresh_from_db()
        self.assertFalse(mod.active)

    def test_approve_and_reject_access_requests(self):
        """Teacher can approve or reject pending access requests."""
        # Create request for Charlie on class2
        req = ClassAccessRequest.objects.create(
            live_class=self.class2,
            student=self.student3,
            status=ClassAccessRequest.Status.PENDING,
            request_note='Please let me join!'
        )

        self.client.force_login(self.teacher1)
        approve_url = reverse('classrooms:teacher_approve_access_request', kwargs={
            'room_code': self.class2.room_code,
            'request_id': req.id
        })
        resp = self.client.post(approve_url)
        self.assertEqual(resp.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.status, ClassAccessRequest.Status.APPROVED)

        # Charlie is now ENROLLED in class2
        enr = ClassEnrollment.objects.filter(live_class=self.class2, student=self.student3).first()
        self.assertIsNotNone(enr)
        self.assertEqual(enr.status, ClassEnrollment.Status.ENROLLED)

    def test_class_invite_token_generation_and_revocation(self):
        """Teacher can generate and revoke secure invite link."""
        self.client.force_login(self.teacher1)

        gen_url = reverse('classrooms:teacher_generate_invite_link', kwargs={'room_code': self.class2.room_code})
        resp = self.client.post(gen_url)
        self.assertEqual(resp.status_code, 302)

        token_obj = ClassInviteToken.objects.filter(live_class=self.class2, is_revoked=False).first()
        self.assertIsNotNone(token_obj)
        self.assertTrue(token_obj.is_active)
        self.assertTrue(len(token_obj.token) >= 32)

        # Revoke token
        rev_url = reverse('classrooms:teacher_revoke_invite_link', kwargs={'room_code': self.class2.room_code})
        resp_rev = self.client.post(rev_url)
        self.assertEqual(resp_rev.status_code, 302)

        token_obj.refresh_from_db()
        self.assertFalse(token_obj.is_active)

    def test_student_class_invite_link_accept_flow(self):
        """Student opens invite link and accepts, enrolling successfully."""
        # Generate token for class2
        token_obj = ClassInviteToken.create_for_class(self.class2)

        # Charlie logs in as student
        self.client.force_login(self.student3)

        # GET landing page
        invite_url = reverse('student:class_invite', kwargs={'token': token_obj.token})
        resp_get = self.client.get(invite_url)
        self.assertEqual(resp_get.status_code, 200)
        self.assertEqual(resp_get.context['live_class'], self.class2)

        # POST accept
        resp_post = self.client.post(invite_url, {'action': 'accept'})
        self.assertEqual(resp_post.status_code, 302)

        # Student is enrolled
        enr = ClassEnrollment.objects.filter(live_class=self.class2, student=self.student3).first()
        self.assertIsNotNone(enr)
        self.assertEqual(enr.status, ClassEnrollment.Status.ENROLLED)

        # Token usage tracked
        token_obj.refresh_from_db()
        self.assertEqual(token_obj.times_used, 1)

        # Audit log verified
        audit = AdminAuditLog.objects.filter(
            action=AdminAuditLog.Action.INVITATION_ACCEPTED, admin=self.student3
        ).first()
        self.assertIsNotNone(audit)

    def test_student_revoked_blocked_from_invite_token(self):
        """A student whose access was revoked cannot re-enroll via invite token."""
        # Revoke Bob from class1
        self.enr_bob.status = ClassEnrollment.Status.REVOKED
        self.enr_bob.revocation_reason = 'Disciplinary removal'
        self.enr_bob.save()

        token_obj = ClassInviteToken.create_for_class(self.class1)

        self.client.force_login(self.student2)
        invite_url = reverse('student:class_invite', kwargs={'token': token_obj.token})

        # GET shows revoked warning
        resp = self.client.get(invite_url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['is_revoked'])

        # POST accept is blocked
        resp_post = self.client.post(invite_url, {'action': 'accept'})
        self.assertEqual(resp_post.status_code, 302)

        self.enr_bob.refresh_from_db()
        self.assertEqual(self.enr_bob.status, ClassEnrollment.Status.REVOKED)

    def test_student_leave_class(self):
        """Student can leave an enrolled class, setting status to REVOKED with voluntary reason."""
        self.client.force_login(self.student1)

        leave_url = reverse('student:class_leave', kwargs={'class_id': self.class1.id})
        resp = self.client.post(leave_url, {'reason': 'Schedule conflict'})
        self.assertEqual(resp.status_code, 302)

        self.enr_alice.refresh_from_db()
        self.assertEqual(self.enr_alice.status, ClassEnrollment.Status.REVOKED)
        self.assertIn('Schedule conflict', self.enr_alice.revocation_reason)

    def test_student_accept_and_decline_direct_invite(self):
        """Student can accept or decline in-app invitation."""
        # Bob is INVITED to class1
        self.client.force_login(self.student2)

        # Accept
        accept_url = reverse('student:class_accept', kwargs={'class_id': self.class1.id})
        resp_acc = self.client.post(accept_url)
        self.assertEqual(resp_acc.status_code, 302)

        self.enr_bob.refresh_from_db()
        self.assertEqual(self.enr_bob.status, ClassEnrollment.Status.ENROLLED)

        # Reset Bob to INVITED to test decline
        self.enr_bob.status = ClassEnrollment.Status.INVITED
        self.enr_bob.save()

        # Decline
        dec_url = reverse('student:class_decline', kwargs={'class_id': self.class1.id})
        resp_dec = self.client.post(dec_url)
        self.assertEqual(resp_dec.status_code, 302)

        self.enr_bob.refresh_from_db()
        self.assertEqual(self.enr_bob.status, ClassEnrollment.Status.REVOKED)
        self.assertIn('declined', self.enr_bob.revocation_reason.lower())
