from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from classrooms.models import Classroom
from attendance.models import AttendanceSession, AttendanceRecord


class AttendanceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username='prof_taylor',
            password='Password123!',
            role=User.Role.TEACHER
        )
        self.student1 = User.objects.create_user(
            username='student1',
            password='Password123!',
            role=User.Role.STUDENT
        )
        self.student2 = User.objects.create_user(
            username='student2',
            password='Password123!',
            role=User.Role.STUDENT
        )
        self.classroom = Classroom.objects.create(
            name='Physics 101',
            code='PHYS101',
            subject='Physics',
            teacher=self.teacher
        )
        self.classroom.students.add(self.student1, self.student2)

    def test_take_attendance_flow(self):
        client = Client()
        client.login(username='prof_taylor', password='Password123!')

        post_data = {
            'date': str(timezone.now().date()),
            'topic': 'Kinematics in 1D',
            f'status_{self.student1.id}': AttendanceRecord.Status.PRESENT,
            f'remarks_{self.student1.id}': 'On time',
            f'status_{self.student2.id}': AttendanceRecord.Status.ABSENT,
            f'remarks_{self.student2.id}': 'Sick leave',
        }
        res = client.post(reverse('attendance:take', kwargs={'classroom_id': self.classroom.id}), post_data, follow=True)
        self.assertEqual(res.status_code, 200)

        session = AttendanceSession.objects.filter(classroom=self.classroom).first()
        self.assertIsNotNone(session)
        self.assertEqual(session.topic, 'Kinematics in 1D')
        self.assertEqual(session.total_present, 1)
        self.assertEqual(session.total_records, 2)

    def test_student_attendance_report(self):
        session = AttendanceSession.objects.create(
            classroom=self.classroom,
            date=timezone.now().date(),
            topic='Intro',
            created_by=self.teacher
        )
        AttendanceRecord.objects.create(
            session=session,
            student=self.student1,
            status=AttendanceRecord.Status.PRESENT
        )

        client = Client()
        client.login(username='student1', password='Password123!')
        res = client.get(reverse('attendance:student_report'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'PHYS101')
        self.assertContains(res, '100.0%')
