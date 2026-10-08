from datetime import time
from django.core.management.base import BaseCommand
from django.utils import timezone
from accounts.models import User
from classrooms.models import Classroom, ClassSchedule
from attendance.models import AttendanceSession, AttendanceRecord
from chat.models import ChatMessage


class Command(BaseCommand):
    help = "Seeds initial demo data (Admin, Teacher, Student, Classroom, Schedules, Attendance, Chat)"

    def handle(self, *args, **options):
        self.stdout.write("Seeding LiveClass demo data...")

        # 1. Admin
        admin, _ = User.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@liveclass.edu',
                'first_name': 'System',
                'last_name': 'Administrator',
                'role': User.Role.ADMIN,
                'is_staff': True,
                'is_superuser': True,
            }
        )
        admin.set_password('AdminPass123!')
        admin.save()

        # 2. Teacher
        teacher, _ = User.objects.get_or_create(
            username='prof_smith',
            defaults={
                'email': 'smith@liveclass.edu',
                'first_name': 'Sarah',
                'last_name': 'Smith',
                'role': User.Role.TEACHER,
                'bio': 'Professor of Computer Science & Software Engineering',
            }
        )
        teacher.set_password('TeacherPass123!')
        teacher.save()

        # 3. Student
        student, _ = User.objects.get_or_create(
            username='student_alex',
            defaults={
                'email': 'alex@student.edu',
                'first_name': 'Alex',
                'last_name': 'Miller',
                'role': User.Role.STUDENT,
            }
        )
        student.set_password('StudentPass123!')
        student.save()

        # 4. Classroom
        classroom, _ = Classroom.objects.get_or_create(
            code='CS101A',
            defaults={
                'name': 'CS101: Introduction to Computer Science',
                'subject': 'Computer Science',
                'description': 'Foundational principles of computation, algorithms, data structures, and web programming.',
                'teacher': teacher,
            }
        )
        classroom.students.add(student)

        # 5. Timetable Schedule
        ClassSchedule.objects.get_or_create(
            classroom=classroom,
            title='Lecture: Algorithms & Python',
            defaults={
                'day_of_week': ClassSchedule.DayOfWeek.MONDAY,
                'start_time': time(9, 30),
                'end_time': time(11, 0),
                'notes': 'Bring laptop with browser installed',
            }
        )
        ClassSchedule.objects.get_or_create(
            classroom=classroom,
            title='Lab: Web Development & DRF APIs',
            defaults={
                'day_of_week': ClassSchedule.DayOfWeek.WEDNESDAY,
                'start_time': time(14, 0),
                'end_time': time(16, 0),
                'notes': 'Practical web coding session',
            }
        )

        # 6. Attendance Session
        session, _ = AttendanceSession.objects.get_or_create(
            classroom=classroom,
            date=timezone.now().date(),
            defaults={
                'topic': 'Introduction to Web Architecture & Django',
                'created_by': teacher,
            }
        )
        AttendanceRecord.objects.get_or_create(
            session=session,
            student=student,
            defaults={
                'status': AttendanceRecord.Status.PRESENT,
                'remarks': 'Attended on time and participated actively',
            }
        )

        # 7. Chat Messages
        ChatMessage.objects.get_or_create(
            classroom=classroom,
            sender=teacher,
            message="Welcome everyone to CS101 LiveClass! All sessions and discussions will occur here.",
        )
        ChatMessage.objects.get_or_create(
            classroom=classroom,
            sender=student,
            message="Hello Professor Smith! Excited to learn about web architectures and building with Django.",
        )

        self.stdout.write(self.style.SUCCESS("Successfully seeded LiveClass database with demo data!"))
        self.stdout.write(self.style.SUCCESS("Credentials:"))
        self.stdout.write("  Admin:   username='admin', password='AdminPass123!'")
        self.stdout.write("  Teacher: username='prof_smith', password='TeacherPass123!'")
        self.stdout.write("  Student: username='student_alex', password='StudentPass123!'")
