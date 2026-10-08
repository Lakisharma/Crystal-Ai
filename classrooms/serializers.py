from rest_framework import serializers
from accounts.serializers import UserSerializer
from .models import Classroom, ClassSchedule


class ClassScheduleSerializer(serializers.ModelSerializer):
    day_name = serializers.CharField(source='get_day_of_week_display', read_only=True)

    class Meta:
        model = ClassSchedule
        fields = ['id', 'title', 'day_of_week', 'day_name', 'start_time', 'end_time', 'notes']


class ClassroomSerializer(serializers.ModelSerializer):
    teacher = UserSerializer(read_only=True)
    student_count = serializers.IntegerField(read_only=True)
    schedules = ClassScheduleSerializer(many=True, read_only=True)

    class Meta:
        model = Classroom
        fields = [
            'id',
            'name',
            'code',
            'subject',
            'description',
            'teacher',
            'student_count',
            'is_active',
            'schedules',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'code', 'student_count', 'created_at', 'updated_at']
