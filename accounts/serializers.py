from rest_framework import serializers
from .models import User


class UserSerializer(serializers.ModelSerializer):
    """
    Serializer for user data in API endpoints.
    """
    role_display = serializers.CharField(source='get_role_display', read_only=True)

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'role',
            'role_display',
            'bio',
            'phone_number',
            'is_teacher',
            'is_student',
            'is_admin_role',
        ]
        read_only_fields = ['id', 'role_display', 'is_teacher', 'is_student', 'is_admin_role']
