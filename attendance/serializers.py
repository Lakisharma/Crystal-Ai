from rest_framework import serializers
from accounts.serializers import UserSerializer
from .models import AttendanceSession, AttendanceRecord


class AttendanceRecordSerializer(serializers.ModelSerializer):
    student = UserSerializer(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = ['id', 'student', 'status', 'status_display', 'remarks', 'marked_at']


class AttendanceSessionSerializer(serializers.ModelSerializer):
    total_present = serializers.IntegerField(read_only=True)
    total_records = serializers.IntegerField(read_only=True)
    records = AttendanceRecordSerializer(many=True, read_only=True)

    class Meta:
        model = AttendanceSession
        fields = [
            'id',
            'classroom',
            'date',
            'topic',
            'created_by',
            'total_present',
            'total_records',
            'records',
            'created_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at']
