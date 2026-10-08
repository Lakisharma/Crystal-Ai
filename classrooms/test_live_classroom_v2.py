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
from classrooms.scheduling import (
    can_teacher_start_class,
    can_student_join_class,
)


class AdvancedLiveClassroomV2Tests(TestCase):
    """
    Automated test suite for Live Classroom 2.0 (Prompt #14).
    Verifies teacher/student access, scheduling windows, token generation,
    reconnect attendance deduplication, active participants, chat, and moderation.
    """

    def setUp(self):
        cache.clear()
        self.now = timezone.now()

        # 1. Create Teacher
        self.teacher = User.objects.create_user(
            username='prof_albus',
            email='albus@teachlive.test',
            password='Password123!',
            role=User.Role.TEACHER,
            first_name='Albus',
            last_name='Dumbledore'
        )

        # 2. Create Students
        self.student = User.objects.create_user(
            username='harry_p',
            email='harry@teachlive.test',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Harry',
            last_name='Potter'
        )

        self.student2 = User.objects.create_user(
            username='ron_w',
            email='ron@teachlive.test',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Ron',
            last_name='Weasley'
        )

        self.unauthorized_student = User.objects.create_user(
            username='draco_m',
            email='draco@teachlive.test',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Draco',
            last_name='Malfoy'
        )

        # 3. Create scheduled live class currently LIVE
        self.live_class = LiveClass.objects.create(
            teacher=self.teacher,
            title='Defense Against the Dark Arts',
            subject='Defense',
            description='Live advanced defense training.',
            room_code='DADA2026',
            status=LiveClass.Status.LIVE,
            access_mode=LiveClass.AccessMode.ENROLLMENT_ONLY,
            scheduled_date=date.today(),
            scheduled_time=time(10, 0),
            duration=60,
            max_students=30,
            started_at=self.now - timedelta(minutes=15)
        )

        # Enroll authorized students
        ClassEnrollment.objects.create(
            live_class=self.live_class,
            student=self.student,
            status=ClassEnrollment.Status.ENROLLED
        )
        ClassEnrollment.objects.create(
            live_class=self.live_class,
            student=self.student2,
            status=ClassEnrollment.Status.ENROLLED
        )

        # Create active participant record for student
        self.participant1 = ClassParticipant.objects.create(
            live_class=self.live_class,
            user=self.student,
            student_name='Harry Potter',
            student_email=self.student.email,
            join_time=self.now - timedelta(minutes=10),
            last_seen_at=self.now,
            status=ClassParticipant.Status.ACTIVE
        )

        # Setup Clients
        self.teacher_client = Client()
        self.teacher_client.force_login(self.teacher)

        self.student_client = Client()
        self.student_client.force_login(self.student)

        self.unauth_client = Client()
        self.unauth_client.force_login(self.unauthorized_student)

    # 1. Teacher Classroom Access
    def test_teacher_classroom_access(self):
        res = self.teacher_client.get(reverse('live_room:teacher_live', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Teacher Classroom')
        self.assertContains(res, 'Defense Against the Dark Arts')

    # 2. Student Classroom Access
    def test_student_classroom_access(self):
        res = self.student_client.get(reverse('live_room:classroom', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Live Class')
        self.assertContains(res, 'Defense Against the Dark Arts')

    # 3. Unauthorized Classroom Access
    def test_unauthorized_classroom_access_blocked(self):
        res = self.unauth_client.get(reverse('live_room:classroom', kwargs={'room_code': self.live_class.room_code}))
        self.assertIn(res.status_code, [403, 302])

    # 4. Cancelled Class Blocked
    def test_cancelled_class_access_blocked(self):
        self.live_class.status = LiveClass.Status.CANCELLED
        self.live_class.save()

        res_teacher = self.teacher_client.get(reverse('live_room:teacher_live', kwargs={'room_code': self.live_class.room_code}))
        self.assertIn(res_teacher.status_code, [400, 403, 404, 302])

        res_student = self.student_client.get(reverse('live_room:classroom', kwargs={'room_code': self.live_class.room_code}))
        self.assertIn(res_student.status_code, [400, 403, 404, 302])

    # 5. Ended Class Blocked
    def test_ended_class_access_blocked(self):
        self.live_class.status = LiveClass.Status.COMPLETED
        self.live_class.save()

        token_res = self.student_client.post(
            reverse('live_room:token', kwargs={'room_code': self.live_class.room_code}),
            content_type='application/json'
        )
        self.assertEqual(token_res.status_code, 400)
        data = json.loads(token_res.content)
        self.assertIn('ended', data.get('error', '').lower())

    # 6. Scheduling Start-Window Rules
    def test_scheduling_start_window_rules(self):
        future_class = LiveClass.objects.create(
            teacher=self.teacher,
            title='Potions in Future',
            subject='Potions',
            status=LiveClass.Status.SCHEDULED,
            scheduled_date=date.today() + timedelta(days=2),
            scheduled_time=time(14, 0),
            duration=60,
            room_code='POTION99'
        )
        allowed, reason, _ = can_teacher_start_class(future_class, self.teacher)
        self.assertFalse(allowed)
        self.assertIn('cannot be started yet', reason.lower())

    # 7. Student Join-Window Rules
    def test_student_join_window_rules(self):
        future_class = LiveClass.objects.create(
            teacher=self.teacher,
            title='Charms in Future',
            subject='Charms',
            status=LiveClass.Status.SCHEDULED,
            scheduled_date=date.today() + timedelta(days=2),
            scheduled_time=time(15, 0),
            duration=60,
            room_code='CHARMS99'
        )
        allowed, reason, _ = can_student_join_class(future_class, self.student)
        self.assertFalse(allowed)

    # 8. LiveKit Token Authorization & Generation
    @patch('classrooms.live_views.generate_livekit_access_token')
    def test_livekit_token_authorization(self, mock_gen):
        mock_gen.return_value = {
            'token': 'mock.jwt.token',
            'livekit_url': 'wss://livekit.teachlive.test',
            'room_name': self.live_class.room_code,
            'role': 'teacher'
        }
        res = self.teacher_client.post(
            reverse('live_room:token', kwargs={'room_code': self.live_class.room_code}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.content)
        self.assertIn('token', data)
        self.assertIn('livekit_url', data)

    # 9. Teacher Role Permissions in Token
    @patch('classrooms.live_views.generate_livekit_access_token')
    def test_teacher_role_permissions(self, mock_gen):
        mock_gen.return_value = {
            'token': 'mock.jwt.token',
            'livekit_url': 'wss://livekit.teachlive.test',
            'room_name': self.live_class.room_code,
            'role': 'teacher'
        }
        res = self.teacher_client.post(
            reverse('live_room:token', kwargs={'room_code': self.live_class.room_code}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.content)
        self.assertEqual(data.get('role'), 'teacher')

    # 10. Student Role Permissions in Token
    @patch('classrooms.live_views.generate_livekit_access_token')
    def test_student_role_permissions(self, mock_gen):
        mock_gen.return_value = {
            'token': 'mock.jwt.token',
            'livekit_url': 'wss://livekit.teachlive.test',
            'room_name': self.live_class.room_code,
            'role': 'student'
        }
        res = self.student_client.post(
            reverse('live_room:token', kwargs={'room_code': self.live_class.room_code}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.content)
        self.assertEqual(data.get('role'), 'student')

    # 11. Participant Count & Roster Sync
    def test_participant_count_endpoint(self):
        res = self.teacher_client.get(reverse('live_room:participants', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.content)
        self.assertGreaterEqual(data.get('active_count'), 1)

    # 12. Attendance Integration (Join & Duration)
    def test_attendance_join_and_leave(self):
        # Access room creates or resumes attendance
        self.student_client.get(reverse('live_room:classroom', kwargs={'room_code': self.live_class.room_code}))
        att = Attendance.objects.filter(live_class=self.live_class, student=self.student).first()
        self.assertIsNotNone(att)

        # Leave class updates attendance
        leave_res = self.student_client.post(
            reverse('live_room:leave', kwargs={'room_code': self.live_class.room_code}),
            content_type='application/json'
        )
        self.assertEqual(leave_res.status_code, 200)
        att.refresh_from_db()
        self.assertIsNotNone(att.left_at)

    # 13. Reconnect Attendance Deduplication
    @patch('classrooms.live_views.generate_livekit_access_token')
    def test_reconnect_attendance_deduplication(self, mock_gen):
        mock_gen.return_value = {
            'token': 'mock.jwt.token',
            'livekit_url': 'wss://livekit.teachlive.test',
            'room_name': self.live_class.room_code,
            'role': 'student'
        }
        # Simulate initial join
        self.student_client.get(reverse('live_room:classroom', kwargs={'room_code': self.live_class.room_code}))
        initial_count = Attendance.objects.filter(live_class=self.live_class, student=self.student).count()

        # Simulate reconnect (requesting token again and heartbeat)
        self.student_client.post(
            reverse('live_room:token', kwargs={'room_code': self.live_class.room_code}),
            content_type='application/json'
        )
        self.student_client.post(
            reverse('live_room:heartbeat', kwargs={'room_code': self.live_class.room_code}),
            content_type='application/json'
        )

        # Count should remain the same active attendance record
        new_count = Attendance.objects.filter(live_class=self.live_class, student=self.student).count()
        self.assertEqual(initial_count, new_count)

    # 14. Moderation: Mute Participant
    def test_moderation_mute_participant(self):
        res = self.teacher_client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class.room_code}),
            data=json.dumps({'student_id': self.student.id, 'action': 'mute'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        audit = LiveClassParticipantModeration.objects.filter(
            live_class=self.live_class,
            student=self.student,
            action=LiveClassParticipantModeration.Action.MUTED
        ).first()
        self.assertIsNotNone(audit)

    # 15. Moderation: Remove Participant
    def test_moderation_remove_participant(self):
        res = self.teacher_client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class.room_code}),
            data=json.dumps({'student_id': self.student.id, 'action': 'remove', 'reason': 'Disruptive behavior'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        audit = LiveClassParticipantModeration.objects.filter(
            live_class=self.live_class,
            student=self.student,
            action=LiveClassParticipantModeration.Action.REMOVED
        ).first()
        self.assertIsNotNone(audit)

        # Removed student is blocked from room access
        student_res = self.student_client.get(reverse('live_room:classroom', kwargs={'room_code': self.live_class.room_code}))
        self.assertIn(student_res.status_code, [302, 403])

    # 16. Raise Hand
    def test_raise_hand(self):
        res = self.student_client.post(
            reverse('live_room:hand_toggle', kwargs={'room_code': self.live_class.room_code}),
            data=json.dumps({'action': 'raise'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.content)
        self.assertEqual(data.get('action'), 'HAND_RAISED')

    # 17. Lower Hand
    def test_lower_hand(self):
        # First raise hand
        self.student_client.post(
            reverse('live_room:hand_toggle', kwargs={'room_code': self.live_class.room_code}),
            data=json.dumps({'action': 'raise'}),
            content_type='application/json'
        )

        # Teacher lowers hand
        res = self.teacher_client.post(
            reverse('live_room:moderate', kwargs={'room_code': self.live_class.room_code}),
            data=json.dumps({'student_id': self.student.id, 'action': 'lower_hand'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)

    # 18. Chat Authorization
    def test_chat_authorization(self):
        # Enrolled student can post chat
        res = self.student_client.post(
            reverse('live_room:chat', kwargs={'room_code': self.live_class.room_code}),
            data=json.dumps({'message': 'Hello Professor!'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        msg = ChatMessage.objects.filter(live_class=self.live_class, sender=self.student).first()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.message, 'Hello Professor!')

        # Unenrolled student blocked from posting chat
        unauth_res = self.unauth_client.post(
            reverse('live_room:chat', kwargs={'room_code': self.live_class.room_code}),
            data=json.dumps({'message': 'Unauthorized post'}),
            content_type='application/json'
        )
        self.assertIn(unauth_res.status_code, [400, 403])

    # 19. Chat 500-Character Validation
    def test_chat_character_limit(self):
        long_message = 'A' * 501
        res = self.student_client.post(
            reverse('live_room:chat', kwargs={'room_code': self.live_class.room_code}),
            data=json.dumps({'message': long_message}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)

    # 20. End Class Action
    def test_end_class_action(self):
        res = self.teacher_client.post(
            reverse('live_room:end', kwargs={'room_code': self.live_class.room_code})
        )
        self.assertEqual(res.status_code, 302)
        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.status, LiveClass.Status.COMPLETED)
        self.assertIsNotNone(self.live_class.ended_at)

        # Classroom access should now be blocked
        student_res = self.student_client.get(reverse('live_room:classroom', kwargs={'room_code': self.live_class.room_code}))
        self.assertIn(student_res.status_code, [302, 400, 403])
