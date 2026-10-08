import calendar
from datetime import datetime, date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView, View
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import TeacherRequiredMixin, teacher_required
from .forms import (
    ClassroomCreateForm,
    ClassroomJoinForm,
    ClassScheduleForm,
    LiveClassCreateForm,
    LiveClassEditForm,
    LiveClassRescheduleForm,
)
from .models import (
    Classroom,
    ClassSchedule,
    LiveClass,
    ClassParticipant,
    Attendance,
    ChatMessage,
)
from .scheduling import (
    can_student_join_class,
    can_teacher_start_class,
    cancel_live_class,
    get_class_status,
    reschedule_live_class,
)
from .serializers import ClassroomSerializer
from notifications.services import NotificationService


# =====================================================================
# Course Classroom Views (Preserved for backwards-compatibility)
# =====================================================================

class ClassroomListView(LoginRequiredMixin, ListView):
    model = Classroom
    template_name = 'classrooms/classroom_list.html'
    context_object_name = 'classrooms'

    def get_queryset(self):
        user = self.request.user
        if user.is_teacher:
            return Classroom.objects.filter(teacher=user)
        elif user.is_student:
            return Classroom.objects.filter(students=user)
        return Classroom.objects.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['join_form'] = ClassroomJoinForm()
        return context


class ClassroomDetailView(LoginRequiredMixin, DetailView):
    model = Classroom
    template_name = 'classrooms/classroom_detail.html'
    context_object_name = 'classroom'

    def get_object(self, queryset=None):
        classroom = super().get_object(queryset)
        user = self.request.user

        if user.is_admin_role:
            return classroom

        if user.is_teacher:
            if classroom.teacher != user:
                raise PermissionDenied("Access Denied: A teacher must only access their own classes.")
            return classroom

        if user.is_student:
            if not classroom.students.filter(id=user.id).exists():
                raise PermissionDenied("Access Denied: You are not enrolled in this classroom.")
            return classroom

        raise PermissionDenied("Access Denied: You do not have permission to view this classroom.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        classroom = self.get_object()
        context['schedules'] = classroom.schedules.all()
        context['students'] = classroom.students.all()
        context['schedule_form'] = ClassScheduleForm()
        context['is_teacher'] = (classroom.teacher == self.request.user or self.request.user.is_admin_role)
        return context


class ClassroomCreateView(TeacherRequiredMixin, CreateView):
    model = Classroom
    form_class = ClassroomCreateForm
    template_name = 'classrooms/classroom_form.html'

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get('live') == '1':
            initial['status'] = Classroom.Status.LIVE
        return initial

    def form_valid(self, form):
        form.instance.teacher = self.request.user
        classroom = form.save()
        messages.success(
            self.request,
            f"Classroom '{classroom.name}' created with code: {classroom.code} (Status: {classroom.get_status_display()})"
        )
        return redirect(reverse('classrooms:detail', kwargs={'pk': classroom.pk}))


@teacher_required
def toggle_classroom_status(request, pk):
    classroom = get_object_or_404(Classroom, pk=pk)
    if classroom.teacher != request.user and not request.user.is_admin_role:
        raise PermissionDenied("A teacher must only access their own classes.")

    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in Classroom.Status.values:
            classroom.status = new_status
            classroom.save()
            messages.success(request, f"Class status updated to: {classroom.get_status_display()}")
        else:
            messages.error(request, "Invalid status choice.")

    return redirect(request.META.get('HTTP_REFERER', reverse('classrooms:detail', kwargs={'pk': classroom.pk})))


class ClassroomJoinView(LoginRequiredMixin, View):
    def post(self, request):
        form = ClassroomJoinForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data['code']
            try:
                classroom = Classroom.objects.get(code__iexact=code, is_active=True)
                if classroom.students.filter(id=request.user.id).exists():
                    messages.info(request, f"You are already enrolled in '{classroom.name}'.")
                elif classroom.teacher == request.user:
                    messages.warning(request, "You are the instructor of this classroom!")
                else:
                    classroom.students.add(request.user)
                    messages.success(request, f"Successfully enrolled in '{classroom.name}'!")
                return redirect(reverse('classrooms:detail', kwargs={'pk': classroom.pk}))
            except Classroom.DoesNotExist:
                messages.error(request, f"No active classroom found with code '{code}'. Please check and try again.")
        else:
            messages.error(request, "Invalid classroom code.")
        return redirect('classrooms:list')


@login_required
def add_schedule_view(request, pk):
    classroom = get_object_or_404(Classroom, pk=pk)
    if not (classroom.teacher == request.user or request.user.is_admin_role):
        raise PermissionDenied("A teacher must only access their own classes.")
    if request.method == 'POST':
        form = ClassScheduleForm(request.POST)
        if form.is_valid():
            schedule = form.save(commit=False)
            schedule.classroom = classroom
            schedule.save()
            messages.success(request, "Class schedule slot added.")
    return redirect('classrooms:detail', pk=classroom.pk)


# =====================================================================
# Complete Teacher CRUD Views for LiveClass
# =====================================================================

class LiveClassListView(TeacherRequiredMixin, ListView):
    """
    List classes:
    - Lists only classes owned by the logged-in teacher.
    - Supports status filtering and search query.
    - Shows class counts across statuses.
    """
    model = LiveClass
    template_name = 'classrooms/liveclass_list.html'
    context_object_name = 'classes'

    def get_queryset(self):
        user = self.request.user
        qs = user.live_classes.all()

        # Status filter
        status_filter = self.request.GET.get('status')
        if status_filter and status_filter.upper() in LiveClass.Status.values:
            qs = qs.filter(status=status_filter.upper())

        # Search query
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(title__icontains=q) |
                Q(subject__icontains=q) |
                Q(room_code__icontains=q)
            )

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_classes = self.request.user.live_classes.all()
        context['total_count'] = all_classes.count()
        context['scheduled_count'] = all_classes.filter(status=LiveClass.Status.SCHEDULED).count()
        context['live_count'] = all_classes.filter(status=LiveClass.Status.LIVE).count()
        context['completed_count'] = all_classes.filter(status=LiveClass.Status.COMPLETED).count()
        context['cancelled_count'] = all_classes.filter(status=LiveClass.Status.CANCELLED).count()
        context['current_status'] = self.request.GET.get('status', 'ALL').upper()
        context['search_query'] = self.request.GET.get('q', '')
        return context


