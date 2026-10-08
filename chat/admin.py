from django.contrib import admin
from .models import ChatMessage


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('classroom', 'sender', 'message_snippet', 'created_at')
    list_filter = ('classroom', 'created_at')
    search_fields = ('message', 'sender__username', 'classroom__name', 'classroom__code')

    def message_snippet(self, obj):
        return obj.message[:50]
    message_snippet.short_description = "Message"
