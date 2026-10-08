from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from django.views.generic import View
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from classrooms.models import Classroom
from .models import ChatMessage
from .serializers import ChatMessageSerializer


class ClassroomChatRoomView(LoginRequiredMixin, View):
    """
    Renders the modern interactive chat and discussion UI for a classroom.
    """
    def get(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, id=classroom_id)
        user = request.user
        if not (user.is_admin_role or classroom.teacher == user or classroom.students.filter(id=user.id).exists()):
            raise PermissionDenied("You must be enrolled in this classroom to access discussions.")

        messages = classroom.chat_messages.select_related('sender').order_by('created_at')[:100]

        return render(request, 'chat/room.html', {
            'classroom': classroom,
            'initial_messages': messages,
            'is_teacher': (classroom.teacher == user or user.is_admin_role),
        })


class ChatMessageListCreateAPI(generics.ListCreateAPIView):
    """
    DRF API to fetch chat messages (supports since_id for efficient polling)
    and create new chat messages.
    """
    serializer_class = ChatMessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        classroom_id = self.request.query_params.get('classroom')
        if not classroom_id:
            return ChatMessage.objects.none()

        classroom = get_object_or_404(Classroom, id=classroom_id)
        user = self.request.user
        if not (user.is_admin_role or classroom.teacher == user or classroom.students.filter(id=user.id).exists()):
            raise PermissionDenied()

        qs = ChatMessage.objects.filter(classroom=classroom).select_related('sender')
        since_id = self.request.query_params.get('since_id')
        if since_id and since_id.isdigit():
            qs = qs.filter(id__gt=int(since_id))

        return qs.order_by('created_at')

    def perform_create(self, serializer):
        classroom_id = self.request.data.get('classroom')
        classroom = get_object_or_404(Classroom, id=classroom_id)
        user = self.request.user
        if not (user.is_admin_role or classroom.teacher == user or classroom.students.filter(id=user.id).exists()):
            raise PermissionDenied("Cannot post message to this classroom.")
        serializer.save(sender=user, classroom=classroom)
