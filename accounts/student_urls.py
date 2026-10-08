from django.urls import path
from . import student_views

app_name = 'student'

urlpatterns = [
    path('login/', student_views.StudentLoginView.as_view(), name='login'),
    path('logout/', student_views.student_logout_view, name='logout'),
    path('dashboard/', student_views.StudentDashboardView.as_view(), name='dashboard'),
    path('classes/', student_views.StudentClassesListView.as_view(), name='classes'),
    path('classes/<int:class_id>/', student_views.StudentClassDetailView.as_view(), name='class_detail'),
    path('classes/<int:class_id>/leave/', student_views.StudentLeaveClassView.as_view(), name='class_leave'),
    path('classes/<int:class_id>/accept/', student_views.StudentAcceptInviteView.as_view(), name='class_accept'),
    path('classes/<int:class_id>/decline/', student_views.StudentDeclineInviteView.as_view(), name='class_decline'),
    path('classes/invite/<str:token>/', student_views.StudentClassInviteView.as_view(), name='class_invite'),
    path('invite/<str:token>/', student_views.StudentClassInviteView.as_view(), name='invite'),
    path('calendar/', student_views.StudentCalendarView.as_view(), name='calendar'),
    path('attendance/', student_views.StudentAttendanceListView.as_view(), name='attendance'),
    path('classes/<int:class_id>/attendance/', student_views.StudentClassAttendanceDetailView.as_view(), name='class_attendance'),
    path('google/login/', student_views.StudentGoogleAuthInitiateView.as_view(), name='google_login'),
    path('google/callback/', student_views.StudentGoogleCallbackView.as_view(), name='google_callback'),
]
