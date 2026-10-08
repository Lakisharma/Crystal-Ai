from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from classrooms.models import Classroom
from chat.models import ChatMessage


class ChatTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username='prof_green',
            password='Password123!',
            role=User.Role.TEACHER
        )
        self.student = User.objects.create_user(
            username='sam_student',
            password='Password123!',
            role=User.Role.STUDENT
        )
        self.classroom = Classroom.objects.create(
            name='Chemistry 201',
            code='CHEM201',
            subject='Chemistry',
            teacher=self.teacher
        )
        self.classroom.students.add(self.student)

    def test_chat_room_view_and_post_message(self):
        client = Client()
        client.login(username='sam_student', password='Password123!')

        # Access chat room page
        res = client.get(reverse('chat:room', kwargs={'classroom_id': self.classroom.id}))
        self.assertEqual(res.status_code, 200)

        # Post message via DRF API
        api_res = client.post(
            reverse('chat:api_messages'),
            data={'classroom': self.classroom.id, 'message': 'Hello Professor!'},
            content_type='application/json'
        )
        self.assertEqual(api_res.status_code, 201)
        msg_id = api_res.json()['id']

        # Query messages via DRF API
        get_res = client.get(
            f"{reverse('chat:api_messages')}?classroom={self.classroom.id}"
        )
        self.assertEqual(get_res.status_code, 200)
        messages_data = get_res.json().get('results', get_res.json())
        self.assertEqual(len(messages_data), 1)
        self.assertEqual(messages_data[0]['message'], 'Hello Professor!')

        # Query with since_id
        since_res = client.get(
            f"{reverse('chat:api_messages')}?classroom={self.classroom.id}&since_id={msg_id}"
        )
        self.assertEqual(since_res.status_code, 200)
        since_data = since_res.json().get('results', since_res.json())
        self.assertEqual(len(since_data), 0)
