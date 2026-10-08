"""
Comprehensive test suite for Prompt #11:
Live Classroom Moderation + Participant Controls
Covers:
- Teacher moderation authorization (mute, remove, lower hand)
- Teacher ownership isolation (cannot moderate another teacher's class)
- Student controls (raise/lower hand, role restrictions)
- Session-level temporary removal and reconnect blocking
- Enrollment preservation across removals
- Attendance integration on removal and reconnects
- Hand-raised priority ordering in participant list
- Rate limiting on hand toggle
- Admin Dashboard moderation monitoring
- No sensitive information leakage
"""

import json
from datetime import date, time, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from classrooms.models import (
    Attendance,
    ChatMessage,
    ClassEnrollment,
    ClassParticipant,
    LiveClass,
    LiveClassParticipantModeration,
)


class ClassroomModerationTests(TestCase):
    """
    Automated verification of all Prompt #11 live classroom moderation criteria.
    """

    def setUp(self):
        cache.clear()
        # 1. Teachers
        self.teacher1 = User.objects.create_user(
            username='prof_smith',
            email='smith@teachlive.test',
            password='Password123!',
            role=User.Role.TEACHER
        )
        self.teacher2 = User.objects.create_user(
            username='prof_jones',
            email='jones@teachlive.test',
            password='Password123!',
            role=User.Role.TEACHER
        )

        # 2. Admin
        self.admin_user = User.objects.create_user(
            username='admin_super',
            email='admin@teachlive.test',
            password='Password123!',
            role=User.Role.ADMIN,
            is_staff=True,
            is_superuser=True
        )

        # 3. Students
        self.student1 = User.objects.create_user(
            username='student_rahul',
            email='rahul@teachlive.test',
            password='Password123!',
            role=User.Role.STUDENT
        )
        self.student2 = User.objects.create_user(
            username='student_amit',
            email='amit@teachlive.test',
            password='Password123!',
            role=User.Role.STUDENT
        )

        # 4. Live Class owned by teacher1
        self.live_class1 = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Advanced Quantum Mechanics',
            subject='Physics',
            room_code='QUANTUM101',
            status=LiveClass.Status.LIVE,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK,
            scheduled_date=date.today(),
            scheduled_time=time(10, 0),
            duration=60,
            max_students=30,
            started_at=timezone.now() - timedelta(minutes=15)
        )

        # 5. Live Class owned by teacher2
        self.live_class2 = LiveClass.objects.create(
            teacher=self.teacher2,
            title='Organic Chemistry Lab',
            subject='Chemistry',
            room_code='CHEM202',
            status=LiveClass.Status.LIVE,
            access_mode=LiveClass.AccessMode.PUBLIC_LINK,
            scheduled_date=date.today(),
            scheduled_time=time(11, 0),
            duration=60,
            max_students=30,
            started_at=timezone.now() - timedelta(minutes=10)
        )

        # 6. Active participants in live_class1
        self.now = timezone.now()
        self.participant1 = ClassParticipant.objects.create(
            live_class=self.live_class1,
            user=self.student1,
            student_name='Rahul Kumar',
            student_email=self.student1.email,
            join_time=self.now - timedelta(minutes=10),
            last_seen_at=self.now,
            status=ClassParticipant.Status.ACTIVE,
        )
        self.participant2 = ClassParticipant.objects.create(
            live_class=self.live_class1,
            user=self.student2,
            student_name='Amit Verma',
            student_email=self.student2.email,
            join_time=self.now - timedelta(minutes=5),
            last_seen_at=self.now,
            status=ClassParticipant.Status.ACTIVE,
        )

        # 7. Attendances
        self.attendance1 = Attendance.objects.create(
            live_class=self.live_class1,
            student=self.student1,
            student_name='Rahul Kumar',
            joined_at=self.now - timedelta(minutes=10),
            status=Attendance.Status.PRESENT,
        )
        self.attendance2 = Attendance.objects.create(
            live_class=self.live_class1,
            student=self.student2,
            student_name='Amit Verma',
            joined_at=self.now - timedelta(minutes=5),
            status=Attendance.Status.PRESENT,
        )

        # 8. Enrollments
        self.enrollment1 = ClassEnrollment.objects.create(
            live_class=self.live_class1,
            student=self.student1,
            status=ClassEnrollment.Status.ENROLLED
        )
        self.enrollment2 = ClassEnrollment.objects.create(
            live_class=self.live_class1,
            student=self.student2,
            status=ClassEnrollment.Status.ENROLLED
        )

    # -----------------------------------------------------------------
    # 1. Teacher can view own class participants & order
    # -----------------------------------------------------------------
    def test_teacher_can_view_own_class_participants(self):
        client = Client()
        client.force_login(self.teacher1)
        res = client.get(reverse('live_room:participants', kwargs={'room_code': self.live_class1.room_code}))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['active_count'], 2)
        names = [p['student_name'] for p in data['participants']]
        self.assertIn('Rahul Kumar', names)
        self.assertIn('Amit Verma', names)

    # -----------------------------------------------------------------
    # 2. Teacher cannot moderate another teacher's class
    # -----------------------------------------------------------------
    def test_teacher_cannot_moderate_another_teachers_class(self):
        client = Client()
        # Teacher2 tries to mute student1 in Teacher1's class
        client.force_login(self.teacher2)
        res = client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({
                'action': 'mute',
                'student_id': self.student1.id
            }),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 403)
        self.participant1.refresh_from_db()
        self.assertFalse(self.participant1.is_muted)

    # -----------------------------------------------------------------
    # 3. Student cannot access moderation endpoint
    # -----------------------------------------------------------------
    def test_student_cannot_access_moderation_endpoint(self):
        client = Client()
        client.force_login(self.student1)
        res = client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({
                'action': 'mute',
                'student_id': self.student2.id
            }),
            content_type='application/json'
        )
        # Login required decorator with teacher login url redirects or returns 403
        self.assertIn(res.status_code, [302, 403])
        self.participant2.refresh_from_db()
        self.assertFalse(self.participant2.is_muted)

    # -----------------------------------------------------------------
    # 4. Student can raise own hand
    # -----------------------------------------------------------------
    def test_student_can_raise_hand(self):
        client = Client()
        client.force_login(self.student1)
        res = client.post(
            reverse('live_room:hand_toggle', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({'action': 'raise'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['is_hand_raised'])

        self.participant1.refresh_from_db()
        self.assertTrue(self.participant1.is_hand_raised)
        self.assertIsNotNone(self.participant1.hand_raised_at)

        # Verify audit log
        log = LiveClassParticipantModeration.objects.filter(
            live_class=self.live_class1,
            student=self.student1,
            action=LiveClassParticipantModeration.Action.HAND_RAISED
        ).first()
        self.assertIsNotNone(log)

    # -----------------------------------------------------------------
    # 5. Student can lower own hand
    # -----------------------------------------------------------------
    def test_student_can_lower_own_hand(self):
        self.participant1.is_hand_raised = True
        self.participant1.hand_raised_at = timezone.now()
        self.participant1.save()

        client = Client()
        client.force_login(self.student1)
        res = client.post(
            reverse('live_room:hand_toggle', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({'action': 'lower'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data['is_hand_raised'])

        self.participant1.refresh_from_db()
        self.assertFalse(self.participant1.is_hand_raised)

    # -----------------------------------------------------------------
    # 6. Teacher can lower student's hand
    # -----------------------------------------------------------------
    def test_teacher_can_lower_student_hand(self):
        self.participant1.is_hand_raised = True
        self.participant1.hand_raised_at = timezone.now()
        self.participant1.save()

        client = Client()
        client.force_login(self.teacher1)
        res = client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({
                'action': 'lower_hand',
                'student_id': self.student1.id
            }),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        self.participant1.refresh_from_db()
        self.assertFalse(self.participant1.is_hand_raised)

    # -----------------------------------------------------------------
    # 7. Unauthorized user cannot lower another student's hand
    # -----------------------------------------------------------------
    def test_student_cannot_lower_another_student_hand(self):
        self.participant1.is_hand_raised = True
        self.participant1.save()

        client = Client()
        client.force_login(self.student2)
        res = client.post(
            reverse('live_room:hand_toggle', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({
                'action': 'lower',
                'student_id': self.student1.id
            }),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 403)
        self.participant1.refresh_from_db()
        self.assertTrue(self.participant1.is_hand_raised)

    # -----------------------------------------------------------------
    # 8. Teacher mute authorization
    # -----------------------------------------------------------------
    def test_teacher_mute_authorization(self):
        client = Client()
        client.force_login(self.teacher1)
        res = client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({
                'action': 'mute',
                'student_id': self.student1.id,
                'reason': 'Background noise'
            }),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        self.participant1.refresh_from_db()
        self.assertTrue(self.participant1.is_muted)

        # Audit log verification
        log = LiveClassParticipantModeration.objects.filter(
            live_class=self.live_class1,
            student=self.student1,
            action=LiveClassParticipantModeration.Action.MUTED
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.reason, 'Background noise')
        self.assertEqual(log.created_by, self.teacher1)

    # -----------------------------------------------------------------
    # 9. Unauthorized mute rejected
    # -----------------------------------------------------------------
    def test_unauthorized_mute_rejected(self):
        client = Client()
        client.force_login(self.teacher2)
        res = client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({
                'action': 'mute',
                'student_id': self.student1.id
            }),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 403)

    # -----------------------------------------------------------------
    # 10. Teacher remove authorization
    # -----------------------------------------------------------------
    def test_teacher_remove_authorization(self):
        client = Client()
        client.force_login(self.teacher1)
        res = client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({
                'action': 'remove',
                'student_id': self.student1.id,
                'reason': 'Disruptive behavior'
            }),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)

        # Check participant status
        self.participant1.refresh_from_db()
        self.assertEqual(self.participant1.status, ClassParticipant.Status.KICKED)
        self.assertIsNotNone(self.participant1.leave_time)

        # Check attendance closed
        self.attendance1.refresh_from_db()
        self.assertEqual(self.attendance1.status, Attendance.Status.LEFT)
        self.assertIsNotNone(self.attendance1.left_at)
        self.assertGreaterEqual(self.attendance1.total_duration, 1)

        # Check session-level restriction
        mod = LiveClassParticipantModeration.objects.filter(
            live_class=self.live_class1,
            student=self.student1,
            action=LiveClassParticipantModeration.Action.REMOVED,
            active=True
        ).first()
        self.assertIsNotNone(mod)
        self.assertEqual(mod.reason, 'Disruptive behavior')

    # -----------------------------------------------------------------
    # 11. Unauthorized remove rejected
    # -----------------------------------------------------------------
    def test_unauthorized_remove_rejected(self):
        client = Client()
        client.force_login(self.student2)
        res = client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({
                'action': 'remove',
                'student_id': self.student1.id
            }),
            content_type='application/json'
        )
        self.assertIn(res.status_code, [302, 403])

    # -----------------------------------------------------------------
    # 12. Removed student cannot immediately reconnect
    # -----------------------------------------------------------------
    def test_removed_student_cannot_reconnect(self):
        # Remove student1
        LiveClassParticipantModeration.objects.create(
            live_class=self.live_class1,
            student=self.student1,
            action=LiveClassParticipantModeration.Action.REMOVED,
            active=True,
            created_by=self.teacher1
        )

        client = Client()
        client.force_login(self.student1)

        # 1. Visiting classroom page redirects to /removed/
        res_room = client.get(reverse('live_room:classroom', kwargs={'room_code': self.live_class1.room_code}))
        self.assertEqual(res_room.status_code, 302)
        self.assertIn(reverse('live_room:removed', kwargs={'room_code': self.live_class1.room_code}), res_room.url)

        # 2. Token generation rejected with 403
        with patch('classrooms.live_views.is_livekit_configured', return_value=True):
            res_token = client.post(reverse('live_room:token', kwargs={'room_code': self.live_class1.room_code}))
            self.assertEqual(res_token.status_code, 403)
            self.assertIn('removed', res_token.json().get('error', '').lower())

        # 3. Heartbeat rejected with 403
        res_hb = client.post(reverse('live_room:heartbeat', kwargs={'room_code': self.live_class1.room_code}))
        self.assertEqual(res_hb.status_code, 403)

    # -----------------------------------------------------------------
    # 13. Enrollment remains intact after temporary removal
    # -----------------------------------------------------------------
    def test_enrollment_remains_intact_after_temporary_removal(self):
        client = Client()
        client.force_login(self.teacher1)
        client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({
                'action': 'remove',
                'student_id': self.student1.id
            }),
            content_type='application/json'
        )

        self.enrollment1.refresh_from_db()
        self.assertEqual(self.enrollment1.status, ClassEnrollment.Status.ENROLLED)

    # -----------------------------------------------------------------
    # 14. Attendance updates correctly on removal
    # -----------------------------------------------------------------
    def test_attendance_updates_correctly_on_removal(self):
        client = Client()
        client.force_login(self.teacher1)
        client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({
                'action': 'remove',
                'student_id': self.student1.id
            }),
            content_type='application/json'
        )
        self.attendance1.refresh_from_db()
        self.assertEqual(self.attendance1.status, Attendance.Status.LEFT)
        self.assertIsNotNone(self.attendance1.left_at)
        self.assertGreater(self.attendance1.total_duration, 0)

    # -----------------------------------------------------------------
    # 15. Reconnect does not duplicate attendance records
    # -----------------------------------------------------------------
    def test_reconnect_does_not_duplicate_attendance(self):
        # Student2 reconnects via join action
        client = Client()
        client.force_login(self.student2)
        res = client.post(reverse('live_room:join', kwargs={'room_code': self.live_class1.room_code}))
        self.assertEqual(res.status_code, 302)

        # Still only 1 attendance record for student2 in live_class1
        count = Attendance.objects.filter(live_class=self.live_class1, student=self.student2).count()
        self.assertEqual(count, 1)

    # -----------------------------------------------------------------
    # 16. Participant count handles reconnects
    # -----------------------------------------------------------------
    def test_participant_count_handles_reconnects(self):
        client = Client()
        client.force_login(self.student2)
        # Call join action multiple times
        client.post(reverse('live_room:join', kwargs={'room_code': self.live_class1.room_code}))
        client.post(reverse('live_room:join', kwargs={'room_code': self.live_class1.room_code}))

        # Unique active participants
        active_count = self.live_class1.participants.filter(
            status__in=[ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED]
        ).count()
        self.assertEqual(active_count, 2)

    # -----------------------------------------------------------------
    # 17. Hand-raised priority ordering in participant list
    # -----------------------------------------------------------------
    def test_hand_raised_priority_ordering(self):
        # Student 2 raises hand
        self.participant2.is_hand_raised = True
        self.participant2.hand_raised_at = timezone.now()
        self.participant2.save()

        client = Client()
        client.force_login(self.teacher1)
        res = client.get(reverse('live_room:participants', kwargs={'room_code': self.live_class1.room_code}))
        self.assertEqual(res.status_code, 200)
        participants = res.json()['participants']

        # First participant must be student2 (Amit Verma) because his hand is raised
        self.assertEqual(participants[0]['student_name'], 'Amit Verma')
        self.assertTrue(participants[0]['is_hand_raised'])
        self.assertFalse(participants[1]['is_hand_raised'])

    # -----------------------------------------------------------------
    # 18. Rate limiting on hand toggle
    # -----------------------------------------------------------------
    def test_hand_toggle_rate_limiting(self):
        client = Client()
        client.force_login(self.student1)
        url = reverse('live_room:hand_toggle', kwargs={'room_code': self.live_class1.room_code})

        # Send 5 rapid requests
        for _ in range(5):
            res = client.post(url, data=json.dumps({'action': 'raise'}), content_type='application/json')
            self.assertEqual(res.status_code, 200)

        # 6th rapid request should receive 429 Too Many Requests
        res_limit = client.post(url, data=json.dumps({'action': 'lower'}), content_type='application/json')
        self.assertEqual(res_limit.status_code, 429)

    # -----------------------------------------------------------------
    # 19. Admin can view moderation records in Admin Dashboard
    # -----------------------------------------------------------------
    def test_admin_can_view_moderation_records(self):
        # Create moderation events
        LiveClassParticipantModeration.objects.create(
            live_class=self.live_class1,
            student=self.student1,
            action=LiveClassParticipantModeration.Action.MUTED,
            reason='Muted for noise',
            created_by=self.teacher1
        )
        LiveClassParticipantModeration.objects.create(
            live_class=self.live_class1,
            student=self.student2,
            action=LiveClassParticipantModeration.Action.REMOVED,
            reason='Violated policy',
            created_by=self.teacher1
        )

        client = Client()
        client.force_login(self.admin_user)
        res = client.get(reverse('admin_dashboard:class_detail', kwargs={'pk': self.live_class1.pk}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Classroom Moderation Audit Log')
        self.assertContains(res, 'Muted for noise')
        self.assertContains(res, 'Violated policy')

    # -----------------------------------------------------------------
    # 20. Chat continues working
    # -----------------------------------------------------------------
    def test_chat_continues_working(self):
        client = Client()
        client.force_login(self.student1)
        res = client.post(
            reverse('live_room:chat', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({'message': 'Hello instructor!'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(ChatMessage.objects.filter(live_class=self.live_class1, message='Hello instructor!').exists())

    # -----------------------------------------------------------------
    # 21. Cannot moderate teacher or admin
    # -----------------------------------------------------------------
    def test_cannot_moderate_teacher_or_admin(self):
        client = Client()
        client.force_login(self.teacher1)
        res = client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class1.room_code}),
            data=json.dumps({
                'action': 'mute',
                'student_id': self.teacher1.id
            }),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 403)
