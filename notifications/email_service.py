import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from .models import EmailLog

logger = logging.getLogger(__name__)


class EmailService:
    """
    Central transactional email dispatcher for TeachLive.
    Handles rendering HTML templates, plain-text fallback generation,
    robust error-handling without blocking calling processes, and audit logging.
    """

    @classmethod
    def send_teachlive_email(
        cls,
        to_email: str,
        subject: str,
        template_name: str,
        context: dict,
        email_type: str,
        related_live_class=None
    ) -> bool:
        """
        Dispatches an HTML/plain-text email safely and logs the outcome in EmailLog.
        Never raises exceptions to callers — failures are recorded safely.
        """
        if not to_email:
            logger.warning("EmailService: Skipped sending %s because to_email is empty.", email_type)
            return False

        try:
            html_content = render_to_string(template_name, context)
            plain_text = strip_tags(html_content).strip()

            from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'TeachLive <no-reply@teachlive.edu>')

            send_mail(
                subject=subject,
                message=plain_text,
                from_email=from_email,
                recipient_list=[to_email],
                html_message=html_content,
                fail_silently=False,
            )

            # Record success in EmailLog
            EmailLog.objects.create(
                recipient=to_email,
                email_type=email_type,
                subject=subject,
                related_live_class=related_live_class,
                status=EmailLog.Status.SENT,
                error_message=''
            )
            logger.info("EmailService: Dispatched %s email to %s", email_type, to_email)
            return True

        except Exception as exc:
            safe_error = str(exc)[:500]
            logger.error("EmailService: Failed to send %s email to %s: %s", email_type, to_email, safe_error)
            try:
                EmailLog.objects.create(
                    recipient=to_email,
                    email_type=email_type,
                    subject=subject,
                    related_live_class=related_live_class,
                    status=EmailLog.Status.FAILED,
                    error_message=safe_error
                )
            except Exception as log_exc:
                logger.error("EmailService: Could not write EmailLog: %s", log_exc)
            return False

    # -------------------------------------------------------------
    # Concrete Email Senders
    # -------------------------------------------------------------

    @classmethod
    def send_welcome_teacher_email(cls, user) -> bool:
        """Dispatches welcome onboarding email to newly registered instructors."""
        context = {
            'user': user,
            'dashboard_url': '/accounts/teacher/dashboard/',
        }
        return cls.send_teachlive_email(
            to_email=user.email,
            subject="Welcome to TeachLive - Instructor Account Ready",
            template_name="emails/welcome_teacher.html",
            context=context,
            email_type="WELCOME_TEACHER"
        )

    @classmethod
    def send_welcome_student_google_email(cls, user) -> bool:
        """Dispatches welcome email to new students signed up via Google OAuth."""
        context = {
            'user': user,
            'dashboard_url': '/student/dashboard/',
        }
        return cls.send_teachlive_email(
            to_email=user.email,
            subject="Welcome to TeachLive - Student Account Ready",
            template_name="emails/welcome_student_google.html",
            context=context,
            email_type="WELCOME_STUDENT_GOOGLE"
        )

    @classmethod
    def send_welcome_student_email(cls, user) -> bool:
        """Dispatches welcome email to students registered via standard credentials."""
        context = {
            'user': user,
            'dashboard_url': '/student/dashboard/',
        }
        return cls.send_teachlive_email(
            to_email=user.email,
            subject="Welcome to TeachLive - Student Account Ready",
            template_name="emails/welcome_student_google.html",
            context=context,
            email_type="WELCOME_STUDENT"
        )

    @classmethod
    def send_class_created_email(cls, live_class) -> bool:
        """Confirms live class creation to the hosting instructor."""
        teacher = live_class.teacher
        context = {
            'live_class': live_class,
            'class_url': live_class.get_absolute_url(),
        }
        return cls.send_teachlive_email(
            to_email=teacher.email,
            subject=f"Class Scheduled: {live_class.title} ({live_class.room_code})",
            template_name="emails/class_created.html",
            context=context,
            email_type="CLASS_CREATED",
            related_live_class=live_class
        )

    @classmethod
    def send_class_updated_email(cls, live_class, recipient, update_summary="") -> bool:
        """Notifies a user about changes to a scheduled live class session."""
        context = {
            'live_class': live_class,
            'update_summary': update_summary,
            'class_url': live_class.get_absolute_url(),
        }
        return cls.send_teachlive_email(
            to_email=recipient.email if hasattr(recipient, 'email') else str(recipient),
            subject=f"Updated Schedule: {live_class.title}",
            template_name="emails/class_updated.html",
            context=context,
            email_type="CLASS_UPDATED",
            related_live_class=live_class
        )

    @classmethod
    def send_class_cancelled_email(cls, live_class, recipient, cancelled_by_name: str) -> bool:
        """Notifies an instructor or student about a cancelled class."""
        context = {
            'live_class': live_class,
            'cancelled_by_name': cancelled_by_name,
            'dashboard_url': '/student/dashboard/' if getattr(recipient, 'is_student', False) else '/accounts/teacher/dashboard/',
        }
        return cls.send_teachlive_email(
            to_email=recipient.email if hasattr(recipient, 'email') else str(recipient),
            subject=f"Class Cancelled: {live_class.title}",
            template_name="emails/class_cancelled.html",
            context=context,
            email_type="CLASS_CANCELLED",
            related_live_class=live_class
        )

    @classmethod
    def send_class_reminder_email(cls, live_class, recipient, minutes_before: int = 30) -> bool:
        """Dispatches an upcoming class reminder email."""
        context = {
            'live_class': live_class,
            'minutes_before': minutes_before,
            'join_url': f"/live-class/{live_class.room_code}/",
        }
        return cls.send_teachlive_email(
            to_email=recipient.email if hasattr(recipient, 'email') else str(recipient),
            subject=f"Reminder: '{live_class.title}' begins in {minutes_before} minutes",
            template_name="emails/class_reminder.html",
            context=context,
            email_type="CLASS_REMINDER",
            related_live_class=live_class
        )

    @classmethod
    def send_class_status_email(cls, live_class, recipient, status_title: str, status_message: str) -> bool:
        """Generic class status change notification email."""
        context = {
            'live_class': live_class,
            'status_title': status_title,
            'status_message': status_message,
            'action_url': live_class.get_absolute_url(),
        }
        return cls.send_teachlive_email(
            to_email=recipient.email if hasattr(recipient, 'email') else str(recipient),
            subject=f"{status_title}: {live_class.title}",
            template_name="emails/class_status_notification.html",
            context=context,
            email_type="CLASS_STATUS",
            related_live_class=live_class
        )

    @classmethod
    def send_class_invitation_email(cls, live_class, student) -> bool:
        """Dispatches an invitation email to a student invited to a LiveClass."""
        context = {
            'live_class': live_class,
            'student': student,
            'join_url': f"/live/{live_class.room_code}/",
        }
        return cls.send_teachlive_email(
            to_email=student.email if hasattr(student, 'email') else str(student),
            subject=f"Class Invitation: '{live_class.title}' - TeachLive",
            template_name="emails/class_invitation.html",
            context=context,
            email_type="CLASS_INVITATION",
            related_live_class=live_class
        )

    @classmethod
    def send_access_approved_email(cls, live_class, student) -> bool:
        """Notifies a student that their access request was approved."""
        context = {
            'live_class': live_class,
            'student': student,
            'join_url': f"/live/{live_class.room_code}/",
        }
        return cls.send_teachlive_email(
            to_email=student.email if hasattr(student, 'email') else str(student),
            subject=f"Access Approved: '{live_class.title}' - TeachLive",
            template_name="emails/access_approved.html",
            context=context,
            email_type="ACCESS_APPROVED",
            related_live_class=live_class
        )

    @classmethod
    def send_access_rejected_email(cls, live_class, student) -> bool:
        """Notifies a student that their access request was rejected."""
        context = {
            'live_class': live_class,
            'student': student,
            'dashboard_url': "/student/dashboard/",
        }
        return cls.send_teachlive_email(
            to_email=student.email if hasattr(student, 'email') else str(student),
            subject=f"Access Request Update: '{live_class.title}' - TeachLive",
            template_name="emails/access_rejected.html",
            context=context,
            email_type="ACCESS_REJECTED",
            related_live_class=live_class
        )

    @classmethod
    def send_access_revoked_email(cls, live_class, student) -> bool:
        """Notifies a student that their enrollment was revoked."""
        context = {
            'live_class': live_class,
            'student': student,
            'dashboard_url': "/student/dashboard/",
        }
        return cls.send_teachlive_email(
            to_email=student.email if hasattr(student, 'email') else str(student),
            subject=f"Enrollment Revoked: '{live_class.title}' - TeachLive",
            template_name="emails/access_revoked.html",
            context=context,
            email_type="ACCESS_REVOKED",
            related_live_class=live_class
        )
