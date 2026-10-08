from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import (
    Classroom,
    ClassSchedule,
    LiveClass,
    generate_classroom_code,
)
from .scheduling import (
    MIN_CLASS_DURATION_MINUTES,
    MAX_CLASS_DURATION_MINUTES,
    detect_teacher_schedule_conflict,
    make_aware_datetime,
    validate_scheduling_parameters,
)


class ClassroomCreateForm(forms.ModelForm):
    """
    Form for teachers to create new course classrooms.
    """
    code = forms.CharField(
        max_length=10,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Unique 6-char code (leave empty to autogenerate)'})
    )

    class Meta:
        model = Classroom
        fields = ['name', 'subject', 'code', 'status', 'scheduled_at', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Advanced Machine Learning'}),
            'subject': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Artificial Intelligence'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'scheduled_at': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Class overview, prerequisites, expectations...'}),
        }

    def clean_code(self):
        code = self.cleaned_data.get('code')
        if code:
            return code.strip().upper()
        return generate_classroom_code()


class ClassroomJoinForm(forms.Form):
    """
    Form for students to join a classroom using the classroom invite code.
    """
    code = forms.CharField(
        max_length=10,
        required=True,
        label="Classroom Code",
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg text-uppercase text-center fw-bold letter-spacing-1',
            'placeholder': 'ENTER CODE (e.g. CS101A)',
            'autocomplete': 'off',
        })
    )

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()


class ClassScheduleForm(forms.ModelForm):
    class Meta:
        model = ClassSchedule
        fields = ['title', 'day_of_week', 'start_time', 'end_time', 'notes']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Morning Lecture'}),
            'day_of_week': forms.Select(attrs={'class': 'form-select'}),
            'start_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'end_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'notes': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Bring lab notebook'}),
        }


# =====================================================================
# LiveClass CRUD Forms
# =====================================================================

class LiveClassCreateForm(forms.ModelForm):
    """
    Form for teachers to schedule or launch a new LiveClass.
    Supports access modes: PUBLIC_LINK, ENROLLMENT_ONLY, PASSWORD_PROTECTED.
    Supports optional room password (automatically hashed).
    """
    room_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter room password (required if Password Protected mode is selected)',
            'autocomplete': 'new-password'
        }),
        help_text="Required if Password Protected is selected. Students must enter this password to join. Never stored in plain text."
    )

    class Meta:
        model = LiveClass
        fields = [
            'title',
            'subject',
            'description',
            'access_mode',
            'scheduled_date',
            'scheduled_time',
            'duration',
            'max_students',
            'status',
        ]
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control form-control-lg',
                'placeholder': 'e.g. CS401: Distributed Systems & Consensus'
            }),
            'subject': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Computer Science'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Session agenda, discussion topics, and reading materials...'
            }),
            'access_mode': forms.Select(attrs={
                'class': 'form-select',
                'id': 'id_access_mode'
            }),
            'scheduled_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'scheduled_time': forms.TimeInput(attrs={
                'class': 'form-control',
                'type': 'time'
            }),
            'duration': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 10,
                'max': 360,
                'step': 5
            }),
            'max_students': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'max': 500
            }),
            'status': forms.Select(attrs={
                'class': 'form-select'
            }),
        }

    def __init__(self, *args, **kwargs):
        self.teacher = kwargs.pop('teacher', None)
        super().__init__(*args, **kwargs)
        now = timezone.now()
        if 'access_mode' in self.fields:
            self.fields['access_mode'].required = False
        if not self.initial.get('scheduled_date'):
            self.initial['scheduled_date'] = now.date()
        if not self.initial.get('scheduled_time'):
            self.initial['scheduled_time'] = now.strftime('%H:%M')
        if not self.initial.get('duration'):
            self.initial['duration'] = 60

    def clean(self):
        cleaned_data = super().clean()
        access_mode = cleaned_data.get('access_mode')
        room_password = cleaned_data.get('room_password')
        scheduled_date = cleaned_data.get('scheduled_date')
        scheduled_time = cleaned_data.get('scheduled_time')
        duration = cleaned_data.get('duration') or 60

        if not access_mode:
            if room_password:
                access_mode = LiveClass.AccessMode.PASSWORD_PROTECTED
            else:
                access_mode = LiveClass.AccessMode.PUBLIC_LINK
            cleaned_data['access_mode'] = access_mode

        if access_mode == LiveClass.AccessMode.PASSWORD_PROTECTED:
            if not room_password:
                self.add_error('room_password', 'Password is required when access mode is Password Protected.')

        # Duration validation
        if duration is not None:
            if duration < MIN_CLASS_DURATION_MINUTES:
                self.add_error('duration', f"Class duration must be at least {MIN_CLASS_DURATION_MINUTES} minutes.")
            elif duration > MAX_CLASS_DURATION_MINUTES:
                self.add_error('duration', f"Class duration cannot exceed {MAX_CLASS_DURATION_MINUTES} minutes.")

        # Date & Time validation (disallow scheduling in the past)
        if scheduled_date and scheduled_time:
            try:
                validate_scheduling_parameters(scheduled_date, scheduled_time, duration)
            except ValidationError as ve:
                self.add_error('scheduled_date', ve.message if hasattr(ve, 'message') else str(ve))

            # Conflict detection for teacher
            teacher = self.teacher or getattr(self.instance, 'teacher', None)
            if teacher:
                has_conflict, msg, _ = detect_teacher_schedule_conflict(
                    teacher=teacher,
                    scheduled_date=scheduled_date,
                    scheduled_time=scheduled_time,
                    duration=duration
                )
                if has_conflict:
                    self.add_error(None, msg)

        return cleaned_data

    def save(self, commit=True):
        live_class = super().save(commit=False)
        raw_password = self.cleaned_data.get('room_password')
        if raw_password:
            live_class.set_room_password(raw_password)
        if commit:
            live_class.save()
        return live_class


