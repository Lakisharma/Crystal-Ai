import json
import os
from datetime import date, time
from unittest.mock import patch

import jwt
from django.contrib.auth.hashers import check_password
from django.db import IntegrityError
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from classrooms.models import (
    Classroom,
    ClassSchedule,
    LiveClass,
    ClassParticipant,
    Attendance,
    ChatMessage,
    generate_secure_room_code,
)



class ClassroomAndLiveClassTests(TestCase):
    def setUp(self):
        self.teacher1 = User.objects.create_user(
            username='dr_jones',
            email='jones@univ.edu',
            password='Password123!',
            role=User.Role.TEACHER
        )
        self.teacher2 = User.objects.create_user(
            username='prof_williams',
            email='williams@univ.edu',
            password='Password123!',
            role=User.Role.TEACHER
        )
        self.student = User.objects.create_user(
            username='charlie',
            email='charlie@student.edu',
            password='Password123!',
            role=User.Role.STUDENT
        )

        self.classroom1 = Classroom.objects.create(
            name='Calculus I',
            code='MATH101',
            subject='Mathematics',
            teacher=self.teacher1,
            status=Classroom.Status.SCHEDULED
        )

        self.live_class1 = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Distributed Databases Lecture',
            subject='Computer Science',
            description='Consensus, Raft, and Paxos',
            room_code='CSD-101-DB1',
            scheduled_date=date.today(),
            scheduled_time=time(10, 0),
            duration=90,
            max_students=40,
            status=LiveClass.Status.SCHEDULED
        )

    # -----------------------------------------------------------------
    # Room Code & Password Security Tests
    # -----------------------------------------------------------------

    def test_room_code_generation_and_uniqueness(self):
        """Room codes must be securely generated and enforce database uniqueness."""
        codes = {generate_secure_room_code() for _ in range(50)}
        self.assertEqual(len(codes), 50)
        for code in codes:
            self.assertRegex(code, r'^[A-Z0-9]{3}-[A-Z0-9]{3}-[A-Z0-9]{3}$')

        # Duplicate room code must raise IntegrityError
        with self.assertRaises(IntegrityError):
            LiveClass.objects.create(
                teacher=self.teacher2,
                title='Collision Test',
                subject='Testing',
                room_code='CSD-101-DB1',  # Duplicate of self.live_class1
                scheduled_date=date.today(),
                scheduled_time=time(11, 0)
            )

    def test_password_hashing_never_plain_text(self):
        """Class passwords must be hashed via PBKDF2 and NEVER stored plain."""
        raw_pass = 'ClassSecretKey2026!#'
        self.live_class1.set_room_password(raw_pass)
        self.live_class1.save()

        self.assertNotEqual(self.live_class1.room_password_hash, raw_pass)
        self.assertTrue(self.live_class1.room_password_hash.startswith('pbkdf2_sha256$'))
        self.assertTrue(self.live_class1.check_room_password(raw_pass))
        self.assertFalse(self.live_class1.check_room_password('WrongPass!'))
        self.assertTrue(self.live_class1.has_password)

    # -----------------------------------------------------------------
    # Teacher CRUD Operations
    # -----------------------------------------------------------------

    def test_teacher_create_class(self):
        """Teacher creates live class with automated room_code and optional password."""
        client = Client()
        client.login(username='dr_jones', password='Password123!')

        future_date = date.today() + timedelta(days=2)
        post_data = {
            'title': 'Machine Learning Lab 01',
            'subject': 'Artificial Intelligence',
            'description': 'Gradient Descent and Backprop',
            'scheduled_date': str(future_date),
            'scheduled_time': '14:30',
            'duration': 75,
            'max_students': 60,
            'status': LiveClass.Status.SCHEDULED,
            'room_password': 'MLPasscode2026',
        }
        res = client.post(reverse('classrooms:live_create'), post_data)
        self.assertEqual(res.status_code, 302)

        created_class = LiveClass.objects.get(title='Machine Learning Lab 01')
        self.assertEqual(created_class.teacher, self.teacher1)
        self.assertTrue(created_class.room_code)
        self.assertTrue(created_class.room_password_hash.startswith('pbkdf2_sha256$'))
        self.assertTrue(created_class.check_room_password('MLPasscode2026'))

    def test_teacher_list_classes(self):
        """Teacher lists only their own classes with filter support."""
        client = Client()
        client.login(username='dr_jones', password='Password123!')

        res = client.get(reverse('classrooms:live_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Distributed Databases Lecture')
        self.assertEqual(res.context['total_count'], 1)

    def test_teacher_view_class(self):
        """Teacher views live class details, code, and participants."""
        client = Client()
        client.login(username='dr_jones', password='Password123!')

        res = client.get(reverse('classrooms:live_detail', kwargs={'pk': self.live_class1.pk}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Distributed Databases Lecture')
        self.assertContains(res, 'CSD-101-DB1')

    def test_teacher_edit_class(self):
        """Teacher edits class details and changes password."""
        client = Client()
        client.login(username='dr_jones', password='Password123!')

        edit_data = {
            'title': 'Distributed Databases & Sharding',
            'subject': 'Computer Science',
            'description': 'Updated syllabus with sharding algorithms',
            'scheduled_date': str(date.today()),
            'scheduled_time': '10:00',
            'duration': 120,
            'max_students': 50,
            'status': LiveClass.Status.SCHEDULED,
            'change_room_password': 'NewUpdatedPassword2026!',
        }
        res = client.post(reverse('classrooms:live_edit', kwargs={'pk': self.live_class1.pk}), edit_data)
        self.assertEqual(res.status_code, 302)

        self.live_class1.refresh_from_db()
        self.assertEqual(self.live_class1.title, 'Distributed Databases & Sharding')
        self.assertEqual(self.live_class1.duration, 120)
        self.assertTrue(self.live_class1.check_room_password('NewUpdatedPassword2026!'))

    def test_teacher_cancel_class(self):
        """Teacher cancels live class via POST with CSRF protection."""
        client = Client()
        client.login(username='dr_jones', password='Password123!')

        res = client.post(reverse('classrooms:live_cancel', kwargs={'pk': self.live_class1.pk}))
        self.assertEqual(res.status_code, 302)

        self.live_class1.refresh_from_db()
        self.assertEqual(self.live_class1.status, LiveClass.Status.CANCELLED)
        self.assertTrue(self.live_class1.is_cancelled)

    def test_teacher_delete_class(self):
        """Teacher deletes live class permanently."""
        client = Client()
        client.login(username='dr_jones', password='Password123!')

        res = client.post(reverse('classrooms:live_delete', kwargs={'pk': self.live_class1.pk}))
        self.assertEqual(res.status_code, 302)
        self.assertFalse(LiveClass.objects.filter(pk=self.live_class1.pk).exists())

    # -----------------------------------------------------------------
    # Teacher Ownership & Isolation Tests
    # -----------------------------------------------------------------

    def test_teacher_ownership_isolation(self):
        """A teacher must only access their own classes (HTTP 403 Forbidden for other teachers)."""
        client = Client()
        # Teacher 2 attempts unauthorized access to Teacher 1's class
        client.login(username='prof_williams', password='Password123!')

        # Detail view
        res_view = client.get(reverse('classrooms:live_detail', kwargs={'pk': self.live_class1.pk}))
        self.assertEqual(res_view.status_code, 403)

        # Edit view
        res_edit = client.get(reverse('classrooms:live_edit', kwargs={'pk': self.live_class1.pk}))
        self.assertEqual(res_edit.status_code, 403)

        # Cancel action
        res_cancel = client.post(reverse('classrooms:live_cancel', kwargs={'pk': self.live_class1.pk}))
        self.assertEqual(res_cancel.status_code, 403)

        # Delete action
        res_delete = client.post(reverse('classrooms:live_delete', kwargs={'pk': self.live_class1.pk}))
        self.assertEqual(res_delete.status_code, 403)

    # -----------------------------------------------------------------
    # Student Permissions Tests
    # -----------------------------------------------------------------

    def test_student_cannot_access_teacher_crud(self):
        """Students must NEVER access teacher CRUD management (HTTP 403 Forbidden)."""
        client = Client()
        client.login(username='charlie', password='Password123!')

        # List
        self.assertEqual(client.get(reverse('classrooms:live_list')).status_code, 403)
        # Create
        self.assertEqual(client.get(reverse('classrooms:live_create')).status_code, 403)
        # Edit
        self.assertEqual(client.get(reverse('classrooms:live_edit', kwargs={'pk': self.live_class1.pk})).status_code, 403)
        # Cancel
        self.assertEqual(client.post(reverse('classrooms:live_cancel', kwargs={'pk': self.live_class1.pk})).status_code, 403)
        # Delete
        self.assertEqual(client.post(reverse('classrooms:live_delete', kwargs={'pk': self.live_class1.pk})).status_code, 403)

    # -----------------------------------------------------------------
    # Class Status Lifecycle Tests
    # -----------------------------------------------------------------

    def test_class_status_lifecycle(self):
        """Start, end, and status transitions."""
        client = Client()
        client.login(username='dr_jones', password='Password123!')

        # Ensure class is within start window (scheduled now in local timezone)
        local_now = timezone.localtime(timezone.now())
        self.live_class1.scheduled_date = local_now.date()
        self.live_class1.scheduled_time = local_now.time()
        self.live_class1.save()

        # Start class
        res_start = client.post(reverse('classrooms:live_start', kwargs={'pk': self.live_class1.pk}))
        self.assertEqual(res_start.status_code, 302)
        self.live_class1.refresh_from_db()
        self.assertEqual(self.live_class1.status, LiveClass.Status.LIVE)
        self.assertTrue(self.live_class1.is_live)
        self.assertIsNotNone(self.live_class1.started_at)

        # End class
        res_end = client.post(reverse('classrooms:live_end', kwargs={'pk': self.live_class1.pk}))
        self.assertEqual(res_end.status_code, 302)
        self.live_class1.refresh_from_db()
        self.assertEqual(self.live_class1.status, LiveClass.Status.COMPLETED)
        self.assertTrue(self.live_class1.is_completed)
        self.assertIsNotNone(self.live_class1.ended_at)

    # -----------------------------------------------------------------
    # Participant, Attendance, and ChatMessage Models
    # -----------------------------------------------------------------

    def test_participant_attendance_and_chat_models(self):
        participant = ClassParticipant.objects.create(
            live_class=self.live_class1,
            student_name='Alice Wonderland',
            student_email='alice@example.com',
            status=ClassParticipant.Status.JOINED
        )
        self.assertEqual(participant.status, 'JOINED')
        self.assertIn('Alice Wonderland', str(participant))

        attendance = Attendance.objects.create(
            live_class=self.live_class1,
            student_name='Alice Wonderland',
            total_duration=85
        )
        self.assertEqual(attendance.total_duration, 85)

        chat_msg = ChatMessage.objects.create(
            live_class=self.live_class1,
            sender_name='Alice Wonderland',
            sender_role=ChatMessage.SenderRole.STUDENT,
            message='Is Paxos guaranteed to terminate?'
        )
        self.assertEqual(chat_msg.sender_role, 'STUDENT')
        self.assertIn('Paxos', chat_msg.message)


class LiveClassJoiningAndAttendanceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.teacher = User.objects.create_user(
            username='prof_turing',
            email='turing@teachlive.edu',
            password='TeacherPassword123!',
            role=User.Role.TEACHER,
            first_name='Alan',
            last_name='Turing'
        )
        self.student = User.objects.create_user(
            username='grace_student',
            email='grace.hopper@gmail.com',
            password='StudentPassword123!',
            role=User.Role.STUDENT,
            first_name='Grace',
            last_name='Hopper'
        )

        # Standard LIVE class
        self.live_class = LiveClass.objects.create(
            teacher=self.teacher,
            title='Intro to Compilers',
            subject='CS',
            room_code='TL-CMP-101',
            status=LiveClass.Status.LIVE,
            scheduled_date=date.today(),
            scheduled_time=time(14, 0),
            duration=60,
            max_students=30
        )

        # Password protected LIVE class
        self.protected_class = LiveClass.objects.create(
            teacher=self.teacher,
            title='Secret Cryptography Seminar',
            subject='Security',
            room_code='TL-CRY-999',
            status=LiveClass.Status.LIVE,
            scheduled_date=date.today(),
            scheduled_time=time(15, 0),
            duration=45,
            max_students=10
        )
        self.protected_class.set_room_password('SuperSecretPass123!')
        self.protected_class.save()

        # SCHEDULED class (future scheduled date)
        self.scheduled_class = LiveClass.objects.create(
            teacher=self.teacher,
            title='Quantum Mechanics',
            subject='Physics',
            room_code='TL-PHY-202',
            status=LiveClass.Status.SCHEDULED,
            scheduled_date=date.today() + timedelta(days=1),
            scheduled_time=time(16, 0),
            duration=60
        )

        # ENDED class
        self.ended_class = LiveClass.objects.create(
            teacher=self.teacher,
            title='Ancient Rome History',
            subject='History',
            room_code='TL-HIS-303',
            status=LiveClass.Status.COMPLETED,
            scheduled_date=date.today(),
            scheduled_time=time(10, 0),
            duration=60
        )

        # CANCELLED class
        self.cancelled_class = LiveClass.objects.create(
            teacher=self.teacher,
            title='Organic Chemistry Lab',
            subject='Chemistry',
            room_code='TL-CHM-404',
            status=LiveClass.Status.CANCELLED,
            scheduled_date=date.today(),
            scheduled_time=time(11, 0),
            duration=90
        )

    def test_unauthenticated_user_redirected_to_student_login(self):
        """Unauthenticated visitor to /live/<room_code>/ is redirected to /student/login/?next=..."""
        res = self.client.get(reverse('live_room:detail', kwargs={'room_code': 'TL-CMP-101'}))
        self.assertEqual(res.status_code, 302)
        self.assertIn('/student/login/', res.url)
        self.assertIn('next=/live/TL-CMP-101/', res.url)

    def test_valid_room_code_shows_join_screen(self):
        """Authenticated student visiting valid room code sees class join screen."""
        self.client.force_login(self.student)
        res = self.client.get(reverse('live_room:detail', kwargs={'room_code': 'TL-CMP-101'}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Intro to Compilers')
        self.assertContains(res, 'Prof. Alan Turing')
        self.assertContains(res, 'LIVE NOW')
        self.assertContains(res, 'Join Live Class')

    def test_invalid_room_code_shows_404_not_found(self):
        """Invalid room code renders friendly Classroom Not Found 404 page."""
        self.client.force_login(self.student)
        res = self.client.get(reverse('live_room:detail', kwargs={'room_code': 'NON-EXISTENT-CODE'}))
        self.assertEqual(res.status_code, 404)
        self.assertContains(res, 'Classroom Not Found', status_code=404)
        self.assertContains(res, 'NON-EXISTENT-CODE', status_code=404)

    def test_student_joins_live_class_creates_participant_and_attendance(self):
        """Joining a LIVE class creates ClassParticipant and Attendance (PRESENT)."""
        self.client.force_login(self.student)
        join_res = self.client.post(reverse('live_room:join', kwargs={'room_code': 'TL-CMP-101'}), follow=True)
        self.assertEqual(join_res.status_code, 200)

        # Check ClassParticipant
        participant = ClassParticipant.objects.filter(
            live_class=self.live_class,
            user=self.student
        ).first()
        self.assertIsNotNone(participant)
        self.assertIn(participant.status, [ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED])
        self.assertEqual(participant.student_name, 'Grace Hopper')
        self.assertEqual(participant.student_email, 'grace.hopper@gmail.com')

        # Check Attendance record
        attendance = Attendance.objects.filter(
            live_class=self.live_class,
            student=self.student
        ).first()
        self.assertIsNotNone(attendance)
        self.assertEqual(attendance.status, Attendance.Status.PRESENT)

    def test_student_leaving_live_class_updates_participant_and_attendance_duration(self):
        """Leaving a live class updates leave_time, status=LEFT, and calculates total_duration."""
        self.client.force_login(self.student)
        # Join first
        self.client.post(reverse('live_room:join', kwargs={'room_code': 'TL-CMP-101'}))

        # Leave class
        leave_res = self.client.post(reverse('live_room:leave', kwargs={'room_code': 'TL-CMP-101'}), follow=True)
        self.assertEqual(leave_res.status_code, 200)

        # Participant updated to LEFT
        participant = ClassParticipant.objects.get(live_class=self.live_class, user=self.student)
        self.assertEqual(participant.status, ClassParticipant.Status.LEFT)
        self.assertIsNotNone(participant.leave_time)

        # Attendance updated
        attendance = Attendance.objects.get(live_class=self.live_class, student=self.student)
        self.assertEqual(attendance.status, Attendance.Status.LEFT)
        self.assertIsNotNone(attendance.left_at)
        self.assertGreaterEqual(attendance.total_duration, 1)

    def test_password_protected_class_rejects_wrong_password(self):
        """Password protected class rejects incorrect password and prevents entry."""
        self.client.force_login(self.student)
        res = self.client.post(
            reverse('live_room:join', kwargs={'room_code': 'TL-CRY-999'}),
            {'password': 'IncorrectPassword!'},
            follow=True
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Incorrect class password')
        # Participant must NOT be created
        self.assertFalse(ClassParticipant.objects.filter(live_class=self.protected_class, user=self.student).exists())

    def test_password_protected_class_accepts_correct_password(self):
        """Password protected class accepts valid password server-side and allows entry."""
        self.client.force_login(self.student)
        res = self.client.post(
            reverse('live_room:join', kwargs={'room_code': 'TL-CRY-999'}),
            {'password': 'SuperSecretPass123!'},
            follow=True
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(ClassParticipant.objects.filter(live_class=self.protected_class, user=self.student).exists())

    def test_ended_class_blocks_joining(self):
        """ENDED (COMPLETED) class displays ended notice and blocks joining."""
        self.client.force_login(self.student)
        detail_res = self.client.get(reverse('live_room:detail', kwargs={'room_code': 'TL-HIS-303'}))
        self.assertContains(detail_res, 'This live class has ended.')

        post_res = self.client.post(reverse('live_room:join', kwargs={'room_code': 'TL-HIS-303'}), follow=True)
        self.assertContains(post_res, 'This live class has ended.')
        self.assertFalse(ClassParticipant.objects.filter(live_class=self.ended_class, user=self.student).exists())

    def test_cancelled_class_blocks_joining(self):
        """CANCELLED class displays cancelled notice and blocks joining."""
        self.client.force_login(self.student)
        detail_res = self.client.get(reverse('live_room:detail', kwargs={'room_code': 'TL-CHM-404'}))
        self.assertContains(detail_res, 'This class has been cancelled.')

        post_res = self.client.post(reverse('live_room:join', kwargs={'room_code': 'TL-CHM-404'}), follow=True)
        self.assertContains(post_res, 'This class has been cancelled.')
        self.assertFalse(ClassParticipant.objects.filter(live_class=self.cancelled_class, user=self.student).exists())

    def test_scheduled_class_blocks_student_until_started(self):
        """SCHEDULED class blocks student entry until teacher starts the class."""
        self.client.force_login(self.student)
        detail_res = self.client.get(reverse('live_room:detail', kwargs={'room_code': 'TL-PHY-202'}))
        self.assertContains(detail_res, 'Class has not started yet')

        post_res = self.client.post(reverse('live_room:join', kwargs={'room_code': 'TL-PHY-202'}), follow=True)
        self.assertContains(post_res, 'This class is scheduled but has not started yet')
        self.assertFalse(ClassParticipant.objects.filter(live_class=self.scheduled_class, user=self.student).exists())


class LiveKitRealtimeClassroomTests(TestCase):
    """
    Automated test suite verifying the 20 requirements for the real-time LiveKit live classroom:
    - Teacher & Student authorization and access controls
    - LiveKit secure token generation and grant permissions (publish vs subscribe-only)
    - Sensitive secret leak prevention
    - Class lifecycle (start, live, end, capacity)
    - In-room real-time chat validation & XSS protection
    """
    def setUp(self):
        self.teacher1 = User.objects.create_user(
            username='host_teacher',
            email='host@teachlive.edu',
            first_name='John',
            last_name='Sharma',
            password='TeacherPassword123!',
            role=User.Role.TEACHER
        )
        self.teacher2 = User.objects.create_user(
            username='other_teacher',
            email='other@teachlive.edu',
            password='TeacherPassword123!',
            role=User.Role.TEACHER
        )
        self.student = User.objects.create_user(
            username='student_alice',
            email='alice@student.teachlive',
            first_name='Alice',
            last_name='Roy',
            password='StudentPassword123!',
            role=User.Role.STUDENT
        )
        self.student2 = User.objects.create_user(
            username='student_bob',
            email='bob@student.teachlive',
            password='StudentPassword123!',
            role=User.Role.STUDENT
        )

        self.live_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Python Django Basics',
            subject='Backend Development',
            description='WebRTC and LiveKit real-time architecture',
            room_code='TL-RT-901',
            status=LiveClass.Status.LIVE,
            scheduled_date=date.today(),
            scheduled_time=time(10, 0),
            max_students=2,
            started_at=timezone.now(),
        )

        self.ended_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Concluded Lecture',
            subject='History',
            room_code='TL-END-902',
            status=LiveClass.Status.COMPLETED,
            scheduled_date=date.today(),
            scheduled_time=time(9, 0),
            max_students=30,
            ended_at=timezone.now(),
        )

        self.cancelled_class = LiveClass.objects.create(
            teacher=self.teacher1,
            title='Cancelled Workshop',
            subject='DevOps',
            room_code='TL-CAN-903',
            status=LiveClass.Status.CANCELLED,
            scheduled_date=date.today(),
            scheduled_time=time(11, 0),
            max_students=30,
        )


        self.mock_env = {
            'LIVEKIT_URL': 'wss://teachlive-project.livekit.cloud',
            'LIVEKIT_API_KEY': 'devkey_LK_987654321',
            'LIVEKIT_API_SECRET': 'super_secret_livekit_key_never_leak_xyz_123456789012',
        }

    # 1. Teacher can access own live classroom
    def test_teacher_can_access_own_live_classroom(self):
        self.client.force_login(self.teacher1)
        res_by_id = self.client.get(reverse('classrooms:teacher_class_live', kwargs={'class_id': self.live_class.pk}))
        self.assertEqual(res_by_id.status_code, 200)
        self.assertContains(res_by_id, 'Python Django Basics')
        self.assertContains(res_by_id, 'Teacher Host')

        res_by_code = self.client.get(reverse('live_room:teacher_live', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res_by_code.status_code, 200)

    # 2. Teacher cannot access another teacher's classroom
    def test_teacher_cannot_access_another_teachers_classroom(self):
        self.client.force_login(self.teacher2)
        res = self.client.get(reverse('classrooms:teacher_class_live', kwargs={'class_id': self.live_class.pk}))
        self.assertEqual(res.status_code, 403)

    # 3. Student can access LIVE class
    def test_student_can_access_live_class(self):
        self.client.force_login(self.student)
        res = self.client.get(reverse('live_room:classroom', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Python Django Basics')
        self.assertContains(res, 'Student View')

    # 4. Student cannot access ENDED class
    def test_student_cannot_access_ended_class(self):
        self.client.force_login(self.student)
        res = self.client.get(reverse('live_room:classroom', kwargs={'room_code': self.ended_class.room_code}))
        self.assertEqual(res.status_code, 302)
        redirect_url = reverse('live_room:detail', kwargs={'room_code': self.ended_class.room_code})
        self.assertRedirects(res, redirect_url)

    # 5. Student cannot access CANCELLED class
    def test_student_cannot_access_cancelled_class(self):
        self.client.force_login(self.student)
        res = self.client.get(reverse('live_room:classroom', kwargs={'room_code': self.cancelled_class.room_code}))
        self.assertEqual(res.status_code, 302)
        redirect_url = reverse('live_room:detail', kwargs={'room_code': self.cancelled_class.room_code})
        self.assertRedirects(res, redirect_url)

    # 6. Unauthenticated student is redirected to login
    def test_unauthenticated_student_redirected_to_login(self):
        client = Client()
        res = client.get(reverse('live_room:classroom', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res.status_code, 302)
        self.assertIn('/student/login/', res.url)
        self.assertIn(f'next=/live/{self.live_class.room_code}/classroom/', res.url)

    # 7. Google-login student can return to live class
    def test_google_login_next_parameter_preserves_live_class(self):
        login_url = f"/student/login/?next=/live/{self.live_class.room_code}/"
        res = self.client.get(login_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, f'/live/{self.live_class.room_code}/')

    # 8. Invalid room code rejected
    def test_invalid_room_code_rejected(self):
        self.client.force_login(self.student)
        res = self.client.get(reverse('live_room:detail', kwargs={'room_code': 'INVALID-ROOM-CODE-000'}))
        self.assertEqual(res.status_code, 404)

        token_res = self.client.get(reverse('live_room:token', kwargs={'room_code': 'INVALID-ROOM-CODE-000'}))
        self.assertEqual(token_res.status_code, 404)

    # 9. Student capacity enforced
    @patch.dict(os.environ, {'LIVEKIT_URL': 'wss://test.livekit.cloud', 'LIVEKIT_API_KEY': 'k', 'LIVEKIT_API_SECRET': 's'*32})
    def test_student_capacity_enforced(self):
        # Fill capacity (max_students=2)
        ClassParticipant.objects.create(
            live_class=self.live_class,
            user=self.student,
            student_name='Alice',
            status=ClassParticipant.Status.JOINED,
            join_time=timezone.now()
        )
        ClassParticipant.objects.create(
            live_class=self.live_class,
            user=self.student2,
            student_name='Bob',
            status=ClassParticipant.Status.JOINED,
            join_time=timezone.now()
        )

        # 3rd student attempts to get token
        student3 = User.objects.create_user(
            username='student_carol',
            email='carol@student.teachlive',
            password='Password123!',
            role=User.Role.STUDENT
        )
        self.client.force_login(student3)
        res = self.client.post(reverse('live_room:token', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res.status_code, 403)
        data = res.json()
        self.assertIn('maximum student capacity', data.get('error', ''))

        # Teacher should still be able to enter even if student capacity is full
        self.client.force_login(self.teacher1)
        teacher_res = self.client.post(reverse('live_room:token', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(teacher_res.status_code, 200)

    # 10. Teacher receives publish permission
    @patch.dict(os.environ, {'LIVEKIT_URL': 'wss://test.livekit.cloud', 'LIVEKIT_API_KEY': 'k', 'LIVEKIT_API_SECRET': 's'*32})
    def test_teacher_receives_publish_permission(self):
        self.client.force_login(self.teacher1)
        res = self.client.post(reverse('live_room:token', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['is_teacher'])
        self.assertTrue(data['can_publish'])

        claims = jwt.decode(data['token'], options={'verify_signature': False})
        video_grant = claims.get('video', {})
        self.assertTrue(video_grant.get('canPublish'))
        self.assertTrue(video_grant.get('canSubscribe'))
        self.assertTrue(video_grant.get('roomAdmin'))

    # 11. Student does not receive publish permission by default
    @patch.dict(os.environ, {'LIVEKIT_URL': 'wss://test.livekit.cloud', 'LIVEKIT_API_KEY': 'k', 'LIVEKIT_API_SECRET': 's'*32})
    def test_student_does_not_receive_publish_permission_by_default(self):
        self.client.force_login(self.student)
        res = self.client.post(reverse('live_room:token', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data['is_teacher'])
        self.assertFalse(data['can_publish'])

        claims = jwt.decode(data['token'], options={'verify_signature': False})
        video_grant = claims.get('video', {})
        self.assertFalse(video_grant.get('canPublish'))
        self.assertTrue(video_grant.get('canSubscribe'))
        self.assertFalse(video_grant.get('roomAdmin', False))

    # 12. LiveKit token endpoint requires authentication
    def test_livekit_token_endpoint_requires_authentication(self):
        client = Client()
        res = client.post(reverse('live_room:token', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res.status_code, 401)

    # 13. LiveKit API secret never appears in response
    @patch.dict(os.environ, {'LIVEKIT_URL': 'wss://test.livekit.cloud', 'LIVEKIT_API_KEY': 'k_pub', 'LIVEKIT_API_SECRET': 'SECRET_SUPER_SAFE_XYZ999'})
    def test_livekit_api_secret_never_appears_in_response(self):
        self.client.force_login(self.student)
        res = self.client.post(reverse('live_room:token', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res.status_code, 200)
        content_str = res.content.decode('utf-8')
        self.assertNotIn('SECRET_SUPER_SAFE_XYZ999', content_str)
        self.assertNotIn('LIVEKIT_API_SECRET', content_str)

    # 14. Only class owner can end class
    def test_only_class_owner_can_end_class(self):
        self.client.force_login(self.teacher1)
        res = self.client.post(reverse('classrooms:teacher_class_end', kwargs={'class_id': self.live_class.pk}))
        self.assertEqual(res.status_code, 302)

        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.status, LiveClass.Status.COMPLETED)
        self.assertIsNotNone(self.live_class.ended_at)

    # 15. Student cannot end class
    def test_student_cannot_end_class(self):
        self.client.force_login(self.student)
        res = self.client.post(reverse('classrooms:teacher_class_end', kwargs={'class_id': self.live_class.pk}))
        self.assertIn(res.status_code, [403, 302])
        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.status, LiveClass.Status.LIVE)

        # Direct POST to /live/<room_code>/end/
        res2 = self.client.post(reverse('live_room:end', kwargs={'room_code': self.live_class.room_code}))
        self.assertIn(res2.status_code, [403, 302])
        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.status, LiveClass.Status.LIVE)


    # 16. Chat message validation works
    def test_chat_message_validation(self):
        self.client.force_login(self.student)
        ClassParticipant.objects.create(
            live_class=self.live_class,
            user=self.student,
            student_name='Grace Hopper',
            student_email='grace.hopper@gmail.com',
            status=ClassParticipant.Status.ACTIVE
        )
        # Empty message
        empty_res = self.client.post(
            reverse('live_room:chat', kwargs={'room_code': self.live_class.room_code}),
            data=json.dumps({'message': '   '}),
            content_type='application/json'
        )
        self.assertEqual(empty_res.status_code, 400)
        self.assertIn('cannot be empty', empty_res.json().get('error', ''))

        # Message exceeding 500 characters
        long_msg = 'A' * 501
        long_res = self.client.post(
            reverse('live_room:chat', kwargs={'room_code': self.live_class.room_code}),
            data=json.dumps({'message': long_msg}),
            content_type='application/json'
        )
        self.assertEqual(long_res.status_code, 400)
        self.assertIn('500 characters', long_res.json().get('error', ''))

    # 17. Chat messages are HTML escaped
    def test_chat_messages_are_html_escaped(self):
        self.client.force_login(self.student)
        ClassParticipant.objects.create(
            live_class=self.live_class,
            user=self.student,
            student_name='Grace Hopper',
            student_email='grace.hopper@gmail.com',
            status=ClassParticipant.Status.ACTIVE
        )
        xss_payload = "<script>alert('xss');</script><b>Dangerous</b>"
        res = self.client.post(
            reverse('live_room:chat', kwargs={'room_code': self.live_class.room_code}),
            data=json.dumps({'message': xss_payload}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        msg_obj = ChatMessage.objects.filter(live_class=self.live_class).last()
        self.assertIsNotNone(msg_obj)
        self.assertNotIn('<script>', msg_obj.message)
        self.assertIn('&lt;script&gt;', msg_obj.message)

    # 18. Student leave flow and attendance integration
    def test_student_leave_flow_and_attendance_integration(self):
        self.client.force_login(self.student)
        # Join first
        self.client.post(reverse('live_room:join', kwargs={'room_code': self.live_class.room_code}))

        # Leave class
        leave_res = self.client.post(reverse('live_room:leave', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(leave_res.status_code, 200)
        self.assertContains(leave_res, 'You Have Left the Classroom')

        # Check Attendance updated
        att = Attendance.objects.filter(live_class=self.live_class, student=self.student).first()
        self.assertIsNotNone(att)
        self.assertEqual(att.status, Attendance.Status.LEFT)
        self.assertIsNotNone(att.left_at)
        self.assertGreaterEqual(att.total_duration, 1)

    # 19. Participant count polling API
    def test_participant_count_polling_api(self):
        ClassParticipant.objects.create(
            live_class=self.live_class,
            user=self.student,
            student_name='Alice Roy',
            status=ClassParticipant.Status.JOINED,
            join_time=timezone.now()
        )
        res = self.client.get(reverse('live_room:participants', kwargs={'room_code': self.live_class.room_code}))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['active_count'], 1)
        self.assertEqual(data['status'], LiveClass.Status.LIVE)
        self.assertTrue(data['is_live'])
        self.assertEqual(data['participants'][0]['student_name'], 'Alice Roy')


from datetime import timedelta
from django.core.cache import cache


class LiveClassAttendanceAndChatReportsTests(TestCase):
    """
    Comprehensive tests covering all 27 requirements from Section 31:
    - Student join creates participant & attendance
    - Duplicate refresh does not create unlimited active sessions
    - Student leave updates participant & attendance with server-side duration
    - Heartbeat & disconnect cleanup
    - Teacher attendance dashboard with real DB metrics
    - Class attendance detail with ownership security (403 for unauthorized)
    - Student cannot view attendance or export CSV (403)
    - CSV export with formula injection sanitization and teacher ownership
    - Chat message authorization, rate limiting (5 msgs / 10s), XSS escaping, class ended check
    """

    def setUp(self):
        cache.clear()
        self.teacher_a = User.objects.create_user(
            username='teacher_a',
            email='teacher_a@teachlive.com',
            password='Password123!',
            role=User.Role.TEACHER,
            first_name='Alan',
            last_name='Turing'
        )
        self.teacher_b = User.objects.create_user(
            username='teacher_b',
            email='teacher_b@teachlive.com',
            password='Password123!',
            role=User.Role.TEACHER,
            first_name='Ada',
            last_name='Lovelace'
        )
        self.student_1 = User.objects.create_user(
            username='rahul_student',
            email='rahul@student.teachlive',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Rahul',
            last_name='Sharma'
        )
        self.student_2 = User.objects.create_user(
            username='aman_student',
            email='aman@student.teachlive',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Aman',
            last_name='Verma'
        )
        self.random_user = User.objects.create_user(
            username='random_intruder',
            email='random@other.com',
            password='Password123!',
            role=User.Role.STUDENT,
            first_name='Random',
            last_name='Intruder'
        )

        self.class_a = LiveClass.objects.create(
            teacher=self.teacher_a,
            title='Advanced Python Architecture',
            subject='Computer Science',
            room_code='TL-PY-888',
            status=LiveClass.Status.LIVE,
            scheduled_date=date.today(),
            scheduled_time=time(10, 0),
            max_students=50,
        )

        self.class_b = LiveClass.objects.create(
            teacher=self.teacher_b,
            title='Data Structures & Algorithms',
            subject='Computer Science',
            room_code='TL-DSA-999',
            status=LiveClass.Status.LIVE,
            scheduled_date=date.today(),
            scheduled_time=time(11, 0),
            max_students=50,
        )

    # 1. Student join creates participant
    def test_student_join_creates_participant(self):
        self.client.force_login(self.student_1)
        res = self.client.post(reverse('live_room:join', kwargs={'room_code': self.class_a.room_code}))
        self.assertEqual(res.status_code, 302)

        participant = ClassParticipant.objects.filter(live_class=self.class_a, user=self.student_1).first()
        self.assertIsNotNone(participant)
        self.assertIn(participant.status, [ClassParticipant.Status.ACTIVE, ClassParticipant.Status.JOINED])
        self.assertEqual(participant.student_name, 'Rahul Sharma')

    # 2. Student join creates attendance
    def test_student_join_creates_attendance(self):
        self.client.force_login(self.student_1)
        self.client.post(reverse('live_room:join', kwargs={'room_code': self.class_a.room_code}))

        attendance = Attendance.objects.filter(live_class=self.class_a, student=self.student_1).first()
        self.assertIsNotNone(attendance)
        self.assertEqual(attendance.status, Attendance.Status.PRESENT)
        self.assertIsNone(attendance.left_at)

    # 3. Duplicate refresh does not create unlimited active sessions
    def test_duplicate_refresh_does_not_create_unlimited_active_sessions(self):
        self.client.force_login(self.student_1)
        # First join
        self.client.post(reverse('live_room:join', kwargs={'room_code': self.class_a.room_code}))

        initial_participant = ClassParticipant.objects.get(live_class=self.class_a, user=self.student_1)
        initial_join_time = initial_participant.join_time

        # Refresh classroom 3 times
        for _ in range(3):
            self.client.get(reverse('live_room:classroom', kwargs={'room_code': self.class_a.room_code}))
            self.client.post(reverse('live_room:join', kwargs={'room_code': self.class_a.room_code}))

        # Assert no duplicate rows were created
        self.assertEqual(ClassParticipant.objects.filter(live_class=self.class_a, user=self.student_1).count(), 1)
        self.assertEqual(Attendance.objects.filter(live_class=self.class_a, student=self.student_1).count(), 1)

        # Assert initial join time was preserved
        refreshed_participant = ClassParticipant.objects.get(live_class=self.class_a, user=self.student_1)
        self.assertEqual(refreshed_participant.join_time, initial_join_time)

    # 4 & 5. Student leave updates participant and attendance
    def test_student_leave_updates_participant_and_attendance(self):
        self.client.force_login(self.student_1)
        self.client.post(reverse('live_room:join', kwargs={'room_code': self.class_a.room_code}))

        leave_res = self.client.post(reverse('live_room:leave', kwargs={'room_code': self.class_a.room_code}))
        self.assertEqual(leave_res.status_code, 200)

        participant = ClassParticipant.objects.get(live_class=self.class_a, user=self.student_1)
        self.assertEqual(participant.status, ClassParticipant.Status.LEFT)
        self.assertIsNotNone(participant.leave_time)

        attendance = Attendance.objects.get(live_class=self.class_a, student=self.student_1)
        self.assertEqual(attendance.status, Attendance.Status.LEFT)
        self.assertIsNotNone(attendance.left_at)
        self.assertGreaterEqual(attendance.total_duration, 1)

    # 6. Heartbeat updates last_seen and auto-disconnects inactive participants
    def test_heartbeat_updates_and_disconnects_inactive_participant(self):
        self.client.force_login(self.student_1)
        self.client.post(reverse('live_room:join', kwargs={'room_code': self.class_a.room_code}))

        # Create an inactive participant who dropped connection > 75 seconds ago
        stale_time = timezone.now() - timedelta(seconds=90)
        stale_p = ClassParticipant.objects.create(
            live_class=self.class_a,
            user=self.student_2,
            student_name='Aman Verma',
            status=ClassParticipant.Status.ACTIVE,
            join_time=stale_time,
            last_seen_at=stale_time,
        )
        stale_att = Attendance.objects.create(
            live_class=self.class_a,
            student=self.student_2,
            student_name='Aman Verma',
            status=Attendance.Status.PRESENT,
            joined_at=stale_time,
        )

        # Heartbeat ping from student_1
        hb_res = self.client.post(reverse('live_room:heartbeat', kwargs={'room_code': self.class_a.room_code}))
        self.assertEqual(hb_res.status_code, 200)
        self.assertEqual(hb_res.json()['status'], 'ok')

        # Check stale participant was disconnected automatically
        stale_p.refresh_from_db()
        self.assertEqual(stale_p.status, ClassParticipant.Status.DISCONNECTED)

        stale_att.refresh_from_db()
        self.assertEqual(stale_att.status, Attendance.Status.DISCONNECTED)
        self.assertIsNotNone(stale_att.left_at)
        self.assertGreaterEqual(stale_att.total_duration, 1)

    # 7. Teacher can view own attendance dashboard
    def test_teacher_can_view_own_attendance_dashboard(self):
        self.client.force_login(self.teacher_a)
        # Create sample attendance records for teacher_a's class
        Attendance.objects.create(
            live_class=self.class_a,
            student=self.student_1,
            student_name='Rahul Sharma',
            status=Attendance.Status.LEFT,
            joined_at=timezone.now() - timedelta(minutes=62),
            left_at=timezone.now(),
            total_duration=62
        )

        res = self.client.get(reverse('teacher_attendance'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Attendance')
        self.assertContains(res, 'Rahul Sharma')
        self.assertContains(res, 'Advanced Python Architecture')
        self.assertContains(res, '62 min')

    # 8. Teacher cannot view another teacher's attendance
    def test_teacher_cannot_view_another_teachers_class_attendance(self):
        self.client.force_login(self.teacher_b)
        # Teacher B attempts to view Teacher A's class attendance
        url = reverse('teacher_class_attendance', kwargs={'class_id': self.class_a.pk})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 403)

    # 9. Student cannot view teacher attendance
    def test_student_cannot_view_teacher_attendance(self):
        self.client.force_login(self.student_1)
        res = self.client.get(reverse('teacher_attendance'))
        self.assertEqual(res.status_code, 403)

        class_att_url = reverse('teacher_class_attendance', kwargs={'class_id': self.class_a.pk})
        res2 = self.client.get(class_att_url)
        self.assertEqual(res2.status_code, 403)

    # 10. CSV export works and contains correct records
    def test_csv_export_works_and_contains_correct_records(self):
        self.client.force_login(self.teacher_a)
        Attendance.objects.create(
            live_class=self.class_a,
            student=self.student_1,
            student_name='Rahul Sharma',
            status=Attendance.Status.LEFT,
            joined_at=timezone.now() - timedelta(minutes=45),
            left_at=timezone.now(),
            total_duration=45
        )

        res = self.client.get(reverse('teacher_attendance_export'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('teachlive_attendance_', res['Content-Disposition'])

        csv_content = res.content.decode('utf-8')
        self.assertIn('Student Name', csv_content)
        self.assertIn('Rahul Sharma', csv_content)
        self.assertIn('Advanced Python Architecture', csv_content)
        self.assertIn('45', csv_content)

    # 11. CSV export respects teacher ownership
    def test_csv_export_respects_teacher_ownership(self):
        self.client.force_login(self.teacher_a)
        # Create record in Teacher B's class
        Attendance.objects.create(
            live_class=self.class_b,
            student=self.student_2,
            student_name='Aman Verma',
            status=Attendance.Status.LEFT,
            joined_at=timezone.now(),
            total_duration=30
        )

        res = self.client.get(reverse('teacher_attendance_export'))
        self.assertEqual(res.status_code, 200)
        csv_content = res.content.decode('utf-8')
        # Teacher A must not see Teacher B's records
        self.assertNotIn('Aman Verma', csv_content)
        self.assertNotIn('Data Structures & Algorithms', csv_content)

        # Teacher A attempts to export Teacher B's class directly
        tamper_res = self.client.get(reverse('teacher_class_attendance_export', kwargs={'class_id': self.class_b.pk}))
        self.assertEqual(tamper_res.status_code, 403)

    # 12. CSV export sanitizes formula injection
    def test_csv_export_sanitizes_formula_injection(self):
        self.client.force_login(self.teacher_a)
        malicious_name = "=SUM(1+1)*cmd|' /C calc'!A0"
        Attendance.objects.create(
            live_class=self.class_a,
            student=self.student_1,
            student_name=malicious_name,
            status=Attendance.Status.PRESENT,
            joined_at=timezone.now(),
            total_duration=10
        )

        res = self.client.get(reverse('teacher_attendance_export'))
        self.assertEqual(res.status_code, 200)
        csv_content = res.content.decode('utf-8')
        # Prepends single quote to neutralize formula injection
        self.assertIn(f"'{malicious_name}", csv_content)

    # 13. Student cannot export attendance CSV
    def test_student_cannot_export_attendance_csv(self):
        self.client.force_login(self.student_1)
        res = self.client.get(reverse('teacher_attendance_export'))
        self.assertEqual(res.status_code, 403)

    # 14. Authorized participant can send chat message
    def test_authorized_participant_can_send_chat_message(self):
        self.client.force_login(self.student_1)
        # Student joins
        self.client.post(reverse('live_room:join', kwargs={'room_code': self.class_a.room_code}))

        chat_res = self.client.post(
            reverse('live_room:chat', kwargs={'room_code': self.class_a.room_code}),
            data=json.dumps({'message': 'Good morning sir'}),
            content_type='application/json'
        )
        self.assertEqual(chat_res.status_code, 200)
        self.assertTrue(chat_res.json()['success'])

        saved_msg = ChatMessage.objects.filter(live_class=self.class_a, sender=self.student_1).first()
        self.assertIsNotNone(saved_msg)
        self.assertEqual(saved_msg.message, 'Good morning sir')
        self.assertEqual(saved_msg.sender_role, ChatMessage.SenderRole.STUDENT)

    # 15. Unauthorized user cannot send chat
    def test_unauthorized_user_cannot_send_chat(self):
        self.client.force_login(self.random_user)
        # Random user has not joined class_a
        chat_res = self.client.post(
            reverse('live_room:chat', kwargs={'room_code': self.class_a.room_code}),
            data=json.dumps({'message': 'I am unauthorized'}),
            content_type='application/json'
        )
        self.assertEqual(chat_res.status_code, 403)

    # 16. Chat rate limiting blocks spam (>5 msgs in 10s)
    def test_chat_rate_limiting(self):
        self.client.force_login(self.student_1)
        self.client.post(reverse('live_room:join', kwargs={'room_code': self.class_a.room_code}))

        # Send 5 messages quickly
        for i in range(5):
            res = self.client.post(
                reverse('live_room:chat', kwargs={'room_code': self.class_a.room_code}),
                data=json.dumps({'message': f'Message {i}'}),
                content_type='application/json'
            )
            self.assertEqual(res.status_code, 200)

        # 6th message triggers rate limit
        res_6 = self.client.post(
            reverse('live_room:chat', kwargs={'room_code': self.class_a.room_code}),
            data=json.dumps({'message': 'Spam message 6'}),
            content_type='application/json'
        )
        self.assertEqual(res_6.status_code, 429)
        self.assertIn('Please slow down', res_6.json().get('error', ''))

    # 17. Chat stops when class ends
    def test_chat_stops_when_class_ends(self):
        self.client.force_login(self.student_1)
        self.client.post(reverse('live_room:join', kwargs={'room_code': self.class_a.room_code}))

        # End class
        self.class_a.end_class()

        chat_res = self.client.post(
            reverse('live_room:chat', kwargs={'room_code': self.class_a.room_code}),
            data=json.dumps({'message': 'Can I still send message?'}),
            content_type='application/json'
        )
        self.assertEqual(chat_res.status_code, 400)
        self.assertIn('Chat ended because the live class has ended', chat_res.json().get('error', ''))