class LiveClassCreateView(TeacherRequiredMixin, CreateView):
    """
    Create class:
    - Automatically assigns current teacher.
    - Automatically generates secure random room_code.
    - Hashes room password if provided (never plain text).
    """
    model = LiveClass
    form_class = LiveClassCreateForm
    template_name = 'classrooms/liveclass_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['teacher'] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get('live') == '1':
            initial['status'] = LiveClass.Status.LIVE
        return initial

    def form_valid(self, form):
        form.instance.teacher = self.request.user
        live_class = form.save()
        try:
            NotificationService.notify_class_created(live_class)
        except Exception:
            pass
        messages.success(
            self.request,
            f"Live Class '{live_class.title}' created successfully! Room Code: {live_class.room_code}"
        )
        return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))


class LiveClassDetailView(LoginRequiredMixin, DetailView):
    """
    View class:
    - Displays full details, room code, participants, attendance, and chat messages.
    - Strictly enforces teacher ownership: Teacher B cannot view Teacher A's class.
    """
    model = LiveClass
    template_name = 'classrooms/liveclass_detail.html'
    context_object_name = 'live_class'

    def get_object(self, queryset=None):
        live_class = super().get_object(queryset)
        user = self.request.user

        if user.is_admin_role:
            return live_class

        # Teacher ownership check
        if user.is_teacher and live_class.teacher != user:
            raise PermissionDenied("Access Denied: A teacher must only access their own classes.")

        return live_class

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        live_class = self.get_object()
        context['participants'] = live_class.participants.all()[:50]
        context['attendances'] = live_class.attendances.all()[:50]
        context['chat_messages'] = live_class.chat_messages.all()[:100]
        context['is_owner'] = (live_class.teacher == self.request.user or self.request.user.is_admin_role)
        return context


class LiveClassEditView(TeacherRequiredMixin, UpdateView):
    """
    Edit class:
    - Allows updating title, subject, schedule, duration, max_students, password.
    - Strictly enforces teacher ownership.
    """
    model = LiveClass
    form_class = LiveClassEditForm
    template_name = 'classrooms/liveclass_edit.html'
    context_object_name = 'live_class'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['teacher'] = self.request.user
        return kwargs

    def get_object(self, queryset=None):
        live_class = super().get_object(queryset)
        if live_class.teacher != self.request.user and not self.request.user.is_admin_role:
            raise PermissionDenied("Access Denied: A teacher must only access their own classes.")
        return live_class

    def form_valid(self, form):
        live_class = form.save()
        try:
            NotificationService.notify_class_updated(live_class)
        except Exception:
            pass
        messages.success(self.request, f"Class '{live_class.title}' updated successfully!")
        return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))