class LiveClassEditForm(forms.ModelForm):
    """
    Form for teachers to edit an existing LiveClass session.
    """
    change_room_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Leave blank to preserve existing password',
            'autocomplete': 'new-password'
        }),
        help_text="Enter a new password to change it, or leave blank to keep unchanged."
    )
    clear_password = forms.BooleanField(
        required=False,
        label="Remove room password protection (make class open to anyone with the code)",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    class Meta:
        model = LiveClass
        fields = [
            'title',
            'subject',
            'description',
            'access_mode',
            'scheduled_date',
            'scheduled_time',
            'duration',
            'max_students',
            'status',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control form-control-lg'}),
            'subject': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'access_mode': forms.Select(attrs={'class': 'form-select', 'id': 'id_access_mode'}),
            'scheduled_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'scheduled_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'duration': forms.NumberInput(attrs={'class': 'form-control', 'min': 5, 'max': 360}),
            'max_students': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 500}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        self.teacher = kwargs.pop('teacher', None)
        super().__init__(*args, **kwargs)
        if 'access_mode' in self.fields:
            self.fields['access_mode'].required = False

    def clean(self):
        cleaned_data = super().clean()
        access_mode = cleaned_data.get('access_mode')
        clear_password = cleaned_data.get('clear_password')
        change_password = cleaned_data.get('change_room_password')
        scheduled_date = cleaned_data.get('scheduled_date')
        scheduled_time = cleaned_data.get('scheduled_time')
        duration = cleaned_data.get('duration') or 60

        # Protect completed and cancelled classes from schedule alteration
        if self.instance and self.instance.pk:
            if self.instance.status in (LiveClass.Status.COMPLETED, 'ENDED'):
                if (scheduled_date != self.instance.scheduled_date or
                    scheduled_time != self.instance.scheduled_time or
                    duration != self.instance.duration):
                    self.add_error(None, "Completed classes cannot be rescheduled or modified.")
            elif self.instance.status == LiveClass.Status.CANCELLED:
                if (scheduled_date != self.instance.scheduled_date or
                    scheduled_time != self.instance.scheduled_time or
                    duration != self.instance.duration):
                    self.add_error(None, "Cancelled classes cannot be rescheduled.")
            elif self.instance.status == LiveClass.Status.LIVE:
                if (scheduled_date != self.instance.scheduled_date or
                    scheduled_time != self.instance.scheduled_time):
                    self.add_error(None, "Live classes cannot be rescheduled while in session.")

        if not access_mode:
            if clear_password:
                access_mode = LiveClass.AccessMode.PUBLIC_LINK
            elif change_password or (self.instance and self.instance.has_password):
                access_mode = getattr(self.instance, 'access_mode', None) or LiveClass.AccessMode.PASSWORD_PROTECTED
            else:
                access_mode = getattr(self.instance, 'access_mode', None) or LiveClass.AccessMode.PUBLIC_LINK
            cleaned_data['access_mode'] = access_mode

        if access_mode == LiveClass.AccessMode.PASSWORD_PROTECTED:
            if clear_password:
                self.add_error('clear_password', 'Cannot clear password while class is in Password Protected mode.')
            elif not self.instance.has_password and not change_password:
                self.add_error('change_room_password', 'Password is required when access mode is set to Password Protected.')

        # Duration validation
        if duration is not None:
            if duration < MIN_CLASS_DURATION_MINUTES:
                self.add_error('duration', f"Class duration must be at least {MIN_CLASS_DURATION_MINUTES} minutes.")
            elif duration > MAX_CLASS_DURATION_MINUTES:
                self.add_error('duration', f"Class duration cannot exceed {MAX_CLASS_DURATION_MINUTES} minutes.")

        # Check date/time past and conflicts if changed
        if scheduled_date and scheduled_time and self.instance:
            date_time_changed = (scheduled_date != self.instance.scheduled_date or scheduled_time != self.instance.scheduled_time)
            if date_time_changed and self.instance.status == LiveClass.Status.SCHEDULED:
                try:
                    validate_scheduling_parameters(scheduled_date, scheduled_time, duration, is_reschedule=True)
                except ValidationError as ve:
                    self.add_error('scheduled_date', ve.message if hasattr(ve, 'message') else str(ve))

            if (date_time_changed or duration != self.instance.duration) and self.instance.status == LiveClass.Status.SCHEDULED:
                teacher = self.teacher or self.instance.teacher
                if teacher:
                    has_conflict, msg, _ = detect_teacher_schedule_conflict(
                        teacher=teacher,
                        scheduled_date=scheduled_date,
                        scheduled_time=scheduled_time,
                        duration=duration,
                        exclude_class_id=self.instance.pk
                    )
                    if has_conflict:
                        self.add_error(None, msg)

        return cleaned_data

    def save(self, commit=True):
        live_class = super().save(commit=False)
        if self.cleaned_data.get('clear_password'):
            live_class.room_password_hash = ''
        else:
            raw_password = self.cleaned_data.get('change_room_password')
            if raw_password:
                live_class.set_room_password(raw_password)
        if commit:
            live_class.save()
        return live_class


class LiveClassRescheduleForm(forms.Form):
    """
    Dedicated form for teachers to reschedule a SCHEDULED LiveClass session.
    """
    scheduled_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        label="New Date"
    )
    scheduled_time = forms.TimeField(
        widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
        label="New Start Time"
    )
    duration = forms.IntegerField(
        min_value=MIN_CLASS_DURATION_MINUTES,
        max_value=MAX_CLASS_DURATION_MINUTES,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': MIN_CLASS_DURATION_MINUTES, 'max': MAX_CLASS_DURATION_MINUTES, 'step': 5}),
        label="Duration (minutes)",
        initial=60
    )
    update_summary = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional explanation for students (e.g. Rescheduled due to faculty seminar)'}),
        label="Reason / Note to Students (Optional)"
    )
