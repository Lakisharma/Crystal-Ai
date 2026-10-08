"""
URL configuration for LiveClass project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from classrooms import attendance_views, live_views, enrollment_views, views as classrooms_views
from accounts import views as accounts_views, student_views

urlpatterns = [
    path('admin/', admin.site.urls),

    # App URLs
    path('', include('core.urls', namespace='core')),
    path('student/', include('accounts.student_urls', namespace='student')),
    path('live/', include('classrooms.live_urls', namespace='live_room')),
    path('accounts/', include('accounts.urls', namespace='accounts')),
    path('classrooms/', include('classrooms.urls', namespace='classrooms')),
    path('attendance/', include('attendance.urls', namespace='attendance')),
    path('chat/', include('chat.urls', namespace='chat')),

    # TeachLive Notifications & Notification Center
    path('notifications/', include('notifications.urls', namespace='notifications')),

    # TeachLive Custom Secure Admin Dashboard
    path('admin-dashboard/', include('accounts.admin_dashboard_urls', namespace='admin_dashboard')),

    # Direct Teacher Live Class routes
    path('teacher/classes/<int:class_id>/live/', live_views.TeacherLiveClassroomView.as_view(), name='teacher_class_live_direct'),
    path('teacher/classes/<int:class_id>/end/', live_views.LiveClassEndView.as_view(), name='teacher_class_end_direct'),

    # Direct Teacher Student Management routes
    path('teacher/students/', enrollment_views.TeacherStudentsListView.as_view(), name='teacher_students_direct'),
    path('teacher/students/<int:student_id>/', enrollment_views.TeacherStudentDetailView.as_view(), name='teacher_student_detail_direct'),
    path('teacher/classes/<int:pk>/', classrooms_views.LiveClassDetailView.as_view(), name='teacher_class_detail_direct'),
    path('teacher/classes/<int:class_id>/students/', enrollment_views.TeacherClassStudentsView.as_view(), name='teacher_class_students_id_direct'),
    path('teacher/classes/<str:room_code>/students/', enrollment_views.TeacherClassStudentsView.as_view(), name='teacher_class_students_direct'),
    path('teacher/classes/<int:class_id>/students/add/', enrollment_views.TeacherAddStudentView.as_view(), name='teacher_add_student_id_direct'),
    path('teacher/classes/<int:class_id>/students/<int:enrollment_id>/revoke/', enrollment_views.TeacherRevokeStudentView.as_view(), name='teacher_revoke_student_id_direct'),
    path('teacher/classes/<int:class_id>/students/<int:enrollment_id>/restore/', enrollment_views.TeacherRestoreStudentView.as_view(), name='teacher_restore_student_id_direct'),
    path('teacher/classes/<int:class_id>/access-requests/<int:request_id>/approve/', enrollment_views.TeacherApproveAccessRequestView.as_view(), name='teacher_approve_access_request_id_direct'),
    path('teacher/classes/<int:class_id>/access-requests/<int:request_id>/reject/', enrollment_views.TeacherRejectAccessRequestView.as_view(), name='teacher_reject_access_request_id_direct'),
    path('teacher/classes/<int:class_id>/invite-link/generate/', enrollment_views.TeacherGenerateInviteLinkView.as_view(), name='teacher_generate_invite_link_id_direct'),
    path('teacher/classes/<int:class_id>/invite-link/revoke/', enrollment_views.TeacherRevokeInviteLinkView.as_view(), name='teacher_revoke_invite_link_id_direct'),

    # Direct Student class endpoints
    path('student/classes/invite/<str:token>/', student_views.StudentClassInviteView.as_view(), name='student_class_invite_direct'),
    path('student/classes/<int:class_id>/', student_views.StudentClassDetailView.as_view(), name='student_class_detail_direct'),
    path('student/classes/<int:class_id>/leave/', student_views.StudentLeaveClassView.as_view(), name='student_class_leave_direct'),

    # Direct Teacher Attendance & CSV Export routes
    path('teacher/attendance/', attendance_views.TeacherAttendanceDashboardView.as_view(), name='teacher_attendance'),
    path('teacher/attendance/export/', attendance_views.TeacherAttendanceExportCSVView.as_view(), name='teacher_attendance_export'),
    path('teacher/classes/<int:class_id>/attendance/', attendance_views.ClassAttendanceDetailView.as_view(), name='teacher_class_attendance'),
    path('teacher/classes/<int:class_id>/attendance/export/', attendance_views.TeacherAttendanceExportCSVView.as_view(), name='teacher_class_attendance_export'),

    # Direct Teacher Scheduled Classes, Dashboard & Interactive Calendar routes
    path('teacher/dashboard/', accounts_views.TeacherDashboardView.as_view(), name='teacher_dashboard_direct'),
    path('teacher/classes/', classrooms_views.TeacherClassesListView.as_view(), name='teacher_classes_direct'),
    path('teacher/calendar/', classrooms_views.TeacherCalendarView.as_view(), name='teacher_calendar_direct'),

    # Direct Student Interactive Calendar route
    path('student/calendar/', student_views.StudentCalendarView.as_view(), name='student_calendar_direct'),
]


# Error Handlers
handler404 = 'core.views.custom_404_view'
handler500 = 'core.views.custom_500_view'

# Static and media files serving in development mode
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
