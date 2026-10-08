"""
TeachLive Secure Admin Dashboard URL Configuration.
All routes require authenticated users with ADMIN role.
"""

from django.urls import path
from . import admin_dashboard_views

app_name = 'admin_dashboard'

urlpatterns = [
    # 1. Main Dashboard
    path('', admin_dashboard_views.AdminDashboardIndexView.as_view(), name='dashboard'),

    # 2. Teacher Management
    path('teachers/', admin_dashboard_views.AdminTeacherListView.as_view(), name='teachers'),
    path('teachers/<int:pk>/', admin_dashboard_views.AdminTeacherDetailView.as_view(), name='teacher_detail'),
    path('teachers/<int:pk>/toggle-status/', admin_dashboard_views.AdminTeacherToggleStatusView.as_view(), name='teacher_toggle_status'),

    # 3. Student Management
    path('students/', admin_dashboard_views.AdminStudentListView.as_view(), name='students'),
    path('students/<int:pk>/', admin_dashboard_views.AdminStudentDetailView.as_view(), name='student_detail'),
    path('students/<int:pk>/toggle-status/', admin_dashboard_views.AdminStudentToggleStatusView.as_view(), name='student_toggle_status'),

    # 4. Live Class Management
    path('classes/', admin_dashboard_views.AdminLiveClassListView.as_view(), name='classes'),
    path('classes/<int:pk>/', admin_dashboard_views.AdminLiveClassDetailView.as_view(), name='class_detail'),
    path('classes/<int:pk>/cancel/', admin_dashboard_views.AdminLiveClassCancelView.as_view(), name='class_cancel'),
    path('classes/<int:pk>/enrollments/<int:enrollment_id>/revoke/', admin_dashboard_views.AdminClassEnrollmentRevokeView.as_view(), name='class_enrollment_revoke'),
    path('classes/<int:pk>/access-requests/<int:request_id>/approve/', admin_dashboard_views.AdminAccessRequestApproveView.as_view(), name='class_access_request_approve'),
    path('classes/<int:pk>/access-requests/<int:request_id>/reject/', admin_dashboard_views.AdminAccessRequestRejectView.as_view(), name='class_access_request_reject'),

    # 5. Attendance Management & Reports
    path('attendance/', admin_dashboard_views.AdminAttendanceListView.as_view(), name='attendance'),
    path('attendance/export/', admin_dashboard_views.AdminAttendanceExportCSVView.as_view(), name='attendance_export'),

    # 6. Chat Reports
    path('chat/', admin_dashboard_views.AdminChatReportView.as_view(), name='chat'),

    # 7. Analytics & Reports
    path('reports/', admin_dashboard_views.AdminReportsView.as_view(), name='reports'),

    # 8. Administrative Audit Logs
    path('audit-logs/', admin_dashboard_views.AdminAuditLogListView.as_view(), name='audit_logs'),

    # 9. Communications & Logs
    path('notifications/', admin_dashboard_views.AdminNotificationListView.as_view(), name='notifications'),
    path('emails/', admin_dashboard_views.AdminEmailLogListView.as_view(), name='emails'),

    # 10. Platform Settings
    path('settings/', admin_dashboard_views.AdminSettingsView.as_view(), name='settings'),
]
