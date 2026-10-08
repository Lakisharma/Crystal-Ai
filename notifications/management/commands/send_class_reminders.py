from django.core.management.base import BaseCommand
from notifications.services import ReminderService


class Command(BaseCommand):
    help = "Dispatches upcoming class reminders (in-app notifications and email) for classes starting soon."

    def add_arguments(self, parser):
        parser.add_argument(
            '--window',
            type=int,
            default=30,
            help="Minutes ahead to look for upcoming scheduled classes (default: 30)"
        )

    def handle(self, *args, **options):
        window = options['window']
        self.stdout.write(f"Evaluating upcoming scheduled classes within the next {window} minutes...")
        count = ReminderService.send_upcoming_class_reminders(window_minutes=window)
        self.stdout.write(self.style.SUCCESS(f"Successfully processed upcoming classes. Sent {count} reminders."))
