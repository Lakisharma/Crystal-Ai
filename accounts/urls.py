from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    # Universal Authentication
    path('login/', views.UniversalLoginView.as_view(), name='login'),
    path('admin/login/', views.AdminLoginView.as_view(), name='admin_login'),
    path('logout/', views.user_logout_view, name='logout'),
    path('profile/', views.profile_view, name='profile'),

    # Teacher specific Authentication & Dashboard
    path('teacher/login/', views.TeacherLoginView.as_view(), name='teacher_login'),
    path('teacher/register/', views.TeacherRegisterView.as_view(), name='teacher_register'),
    path('teacher/logout/', views.user_logout_view, name='teacher_logout'),
    path('teacher/dashboard/', views.TeacherDashboardView.as_view(), name='teacher_dashboard'),
    path('teacher/profile/', views.TeacherProfileView.as_view(), name='teacher_profile'),
    path('teacher/change-password/', views.TeacherPasswordChangeView.as_view(), name='teacher_change_password'),

    # Student specific
    path('student/login/', views.StudentLoginView.as_view(), name='student_login'),
    path('student/register/', views.StudentRegisterView.as_view(), name='student_register'),

    # DRF API
    path('api/me/', views.CurrentUserAPIView.as_view(), name='api_me'),
]
