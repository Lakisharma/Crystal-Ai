from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    path('classroom/<int:classroom_id>/', views.ClassroomChatRoomView.as_view(), name='room'),

    # DRF API endpoint
    path('api/messages/', views.ChatMessageListCreateAPI.as_view(), name='api_messages'),
]
