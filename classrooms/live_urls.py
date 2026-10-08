from django.urls import path
from . import live_views

app_name = 'live_room'

urlpatterns = [
    # Join and session pages
    path('<str:room_code>/', live_views.LiveClassJoinScreenView.as_view(), name='detail'),
    path('<str:room_code>/join/', live_views.LiveClassJoinActionView.as_view(), name='join'),
    path('<str:room_code>/classroom/', live_views.StudentLiveClassroomView.as_view(), name='classroom'),
    path('<str:room_code>/teacher/', live_views.TeacherLiveClassroomView.as_view(), name='teacher_live'),
    path('<str:room_code>/room/', live_views.StudentLiveClassroomView.as_view(), name='session'),
    path('<str:room_code>/leave/', live_views.LiveClassLeaveView.as_view(), name='leave'),
    path('<str:room_code>/request-access/', live_views.LiveClassRequestAccessView.as_view(), name='request_access'),

    # Real-time WebRTC LiveKit token API
    path('<str:room_code>/token/', live_views.LiveKitTokenAPIView.as_view(), name='token'),

    # In-room chat, heartbeat & status polling APIs
    path('<str:room_code>/chat/', live_views.LiveClassChatAPIView.as_view(), name='chat'),
    path('<str:room_code>/heartbeat/', live_views.LiveClassHeartbeatAPIView.as_view(), name='heartbeat'),
    path('<str:room_code>/participants/', live_views.LiveClassStatusAPIView.as_view(), name='participants'),
    path('<str:room_code>/status/', live_views.LiveClassStatusAPIView.as_view(), name='status'),

    # Classroom moderation & participant controls
    path('<str:room_code>/moderate/', live_views.LiveClassModerationAPIView.as_view(), name='moderate'),
    path('<str:room_code>/hand/', live_views.LiveClassHandToggleAPIView.as_view(), name='hand_toggle'),
    path('<str:room_code>/removed/', live_views.LiveClassRemovedView.as_view(), name='removed'),

    # End class
    path('<str:room_code>/end/', live_views.LiveClassEndView.as_view(), name='end'),
]

