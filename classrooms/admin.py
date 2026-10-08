from django.contrib import admin
from .models import (
    Classroom,
    ClassSchedule,
    LiveClass,
    ClassParticipant,
    Attendance,
    ChatMessage,
)


class ClassScheduleInline(admin.TabularInline):
    model = ClassSchedule
    extra = 1


@admin.register(Classroom)
class ClassroomAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'subject', 'teacher', 'status', 'student_count', 'is_active', 'created_at')
    list_filter = ('is_active', 'status', 'subject', 'created_at')
    search_fields = ('name', 'code', 'subject', 'teacher__username', 'teacher__first_name', 'teacher__last_name')
    inlines = [ClassScheduleInline]
    filter_horizontal = ('students',)


@admin.register(ClassSchedule)
class ClassScheduleAdmin(admin.ModelAdmin):
    list_display = ('classroom', 'title', 'day_of_week', 'start_time', 'end_time')
    list_filter = ('day_of_week', 'classroom')


class ClassParticipantInline(admin.TabularInline):
    model = ClassParticipant
    extra = 0
    readonly_fields = ('join_time', 'leave_time')


class LiveAttendanceInline(admin.TabularInline):
    model = Attendance
    extra = 0


@admin.register(LiveClass)
class LiveClassAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'room_code',
        'subject',
        'teacher',
        'status',
        'scheduled_date',
        'scheduled_time',
        'duration',
        'max_students',
        'created_at',
    )
    list_filter = ('status', 'subject', 'scheduled_date')
    search_fields = ('title', 'room_code', 'subject', 'teacher__username')
    readonly_fields = ('room_code', 'created_at', 'started_at', 'ended_at')
    inlines = [ClassParticipantInline, LiveAttendanceInline]


@admin.register(ClassParticipant)
class ClassParticipantAdmin(admin.ModelAdmin):
    list_display = ('student_name', 'student_email', 'live_class', 'status', 'join_time', 'leave_time')
    list_filter = ('status', 'live_class')
    search_fields = ('student_name', 'student_email', 'live_class__room_code', 'live_class__title')


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('student_name', 'live_class', 'joined_at', 'left_at', 'total_duration')
    list_filter = ('live_class',)
    search_fields = ('student_name', 'live_class__title', 'live_class__room_code')


@admin.register(ChatMessage)
class LiveChatMessageAdmin(admin.ModelAdmin):
    list_display = ('live_class', 'sender_name', 'sender_role', 'message_snippet', 'created_at')
    list_filter = ('sender_role', 'live_class', 'created_at')
    search_fields = ('sender_name', 'message', 'live_class__room_code')

    def message_snippet(self, obj):
        return obj.message[:50]
    message_snippet.short_description = "Message"
