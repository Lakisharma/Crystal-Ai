from django.contrib import admin
from .models import AttendanceSession, AttendanceRecord


class AttendanceRecordInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ('classroom', 'date', 'topic', 'created_by', 'total_present', 'total_records')
    list_filter = ('classroom', 'date')
    search_fields = ('topic', 'classroom__name', 'classroom__code')
    inlines = [AttendanceRecordInline]


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('session', 'student', 'status', 'marked_at')
    list_filter = ('status', 'session__classroom', 'marked_at')
    search_fields = ('student__username', 'student__email', 'session__topic')