class LiveClassRescheduleView(TeacherRequiredMixin, View):
    """
    Reschedule class:
    - GET: Form to select new date, time, duration, and optional explanation note.
    - POST: Validates schedule, conflict detection, updates schedule, notifies students, logs audit.
    """
    def get(self, request, pk):
        live_class = get_object_or_404(LiveClass, pk=pk)
        if live_class.teacher != request.user and not request.user.is_admin_role:
            raise PermissionDenied("Access Denied: A teacher must only access their own classes.")

        if live_class.status == LiveClass.Status.LIVE:
            messages.warning(request, "Live classes cannot be rescheduled while in progress.")
            return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))

        if live_class.status in (LiveClass.Status.COMPLETED, 'ENDED') or live_class.is_ended:
            messages.info(request, "Completed classes cannot be rescheduled.")
            return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))

        if live_class.status == LiveClass.Status.CANCELLED:
            messages.info(request, "Cancelled classes cannot be rescheduled.")
            return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))

        form = LiveClassRescheduleForm(initial={
            'scheduled_date': live_class.scheduled_date,
            'scheduled_time': live_class.scheduled_time.strftime('%H:%M') if live_class.scheduled_time else '',
            'duration': live_class.duration,
        })
        return render(request, 'classrooms/liveclass_reschedule.html', {
            'live_class': live_class,
            'form': form,
            'page_title': f"Reschedule Class: {live_class.title} - TeachLive",
        })

    def post(self, request, pk):
        live_class = get_object_or_404(LiveClass, pk=pk)
        if live_class.teacher != request.user and not request.user.is_admin_role:
            raise PermissionDenied("Access Denied: A teacher must only access their own classes.")

        form = LiveClassRescheduleForm(request.POST)
        if form.is_valid():
            new_date = form.cleaned_data['scheduled_date']
            new_time = form.cleaned_data['scheduled_time']
            new_duration = form.cleaned_data['duration']
            update_summary = form.cleaned_data.get('update_summary', '')

            success, msg = reschedule_live_class(
                live_class=live_class,
                new_date=new_date,
                new_time=new_time,
                new_duration=new_duration,
                actor=request.user,
                update_summary=update_summary,
                request=request
            )
            if success:
                messages.success(request, msg)
                return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))
            else:
                form.add_error(None, msg)

        return render(request, 'classrooms/liveclass_reschedule.html', {
            'live_class': live_class,
            'form': form,
            'page_title': f"Reschedule Class: {live_class.title} - TeachLive",
        })


class LiveClassCancelView(TeacherRequiredMixin, View):
    """
    Cancel class:
    - GET: Shows confirmation page.
    - POST: Cancels the scheduled class via cancel_live_class().
    - Strictly enforces teacher ownership.
    """
    def get(self, request, pk):
        live_class = get_object_or_404(LiveClass, pk=pk)
        if live_class.teacher != request.user and not request.user.is_admin_role:
            raise PermissionDenied("Access Denied: A teacher must only access their own classes.")

        if live_class.status in (LiveClass.Status.COMPLETED, 'ENDED') or live_class.is_ended:
            messages.info(request, "Completed classes cannot be cancelled.")
            return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))

        if live_class.status == LiveClass.Status.CANCELLED:
            messages.info(request, "This class is already cancelled.")
            return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))

        if live_class.status == LiveClass.Status.LIVE:
            messages.warning(request, "Cannot cancel a class that is currently LIVE. Please end the class instead.")
            return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))

        return render(request, 'classrooms/liveclass_confirm_cancel.html', {
            'live_class': live_class,
            'page_title': f"Cancel Class: {live_class.title} - TeachLive",
        })

    def post(self, request, pk):
        live_class = get_object_or_404(LiveClass, pk=pk)
        if live_class.teacher != request.user and not request.user.is_admin_role:
            raise PermissionDenied("Access Denied: A teacher must only access their own classes.")

        reason = request.POST.get('reason', '').strip()
        success, msg = cancel_live_class(live_class, request.user, reason=reason, request=request)
        if success:
            messages.warning(request, msg)
        else:
            messages.error(request, msg)
        return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))


class LiveClassDeleteView(TeacherRequiredMixin, DeleteView):
    """
    Delete class:
    - Deletes the class from database.
    - Strictly enforces teacher ownership.
    """
    model = LiveClass
    template_name = 'classrooms/liveclass_confirm_delete.html'
    context_object_name = 'live_class'
    success_url = reverse_lazy('classrooms:live_list')

    def get_object(self, queryset=None):
        live_class = super().get_object(queryset)
        if live_class.teacher != self.request.user and not self.request.user.is_admin_role:
            raise PermissionDenied("Access Denied: A teacher must only access their own classes.")
        return live_class

    def form_valid(self, form):
        messages.success(self.request, f"Class '{self.object.title}' was deleted successfully.")
        return super().form_valid(form)


