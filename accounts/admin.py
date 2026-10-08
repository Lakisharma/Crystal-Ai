from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Custom administration panel for User model with role badges and filters.
    """
    list_display = (
        'username',
        'email',
        'first_name',
        'last_name',
        'role',
        'is_staff',
        'is_active',
        'date_joined',
    )
    list_filter = ('role', 'is_staff', 'is_superuser', 'is_active', 'groups')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    ordering = ('username',)

    fieldsets = BaseUserAdmin.fieldsets + (
        ('LiveClass Role & Details', {
            'fields': ('role', 'bio', 'phone_number', 'profile_picture')
        }),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('LiveClass Role & Details', {
            'fields': ('role', 'email', 'first_name', 'last_name', 'bio', 'phone_number')
        }),
    )
