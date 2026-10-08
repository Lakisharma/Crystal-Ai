from django.urls import path
from . import views
from classrooms import attendance_views

app_name = 'attendance'

urlpatterns = [
    path('report/', views.student_attendance_report, name='student_report'),
    path('classroom/<int:classroom_id>/', views.ClassroomAttendanceListView.as_view(), name='classroom_sessions'),
    path('classroom/<int:classroom_id>/take/', views.TakeAttendanceView.as_view(), name='take'),

    # Teacher Live Class Attendance Reports & CSV Export
    path('teacher/', attendance_views.TeacherAttendanceDashboardView.as_view(), name='teacher_dashboard'),
    path('teacher/export/', attendance_views.TeacherAttendanceExportCSVView.as_view(), name='teacher_export'),
    path('live-dashboard/', attendance_views.TeacherAttendanceDashboardView.as_view(), name='live_dashboard'),

    # DRF API
    path('api/sessions/', views.AttendanceSessionListCreateAPI.as_view(), name='api_sessions'),
]