class LiveClassStartView(TeacherRequiredMixin, View):
    """
    Start class: Sets status to LIVE and records started_at.
    Enforces start-window rules server-side.
    """
    def post(self, request, pk):
        live_class = get_object_or_404(LiveClass, pk=pk)
        if live_class.teacher != request.user and not request.user.is_admin_role:
            raise PermissionDenied("Access Denied: A teacher must only access their own classes.")

        can_start, start_reason, _ = can_teacher_start_class(live_class, request.user)
        if not can_start:
            messages.warning(request, start_reason)
            return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))

        live_class.start_class()
        try:
            NotificationService.notify_class_started(live_class)
        except Exception:
            pass
        messages.success(request, f"Class '{live_class.title}' is now LIVE! Room Code: {live_class.room_code}")
        return redirect(reverse('classrooms:teacher_class_live', kwargs={'class_id': live_class.pk}))


class TeacherClassesListView(TeacherRequiredMixin, View):
    """
    Teacher Scheduled Classes Management Page at /teacher/classes/.
    Organizes classes into:
    - TODAY: Classes scheduled for today
    - UPCOMING: Classes scheduled for the future
    - PAST: Completed or past scheduled classes
    - CANCELLED: Cancelled classes
    - ALL: Complete list
    Supports search query, status filter, date filter, subject filter, and pagination.
    """
    def get(self, request):
        user = request.user
        tab = request.GET.get('tab', 'all').strip().lower()
        search_query = request.GET.get('q', '').strip()
        status_filter = request.GET.get('status', '').strip().upper()
        date_filter = request.GET.get('date', '').strip()
        subject_filter = request.GET.get('subject', '').strip()

        now = timezone.now()
        today = now.date()

        base_qs = user.live_classes.all().order_by('-scheduled_date', '-scheduled_time')

        all_classes = user.live_classes.all()
        today_count = all_classes.filter(scheduled_date=today).count()
        live_count = all_classes.filter(status=LiveClass.Status.LIVE).count()
        scheduled_count = all_classes.filter(status=LiveClass.Status.SCHEDULED).count()
        upcoming_count = all_classes.filter(
            Q(scheduled_date__gt=today) |
            Q(scheduled_date=today, status__in=[LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE])
        ).filter(status__in=[LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE]).count()
        completed_count = all_classes.filter(
            Q(status__in=[LiveClass.Status.COMPLETED, 'ENDED']) |
            Q(scheduled_date__lt=today, status=LiveClass.Status.SCHEDULED)
        ).count()
        past_count = completed_count
        cancelled_count = all_classes.filter(status=LiveClass.Status.CANCELLED).count()

        # Tab filtering
        if tab == 'today':
            qs = base_qs.filter(scheduled_date=today).order_by('scheduled_time')
        elif tab == 'live':
            qs = base_qs.filter(status=LiveClass.Status.LIVE).order_by('-started_at')
        elif tab == 'scheduled':
            qs = base_qs.filter(status=LiveClass.Status.SCHEDULED).order_by('scheduled_date', 'scheduled_time')
        elif tab == 'upcoming':
            qs = base_qs.filter(
                Q(scheduled_date__gt=today) |
                Q(scheduled_date=today, status__in=[LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE])
            ).filter(status__in=[LiveClass.Status.SCHEDULED, LiveClass.Status.LIVE]).order_by('scheduled_date', 'scheduled_time')
        elif tab in ['completed', 'past']:
            qs = base_qs.filter(
                Q(status__in=[LiveClass.Status.COMPLETED, 'ENDED']) |
                Q(scheduled_date__lt=today, status=LiveClass.Status.SCHEDULED)
            )
        elif tab == 'cancelled':
            qs = base_qs.filter(status=LiveClass.Status.CANCELLED)
        else:
            qs = base_qs

        # Search Query
        if search_query:
            qs = qs.filter(
                Q(title__icontains=search_query) |
                Q(subject__icontains=search_query) |
                Q(room_code__icontains=search_query)
            )

        # Status Filter
        if status_filter and status_filter in LiveClass.Status.values:
            qs = qs.filter(status=status_filter)

        # Date Filter
        if date_filter:
            try:
                parsed_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
                qs = qs.filter(scheduled_date=parsed_date)
            except ValueError:
                pass

        # Subject Filter
        if subject_filter:
            qs = qs.filter(subject__iexact=subject_filter)

        all_subjects = all_classes.values_list('subject', flat=True).distinct().order_by('subject')

        # Pagination
        paginator = Paginator(qs, 15)
        page_num = request.GET.get('page', 1)
        try:
            classes_page = paginator.page(page_num)
        except (PageNotAnInteger, EmptyPage):
            classes_page = paginator.page(1)

        return render(request, 'classrooms/teacher_classes_list.html', {
            'classes': classes_page,
            'active_tab': tab,
            'search_query': search_query,
            'selected_status': status_filter,
            'selected_date': date_filter,
            'selected_subject': subject_filter,
            'all_subjects': all_subjects,
            'today_count': today_count,
            'live_count': live_count,
            'scheduled_count': scheduled_count,
            'upcoming_count': upcoming_count,
            'completed_count': completed_count,
            'past_count': past_count,
            'cancelled_count': cancelled_count,
            'total_count': all_classes.count(),
            'page_title': 'Scheduled Classes - TeachLive Instructor',
        })


