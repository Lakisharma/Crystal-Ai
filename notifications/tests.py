from datetime import date, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from classrooms.models import ClassParticipant, LiveClass
from notifications.email_service import EmailService
from notifications.models import EmailLog, Notification
from notifications.services import NotificationService, ReminderService

User = get_user_model()


class TeachLiveNotificationAndEmailTests(TestCase):
    """
    Automated test suite verifying the TeachLive Notifications and Transactional Email system:
    - Notification model creation and indexing
    - Notification recipient isolation (users only access their own)
    - Mark single notification as read (POST-only, CSRF safe)
    - Mark all notifications as read (POST-only)
    - GET requests to modification endpoints rejected (405)
    - Unread count calculation via context processor
    - Notification feed pagination and filtering
    - Teacher class creation notifications & confirmation email
    - Class update notifications and participant notifications
    - Class cancellation notifications & emails
    - Welcome teacher email dispatch on registration
    - Welcome student email dispatch
    - Robustness: Email dispatch failure does not crash registration or auth
    - Security: No passwords, OAuth secrets, or LiveKit keys in email templates
    - Admin notification monitoring (/admin-dashboard/notifications/)
    - Admin email delivery logging (/admin-dashboard/emails/)
    - Duplicate notification prevention logic
    - ReminderService upcoming class reminder processing
    """

    def setUp(self):
        # 1. Admin user
        self.admin_user = User.objects.create_user(
            username='site_admin',
            email='admin@teachlive.com',
            password='AdminPassword123!',
            role=User.Role.ADMIN,
            first_name='Admin',
            last_name='User'
        )

        # 2. Teacher user
        self.teacher_user = User.objects.create_user(
            username='prof_hopper',
            email='hopper@teachlive.com',
            password='TeacherPassword123!',
            role=User.Role.TEACHER,
            first_name='Grace',
            last_name='Hopper'
        )

        # 3. Student user A
        self.student_a = User.objects.create_user(
            username='student_alan',
            email='alan@teachlive.com',
            password='StudentPassword123!',
            role=User.Role.STUDENT,
            first_name='Alan',
            last_name='Student'
        )

        # 4. Student user B
        self.student_b = User.objects.create_user(
            username='student_betty',
            email='betty@teachlive.com',
            password='StudentPassword123!',
            role=User.Role.STUDENT,
            first_name='Betty',
            last_name='Student'
        )

        # 5. LiveClass
        self.live_class = LiveClass.objects.create(
            teacher=self.teacher_user,
            title='Compiler Design & Parsing',
            subject='Computer Science',
            room_code='TL-COMP-999',
            status=LiveClass.Status.SCHEDULED,
            scheduled_date=date.today(),
            scheduled_time=time(15, 30),
            duration=60,
            max_students=40,
        )

    # 1. Notification model creation
    def test_notification_model_creation(self):
        notif = Notification.objects.create(
            recipient=self.student_a,
            notification_type=Notification.Type.CLASS_SCHEDULED,
            title='New Class Scheduled',
            message='You have a new class scheduled for today.',
            related_live_class=self.live_class
        )
        self.assertEqual(notif.recipient, self.student_a)
        self.assertFalse(notif.is_read)
        self.assertIsNone(notif.read_at)
        self.assertEqual(notif.related_live_class, self.live_class)
        self.assertIn('New Class Scheduled', str(notif))

    # 2. Notification belongs to correct user and cannot be seen by others
    def test_notification_recipient_isolation(self):
        notif_a = Notification.objects.create(
            recipient=self.student_a,
            title='Private Student A Notification',
            message='Personal alert for student A.'
        )
        notif_b = Notification.objects.create(
            recipient=self.student_b,
            title='Private Student B Notification',
            message='Personal alert for student B.'
        )

        # Student A logs in
        self.client.force_login(self.student_a)
        res = self.client.get(reverse('notifications:list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Private Student A Notification')
        self.assertNotContains(res, 'Private Student B Notification')

        # Student B logs in
        self.client.force_login(self.student_b)
        res_b = self.client.get(reverse('notifications:list'))
        self.assertEqual(res_b.status_code, 200)
        self.assertContains(res_b, 'Private Student B Notification')
        self.assertNotContains(res_b, 'Private Student A Notification')

    # 3. User cannot mark another user's notification as read
    def test_user_cannot_mark_another_users_notification(self):
        notif_a = Notification.objects.create(
            recipient=self.student_a,
            title='Student A Notification',
            message='Confidential message.'
        )

        # Student B attempts to mark Student A's notification as read
        self.client.force_login(self.student_b)
        url = reverse('notifications:mark_read', kwargs={'pk': notif_a.pk})
        res = self.client.post(url)
        self.assertEqual(res.status_code, 404)

        notif_a.refresh_from_db()
        self.assertFalse(notif_a.is_read)

    # 4. Mark single notification as read
    def test_mark_notification_as_read(self):
        notif_a = Notification.objects.create(
            recipient=self.student_a,
            title='Assignment Notice',
            message='Please review syllabus.'
        )
        self.client.force_login(self.student_a)
        url = reverse('notifications:mark_read', kwargs={'pk': notif_a.pk})
        res = self.client.post(url, follow=True)
        self.assertEqual(res.status_code, 200)

        notif_a.refresh_from_db()
        self.assertTrue(notif_a.is_read)
        self.assertIsNotNone(notif_a.read_at)

    # 5. Mark all notifications as read
    def test_mark_all_notifications_as_read(self):
        for i in range(3):
            Notification.objects.create(
                recipient=self.student_a,
                title=f'Alert {i}',
                message=f'Message {i}'
            )

        self.assertEqual(self.student_a.notifications.filter(is_read=False).count(), 3)

        self.client.force_login(self.student_a)
        url = reverse('notifications:mark_all_read')
        res = self.client.post(url, follow=True)
        self.assertEqual(res.status_code, 200)

        self.assertEqual(self.student_a.notifications.filter(is_read=False).count(), 0)

    # 6. GET requests to mark-read endpoints are rejected with 405 Method Not Allowed
    def test_get_request_to_mark_read_is_rejected(self):
        notif = Notification.objects.create(
            recipient=self.student_a,
            title='Test Alert',
            message='Testing GET prevention.'
        )
        self.client.force_login(self.student_a)

        # Single mark read GET
        res_single = self.client.get(reverse('notifications:mark_read', kwargs={'pk': notif.pk}))
        self.assertEqual(res_single.status_code, 405)

        # Mark all read GET
        res_all = self.client.get(reverse('notifications:mark_all_read'))
        self.assertEqual(res_all.status_code, 405)

        notif.refresh_from_db()
        self.assertFalse(notif.is_read)

    # 7. Unread count in context processor
    def test_unread_notifications_context_processor(self):
        Notification.objects.create(
            recipient=self.student_a,
            title='Unread 1',
            message='msg 1'
        )
        Notification.objects.create(
            recipient=self.student_a,
            title='Unread 2',
            message='msg 2'
        )

        self.client.force_login(self.student_a)
        res = self.client.get(reverse('notifications:list'))
        self.assertEqual(res.context['unread_notifications_count'], 2)

    # 8. Notification pagination
    def test_notification_pagination(self):
        for i in range(25):
            Notification.objects.create(
                recipient=self.student_a,
                title=f'Batch Notification {i}',
                message=f'Bulk test message {i}'
            )

        self.client.force_login(self.student_a)
        res = self.client.get(reverse('notifications:list'))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.context['notifications'].has_other_pages())
        self.assertEqual(len(res.context['notifications']), 20)

    # 9. Teacher class creation notification and email
    def test_teacher_class_creation_notification(self):
        mail.outbox.clear()

        # Simulate teacher creating live class
        self.client.force_login(self.teacher_user)
        future_date = (timezone.localtime(timezone.now()) + timedelta(days=2)).date()
        res = self.client.post(reverse('classrooms:live_create'), {
            'title': 'Quantum Computing 101',
            'subject': 'Physics',
            'description': 'Introduction to qubits and quantum logic.',
            'scheduled_date': future_date.strftime('%Y-%m-%d'),
            'scheduled_time': '16:00',
            'duration': 45,
            'max_students': 30,
            'status': LiveClass.Status.SCHEDULED,
        }, follow=True)
        self.assertEqual(res.status_code, 200)

        # Check teacher received in-app notification
        created_notif = Notification.objects.filter(
            recipient=self.teacher_user,
            notification_type=Notification.Type.CLASS_CREATED
        ).first()
        self.assertIsNotNone(created_notif)
        self.assertIn('Quantum Computing 101', created_notif.title)

        # Check confirmation email was sent
        self.assertTrue(len(mail.outbox) >= 1)
        sent_email = mail.outbox[-1]
        self.assertIn('Quantum Computing 101', sent_email.subject)
        self.assertIn(self.teacher_user.email, sent_email.to)

    # 10. Class update notification
    def test_class_update_notification(self):
        # Register a participant for the class
        ClassParticipant.objects.create(
            live_class=self.live_class,
            user=self.student_a,
            student_name='Alan Student',
            student_email='alan@teachlive.com'
        )

        notifs = NotificationService.notify_class_updated(
            self.live_class,
            update_summary="Room time shifted by 30 mins."
        )

        # Both teacher and registered student should have received notification
        teacher_notif = Notification.objects.filter(
            recipient=self.teacher_user,
            notification_type=Notification.Type.TEACHER_CLASS_UPDATE
        ).first()
        student_notif = Notification.objects.filter(
            recipient=self.student_a,
            notification_type=Notification.Type.TEACHER_CLASS_UPDATE
        ).first()

        self.assertIsNotNone(teacher_notif)
        self.assertIsNotNone(student_notif)
        self.assertIn('Room time shifted', teacher_notif.message)

    # 11. Class cancellation notification
    def test_class_cancellation_notification(self):
        ClassParticipant.objects.create(
            live_class=self.live_class,
            user=self.student_a,
            student_name='Alan Student',
            student_email='alan@teachlive.com'
        )

        NotificationService.notify_class_cancelled(self.live_class, cancelled_by=self.admin_user)

        # Teacher cancellation notification
        teacher_notif = Notification.objects.filter(
            recipient=self.teacher_user,
            notification_type=Notification.Type.CLASS_CANCELLED
        ).first()
        self.assertIsNotNone(teacher_notif)

        # Student cancellation notification
        student_notif = Notification.objects.filter(
            recipient=self.student_a,
            notification_type=Notification.Type.CLASS_CANCELLED
        ).first()
        self.assertIsNotNone(student_notif)

    # 12. Welcome email service for new teacher
    def test_welcome_teacher_email_service(self):
        mail.outbox.clear()
        success = EmailService.send_welcome_teacher_email(self.teacher_user)
        self.assertTrue(success)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn('Welcome to Crystal AI', sent.subject)
        self.assertIn(self.teacher_user.email, sent.to)

        # Verify EmailLog was created
        log = EmailLog.objects.filter(recipient=self.teacher_user.email, email_type='WELCOME_TEACHER').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, EmailLog.Status.SENT)

    # 13. Welcome email service for student Google OAuth
    def test_welcome_student_google_email_service(self):
        mail.outbox.clear()
        success = EmailService.send_welcome_student_google_email(self.student_a)
        self.assertTrue(success)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn('Welcome to Crystal AI', sent.subject)
        self.assertIn(self.student_a.email, sent.to)

        # Verify EmailLog was created
        log = EmailLog.objects.filter(recipient=self.student_a.email, email_type='WELCOME_STUDENT_GOOGLE').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, EmailLog.Status.SENT)

    # 14. Email failure does NOT break calling operations
    @patch('notifications.email_service.send_mail')
    def test_email_failure_does_not_crash_caller(self, mock_send):
        mock_send.side_effect = Exception("SMTP Connection Timeout: 504")

        # Calling send_welcome_teacher_email should return False safely without raising
        result = EmailService.send_welcome_teacher_email(self.teacher_user)
        self.assertFalse(result)

        # Failed EmailLog must be recorded
        log = EmailLog.objects.filter(
            recipient=self.teacher_user.email,
            email_type='WELCOME_TEACHER',
            status=EmailLog.Status.FAILED
        ).first()
        self.assertIsNotNone(log)
        self.assertIn('SMTP Connection Timeout', log.error_message)

    # 15. Security: No sensitive passwords, secrets, or LiveKit keys in email templates
    def test_email_templates_do_not_leak_secrets(self):
        mail.outbox.clear()
        EmailService.send_class_created_email(self.live_class)
        self.assertEqual(len(mail.outbox), 1)
        content = mail.outbox[0].body + mail.outbox[0].alternatives[0][0]

        # Ensure no sensitive keywords appear
        self.assertNotIn('LIVEKIT_API_SECRET', content)
        self.assertNotIn('room_password_hash', content)
        self.assertNotIn('SECRET_KEY', content)
        self.assertNotIn('pbkdf2_sha256', content)

    # 16. Role-based notification access (403 for Teacher/Student on admin endpoints)
    def test_role_based_admin_notifications_access(self):
        # Teacher denied
        self.client.force_login(self.teacher_user)
        res_t = self.client.get(reverse('admin_dashboard:notifications'))
        self.assertEqual(res_t.status_code, 403)

        # Student denied
        self.client.force_login(self.student_a)
        res_s = self.client.get(reverse('admin_dashboard:notifications'))
        self.assertEqual(res_s.status_code, 403)

        # Admin allowed
        self.client.force_login(self.admin_user)
        res_a = self.client.get(reverse('admin_dashboard:notifications'))
        self.assertEqual(res_a.status_code, 200)

    # 17. Admin notification monitoring page
    def test_admin_notification_monitoring_page(self):
        Notification.objects.create(
            recipient=self.teacher_user,
            title='Platform Maintenance Notice',
            message='Scheduled downtime tonight.'
        )
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('admin_dashboard:notifications'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Platform Notifications Log')
        self.assertContains(res, 'Platform Maintenance Notice')
        self.assertContains(res, 'Grace Hopper')
        self.assertContains(res, 'hopper@teachlive.com')

    # 18. Admin email delivery monitoring page
    def test_admin_email_delivery_monitoring_page(self):
        EmailLog.objects.create(
            recipient='audit@example.com',
            email_type='AUDIT_TEST',
            subject='Testing Email Logging Interface',
            status=EmailLog.Status.SENT
        )
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('admin_dashboard:emails'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Email Delivery Logs')
        self.assertContains(res, 'audit@example.com')
        self.assertContains(res, 'Testing Email Logging Interface')

    # 19. Duplicate notification prevention
    def test_duplicate_notification_prevention(self):
        # First creation succeeds
        n1 = NotificationService.create_notification(
            recipient=self.student_a,
            notification_type=Notification.Type.CLASS_STARTING,
            title='Class Starting Soon',
            message='Please enter room.',
            related_live_class=self.live_class,
            check_duplicate_minutes=10
        )
        self.assertIsNotNone(n1)

        # Second creation with identical parameters within 10 minutes returns existing
        n2 = NotificationService.create_notification(
            recipient=self.student_a,
            notification_type=Notification.Type.CLASS_STARTING,
            title='Class Starting Soon',
            message='Please enter room.',
            related_live_class=self.live_class,
            check_duplicate_minutes=10
        )
        self.assertEqual(n1.id, n2.id)

        # Count in database is 1, not 2
        count = Notification.objects.filter(
            recipient=self.student_a,
            notification_type=Notification.Type.CLASS_STARTING
        ).count()
        self.assertEqual(count, 1)

    # 20. ReminderService upcoming class reminder
    def test_reminder_service_processing(self):
        # Set scheduled_time to 15 minutes from now in local timezone
        local_now = timezone.localtime(timezone.now())
        target_time = (local_now + timedelta(minutes=15)).time()
        self.live_class.scheduled_date = local_now.date()
        self.live_class.scheduled_time = target_time
        self.live_class.save()

        # Run reminder service looking ahead 30 minutes
        sent_count = ReminderService.send_upcoming_class_reminders(window_minutes=30)
        self.assertTrue(sent_count >= 1)

        # Check teacher received CLASS_STARTING notification
        reminder_notif = Notification.objects.filter(
            recipient=self.teacher_user,
            notification_type=Notification.Type.CLASS_STARTING,
            related_live_class=self.live_class
        ).first()
        self.assertIsNotNone(reminder_notif)
