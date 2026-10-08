from rest_framework import serializers
from accounts.serializers import UserSerializer
from .models import ChatMessage


class ChatMessageSerializer(serializers.ModelSerializer):
    sender = UserSerializer(read_only=True)
    created_at_formatted = serializers.SerializerMethodField()
    is_sender_teacher = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = [
            'id',
            'classroom',
            'sender',
            'is_sender_teacher',
            'message',
            'created_at',
            'created_at_formatted',
        ]
        read_only_fields = ['id', 'sender', 'created_at', 'created_at_formatted', 'is_sender_teacher']

    def get_created_at_formatted(self, obj):
        return obj.created_at.strftime('%b %d, %H:%M')

    def get_is_sender_teacher(self, obj):
        return obj.sender.is_teacher or obj.sender == obj.classroom.teacher
