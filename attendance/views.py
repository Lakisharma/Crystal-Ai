from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import View
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from classrooms.models import Classroom
from .models import AttendanceRecord, AttendanceSession
from .serializers import AttendanceSessionSerializer


class ClassroomAttendanceListView(LoginRequiredMixin, View):
    """
    Shows list of past attendance sessions and metrics for a specific classroom.
    """
    def get(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, id=classroom_id)
        user = request.user
        if not (user.is_admin_role or classroom.teacher == user or classroom.students.filter(id=user.id).exists()):
            raise PermissionDenied("You do not have access to this classroom's attendance.")

        sessions = classroom.attendance_sessions.all()
        is_teacher = (classroom.teacher == user or user.is_admin_role)

        # Student personal record if student
        my_records = []
        attendance_percentage = 0
        if not is_teacher:
            my_records = AttendanceRecord.objects.filter(session__classroom=classroom, student=user)
            total = my_records.count()
            if total > 0:
                present = my_records.filter(status=AttendanceRecord.Status.PRESENT).count()
                attendance_percentage = round((present / total) * 100, 1)

        return render(request, 'attendance/session_list.html', {
            'classroom': classroom,
            'sessions': sessions,
            'is_teacher': is_teacher,
            'my_records': my_records,
            'attendance_percentage': attendance_percentage,
        })


class TakeAttendanceView(LoginRequiredMixin, View):
    """
    Teacher view to log or update attendance for all enrolled students in a session.
    """
    def get(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, id=classroom_id)
        if not (classroom.teacher == request.user or request.user.is_admin_role):
            raise PermissionDenied("Only instructors can take attendance.")

        students = classroom.students.all()
        return render(request, 'attendance/take_attendance.html', {
            'classroom': classroom,
            'students': students,
            'today': timezone.now().date(),
            'status_choices': AttendanceRecord.Status.choices,
        })

    def post(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, id=classroom_id)
        if not (classroom.teacher == request.user or request.user.is_admin_role):
            raise PermissionDenied("Only instructors can take attendance.")

        date_str = request.POST.get('date') or str(timezone.now().date())
        topic = request.POST.get('topic', '').strip()

        with transaction.atomic():
            session = AttendanceSession.objects.create(
                classroom=classroom,
                date=date_str,
                topic=topic,
                created_by=request.user
            )

            students = classroom.students.all()
            for student in students:
                status_val = request.POST.get(f'status_{student.id}', AttendanceRecord.Status.PRESENT)
                remarks_val = request.POST.get(f'remarks_{student.id}', '').strip()

                AttendanceRecord.objects.create(
                    session=session,
                    student=student,
                    status=status_val,
                    remarks=remarks_val
                )

        messages.success(request, f"Attendance saved successfully for {session.records.count()} students.")
        return redirect('attendance:classroom_sessions', classroom_id=classroom.id)


@login_required
def student_attendance_report(request):
    """
    Global attendance summary for a student across all enrolled classes.
    """
    user = request.user
    enrolled_classes = user.enrolled_classes.all()

    class_stats = []
    for cls in enrolled_classes:
        records = AttendanceRecord.objects.filter(session__classroom=cls, student=user)
        total = records.count()
        present = records.filter(status=AttendanceRecord.Status.PRESENT).count()
        late = records.filter(status=AttendanceRecord.Status.LATE).count()
        absent = records.filter(status=AttendanceRecord.Status.ABSENT).count()
        pct = round((present / total) * 100, 1) if total > 0 else 0
        class_stats.append({
            'classroom': cls,
            'total': total,
            'present': present,
            'late': late,
            'absent': absent,
            'percentage': pct,
        })

    return render(request, 'attendance/student_report.html', {
        'class_stats': class_stats,
    })


# DRF API View
class AttendanceSessionListCreateAPI(generics.ListCreateAPIView):
    serializer_class = AttendanceSessionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        classroom_id = self.request.query_params.get('classroom')
        if classroom_id:
            return AttendanceSession.objects.filter(classroom_id=classroom_id)
        return AttendanceSession.objects.none()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
