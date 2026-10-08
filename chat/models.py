from django.conf import settings
from django.db import models
from classrooms.models import Classroom


class ChatMessage(models.Model):
    """
    Real-time or asynchronous classroom discussion message between
    teachers and students in a virtual classroom.
    """
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.CASCADE,
        related_name='chat_messages'
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_chat_messages'
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Chat Message'
        verbose_name_plural = 'Chat Messages'

    def __str__(self):
        return f"[{self.classroom.code}] {self.sender.username}: {self.message[:30]}"