class TeacherCalendarView(TeacherRequiredMixin, View):
    """
    Teacher Interactive Calendar View at /teacher/calendar/.
    Displays monthly schedule with:
    - Month view, Previous month, Next month, Today button
    - Daily scheduled classes with status badges, start times, and durations
    - Direct links to class details and live classrooms.
    """
    def get(self, request):
        user = request.user
        now = timezone.now()

        try:
            year = int(request.GET.get('year', now.year))
            month = int(request.GET.get('month', now.month))
            if month < 1 or month > 12:
                raise ValueError
        except (ValueError, TypeError):
            year = now.year
            month = now.month

        if month == 1:
            prev_month = 12
            prev_year = year - 1
        else:
            prev_month = month - 1
            prev_year = year

        if month == 12:
            next_month = 1
            next_year = year + 1
        else:
            next_month = month + 1
            next_year = year

        month_name = calendar.month_name[month]

        month_classes = user.live_classes.filter(
            scheduled_date__year=year,
            scheduled_date__month=month
        ).order_by('scheduled_time')

        classes_by_day = {}
        for c in month_classes:
            d = c.scheduled_date.day
            if d not in classes_by_day:
                classes_by_day[d] = []
            classes_by_day[d].append(c)

        cal = calendar.Calendar(firstweekday=6)  # Sunday
        month_weeks = cal.monthdayscalendar(year, month)

        return render(request, 'classrooms/teacher_calendar.html', {
            'year': year,
            'month': month,
            'month_name': month_name,
            'prev_year': prev_year,
            'prev_month': prev_month,
            'next_year': next_year,
            'next_month': next_month,
            'today': now.date(),
            'month_weeks': month_weeks,
            'classes_by_day': classes_by_day,
            'total_month_classes': month_classes.count(),
            'page_title': f"{month_name} {year} Schedule - TeachLive Instructor Calendar",
        })


class LiveClassEndView(TeacherRequiredMixin, View):
    """
    End class: Sets status to COMPLETED and records ended_at.
    """
    def post(self, request, pk):
        live_class = get_object_or_404(LiveClass, pk=pk)
        if live_class.teacher != request.user and not request.user.is_admin_role:
            raise PermissionDenied("Access Denied: A teacher must only access their own classes.")

        live_class.end_class()
        try:
            NotificationService.notify_class_ended(live_class)
        except Exception:
            pass
        messages.info(request, f"Class '{live_class.title}' has concluded.")
        return redirect(reverse('classrooms:live_detail', kwargs={'pk': live_class.pk}))


# =====================================================================
# DRF API Views
# =====================================================================

class ClassroomListCreateAPI(generics.ListCreateAPIView):
    serializer_class = ClassroomSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_teacher:
            return Classroom.objects.filter(teacher=user)
        elif user.is_student:
            return Classroom.objects.filter(students=user)
        return Classroom.objects.all()

    def perform_create(self, serializer):
        serializer.save(teacher=self.request.user)


class ClassroomDetailAPI(generics.RetrieveAPIView):
    serializer_class = ClassroomSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Classroom.objects.all()


class ClassroomJoinAPI(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        code = request.data.get('code', '').strip().upper()
        if not code:
            return Response({'error': 'Classroom code is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            classroom = Classroom.objects.get(code__iexact=code, is_active=True)
            classroom.students.add(request.user)
            return Response({
                'message': f"Enrolled in {classroom.name}",
                'classroom': ClassroomSerializer(classroom).data
            }, status=status.HTTP_200_OK)
        except Classroom.DoesNotExist:
            return Response({'error': 'Classroom not found.'}, status=status.HTTP_404_NOT_FOUND)
