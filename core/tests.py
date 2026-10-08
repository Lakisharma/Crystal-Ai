from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from classrooms.models import Classroom


class CoreViewsTests(TestCase):
    def test_home_page(self):
        client = Client()
        res = client.get(reverse('core:home'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'LiveClass')
        self.assertContains(res, 'Zero Downloads Required')

    def test_about_page(self):
        client = Client()
        res = client.get(reverse('core:about'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'About')

    def test_dashboard_redirection_for_anonymous(self):
        client = Client()
        res = client.get(reverse('core:dashboard'))
        self.assertEqual(res.status_code, 302)

    def test_teacher_dashboard_view(self):
        teacher = User.objects.create_user(
            username='tea_user',
            password='Password123!',
            role=User.Role.TEACHER
        )
        client = Client()
        client.login(username='tea_user', password='Password123!')
        res = client.get(reverse('core:dashboard'), follow=True)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Welcome, Professor')
        self.assertContains(res, 'Instructor Portal')

    def test_student_dashboard_view(self):
        student = User.objects.create_user(
            username='stu_user',
            password='Password123!',
            role=User.Role.STUDENT
        )
        client = Client()
        client.login(username='stu_user', password='Password123!')
        res = client.get(reverse('core:dashboard'), follow=True)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Student Dashboard')
