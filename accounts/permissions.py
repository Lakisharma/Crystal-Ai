from functools import wraps
from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from rest_framework import permissions


class TeacherRequiredMixin(AccessMixin):
    """
    CBV Mixin to enforce that the authenticated user has the TEACHER role
    (or is an administrator). If the user is unauthenticated, redirect to login.
    If authenticated as a STUDENT or deactivated, raise PermissionDenied (HTTP 403).
    """
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if not request.user.is_active:
            raise PermissionDenied("Access Denied: Your account has been deactivated.")

        if not (request.user.is_teacher or request.user.is_admin_role):
            raise PermissionDenied("Access Denied: Only teachers are authorized to access this section.")

        return super().dispatch(request, *args, **kwargs)


def teacher_required(view_func):
    """
    Decorator for views that checks that the user is logged in and has the
    TEACHER role (or is an administrator).
    Raises PermissionDenied (403) for students or deactivated accounts.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.conf import settings
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)

        if not request.user.is_active:
            raise PermissionDenied("Access Denied: Your account has been deactivated.")

        if not (request.user.is_teacher or request.user.is_admin_role):
            raise PermissionDenied("Access Denied: Only teachers are authorized to access this section.")

        return view_func(request, *args, **kwargs)

    return _wrapped_view


class IsTeacherUser(permissions.BasePermission):
    """
    DRF permission to check if the user is authenticated and is a teacher or admin.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            (request.user.is_teacher or request.user.is_admin_role)
        )


class AdminRequiredMixin(AccessMixin):
    """
    CBV Mixin to enforce that the authenticated user has the ADMIN role
    (or is a superuser). If unauthenticated, redirect to login with ?next=.
    If authenticated as TEACHER or STUDENT, raise PermissionDenied (HTTP 403).
    """
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if not (request.user.is_admin_role or request.user.is_superuser):
            raise PermissionDenied("Access Denied: Only TeachLive Administrators can access the Admin Dashboard.")

        return super().dispatch(request, *args, **kwargs)


def admin_required(view_func):
    """
    Decorator for views that checks that the user is logged in and has the
    ADMIN role (or is a superuser).
    Raises PermissionDenied (403) for teachers and students.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.conf import settings
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)

        if not (request.user.is_admin_role or request.user.is_superuser):
            raise PermissionDenied("Access Denied: Only TeachLive Administrators can access the Admin Dashboard.")

        return view_func(request, *args, **kwargs)

    return _wrapped_view
