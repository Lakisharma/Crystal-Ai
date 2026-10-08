from django.urls import path
from . import views, live_views, attendance_views, enrollment_views

app_name = 'classrooms'

urlpatterns = [
    # Course Classrooms
    path('', views.ClassroomListView.as_view(), name='list'),
    path('create/', views.ClassroomCreateView.as_view(), name='create'),
    path('join/', views.ClassroomJoinView.as_view(), name='join'),
    path('<int:pk>/', views.ClassroomDetailView.as_view(), name='detail'),
    path('<int:pk>/status/', views.toggle_classroom_status, name='toggle_status'),
    path('<int:pk>/schedule/add/', views.add_schedule_view, name='add_schedule'),

    # Complete Teacher CRUD for LiveClass
    path('live/', views.LiveClassListView.as_view(), name='live_list'),
    path('live/create/', views.LiveClassCreateView.as_view(), name='live_create'),
    path('live/<int:pk>/', views.LiveClassDetailView.as_view(), name='live_detail'),
    path('live/<int:pk>/edit/', views.LiveClassEditView.as_view(), name='live_edit'),
    path('live/<int:pk>/reschedule/', views.LiveClassRescheduleView.as_view(), name='live_reschedule'),
    path('live/<int:pk>/cancel/', views.LiveClassCancelView.as_view(), name='live_cancel'),
    path('live/<int:pk>/delete/', views.LiveClassDeleteView.as_view(), name='live_delete'),
    path('live/<int:pk>/start/', views.LiveClassStartView.as_view(), name='live_start'),
    path('live/<int:pk>/end/', views.LiveClassEndView.as_view(), name='live_end'),

    # Teacher Schedule Management & Interactive Calendar Views
    path('teacher/classes/', views.TeacherClassesListView.as_view(), name='teacher_classes'),
    path('teacher/calendar/', views.TeacherCalendarView.as_view(), name='teacher_calendar'),
    path('teacher/classes/<int:pk>/reschedule/', views.LiveClassRescheduleView.as_view(), name='teacher_class_reschedule'),
    path('teacher/classes/<int:pk>/cancel/', views.LiveClassCancelView.as_view(), name='teacher_class_cancel'),

    path('teacher/classes/<int:class_id>/live/', live_views.TeacherLiveClassroomView.as_view(), name='teacher_class_live'),
    path('teacher/classes/<int:class_id>/end/', live_views.LiveClassEndView.as_view(), name='teacher_class_end'),

    # Teacher Live Class Attendance Reports & CSV Export
    path('teacher/attendance/', attendance_views.TeacherAttendanceDashboardView.as_view(), name='teacher_attendance'),
    path('teacher/attendance/export/', attendance_views.TeacherAttendanceExportCSVView.as_view(), name='teacher_attendance_export'),
    path('teacher/classes/<int:class_id>/attendance/', attendance_views.ClassAttendanceDetailView.as_view(), name='teacher_class_attendance'),
    path('teacher/classes/<int:class_id>/attendance/export/', attendance_views.TeacherAttendanceExportCSVView.as_view(), name='teacher_class_attendance_export'),

    # Teacher Students Global Management (Prompt #15)
    path('teacher/students/', enrollment_views.TeacherStudentsListView.as_view(), name='teacher_students'),
    path('teacher/students/<int:student_id>/', enrollment_views.TeacherStudentDetailView.as_view(), name='teacher_student_detail'),

    # Teacher Class Student Enrollment Management (by room_code and by class_id)
    path('teacher/classes/<str:room_code>/students/', enrollment_views.TeacherClassStudentsView.as_view(), name='teacher_class_students'),
    path('teacher/classes/<int:class_id>/students/', enrollment_views.TeacherClassStudentsView.as_view(), name='teacher_class_students_by_id'),
    path('teacher/classes/<str:room_code>/students/add/', enrollment_views.TeacherAddStudentView.as_view(), name='teacher_add_student'),
    path('teacher/classes/<int:class_id>/students/add/', enrollment_views.TeacherAddStudentView.as_view(), name='teacher_add_student_by_id'),
    path('teacher/classes/<str:room_code>/students/<int:enrollment_id>/revoke/', enrollment_views.TeacherRevokeStudentView.as_view(), name='teacher_revoke_student'),
    path('teacher/classes/<int:class_id>/students/<int:enrollment_id>/revoke/', enrollment_views.TeacherRevokeStudentView.as_view(), name='teacher_revoke_student_by_id'),
    path('teacher/classes/<str:room_code>/students/<int:enrollment_id>/restore/', enrollment_views.TeacherRestoreStudentView.as_view(), name='teacher_restore_student'),
    path('teacher/classes/<int:class_id>/students/<int:enrollment_id>/restore/', enrollment_views.TeacherRestoreStudentView.as_view(), name='teacher_restore_student_by_id'),
    path('teacher/classes/<str:room_code>/access-requests/<int:request_id>/approve/', enrollment_views.TeacherApproveAccessRequestView.as_view(), name='teacher_approve_access_request'),
    path('teacher/classes/<int:class_id>/access-requests/<int:request_id>/approve/', enrollment_views.TeacherApproveAccessRequestView.as_view(), name='teacher_approve_access_request_by_id'),
    path('teacher/classes/<str:room_code>/access-requests/<int:request_id>/reject/', enrollment_views.TeacherRejectAccessRequestView.as_view(), name='teacher_reject_access_request'),
    path('teacher/classes/<int:class_id>/access-requests/<int:request_id>/reject/', enrollment_views.TeacherRejectAccessRequestView.as_view(), name='teacher_reject_access_request_by_id'),

    # Teacher Class Secure Invite Tokens
    path('teacher/classes/<str:room_code>/invite-link/generate/', enrollment_views.TeacherGenerateInviteLinkView.as_view(), name='teacher_generate_invite_link'),
    path('teacher/classes/<int:class_id>/invite-link/generate/', enrollment_views.TeacherGenerateInviteLinkView.as_view(), name='teacher_generate_invite_link_by_id'),
    path('teacher/classes/<str:room_code>/invite-link/revoke/', enrollment_views.TeacherRevokeInviteLinkView.as_view(), name='teacher_revoke_invite_link'),
    path('teacher/classes/<int:class_id>/invite-link/revoke/', enrollment_views.TeacherRevokeInviteLinkView.as_view(), name='teacher_revoke_invite_link_by_id'),

    # DRF API endpoints
    path('api/classrooms/', views.ClassroomListCreateAPI.as_view(), name='api_list_create'),
    path('api/classrooms/<int:pk>/', views.ClassroomDetailAPI.as_view(), name='api_detail'),
    path('api/classrooms/join/', views.ClassroomJoinAPI.as_view(), name='api_join'),
]
