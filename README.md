# TeachLive — Virtual Live Classroom Platform

TeachLive is a modern, high-performance web application designed for real-time online teaching, live WebRTC video/audio conferencing, interactive chat, attendance logging, in-app notifications, transactional email dispatch, and secure role-based administration.

---

## 1. Core Architecture Overview

- **Authentication (`accounts`)**: Role-based access control (`TEACHER`, `STUDENT`, `ADMIN`), student Google OAuth 2.0 / OpenID Connect, account activation/deactivation.
- **Classrooms & WebRTC (`classrooms`)**: LiveKit WebRTC video, screen share, and audio conferencing; room access controls and password hashing.
- **Attendance (`attendance`)**: Real-time join/leave tracking, active presence verification, duration computation, and CSV export.
- **Real-Time Chat (`chat`)**: Live classroom messaging with server-side moderation and message history.
- **Admin Dashboard (`accounts.admin_dashboard`)**: Dedicated `/admin-dashboard/` portal for managing teachers, students, live classes, attendance, chat reports, audit logs, and communications.
- **Notifications & Email (`notifications`)**: Comprehensive in-app notification center (`/notifications/`), transactional HTML email dispatch, delivery audit logs, and class reminder services.

---

## 2. Notification & Transactional Email System

### 2.1 Notification System Architecture
The `Notification` model records in-app alerts for users:
- **Notification Types**:
  - `CLASS_CREATED`: Notifies teacher confirming class details and room code.
  - `CLASS_SCHEDULED`: Informs participants about scheduled sessions.
  - `CLASS_STARTING`: Upcoming class reminders (10–30 mins before start).
  - `CLASS_STARTED`: Real-time alert when a teacher launches a live session.
  - `CLASS_ENDED`: Concluding session alert.
  - `CLASS_CANCELLED`: Cancellation alert when a teacher or administrator cancels a scheduled class.
  - `STUDENT_JOINED`: Notifies instructor when a student enters the live classroom.
  - `TEACHER_CLASS_UPDATE`: Alerts affected students and instructor when schedules change.
  - `SYSTEM`: Platform-wide administrative announcements.
- **Deduplication Engine**: Built-in sliding time-window checks prevent spam or duplicate alerts caused by WebSocket reconnects or rapid page refreshes.
- **Security & Isolation**: Users can only view and update their own notifications. All read state changes require `POST` with CSRF protection (`GET` requests return HTTP 405).

### 2.2 Transactional Email Architecture
The `EmailService` manages all outgoing email communications:
- **Templates**: Mobile-friendly, responsive HTML templates branded with TeachLive design tokens ("Teach Live. Learn Live.") and fallback plain-text rendering.
- **Non-blocking Dispatch**: Email dispatch runs with safe exception isolation; temporary SMTP or network failures will never crash user registrations, Google OAuth logins, or class creation workflows.
- **Audit Logging**: Every outgoing email is recorded in the `EmailLog` database table (`SENT` / `FAILED`), capturing recipient, type, subject, timestamp, and sanitized error summaries without storing secrets or passwords.

### 2.3 Upcoming Class Reminder Service
A decoupled `ReminderService` evaluates upcoming classes scheduled within a target forward window (e.g., 30 minutes):
- Can be invoked manually or via management command:
  ```bash
  python manage.py send_class_reminders --window 30
  ```
- **Production Setup**:
  - **Linux Cron Job**: Add to crontab to run every 10–15 minutes:
    ```cron
    */10 * * * * /path/to/venv/bin/python /path/to/project/manage.py send_class_reminders --window 30 >> /var/log/teachlive_reminders.log 2>&1
    ```
  - **Celery Beat**: Call `ReminderService.send_upcoming_class_reminders(window_minutes=30)` periodically.

---

## 3. Configuration & Environment Variables

Copy `.env.example` to `.env` and configure your settings:

```ini
# Django Settings
SECRET_KEY=your-secure-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,[::1]

# Database Configuration (SQLite default; PostgreSQL in production)
DATABASE_URL=sqlite:///db.sqlite3

# Google OAuth 2.0 / OpenID Connect (Student Portal)
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret

# LiveKit WebRTC Configuration (Video/Audio)
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-livekit-api-key
LIVEKIT_API_SECRET=your-livekit-api-secret

# Email Configuration
# Development (Console Output):
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend

# Production SMTP (e.g. Gmail, SendGrid, Amazon SES):
# EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# EMAIL_HOST=smtp.gmail.com
# EMAIL_PORT=587
# EMAIL_USE_TLS=True
# EMAIL_HOST_USER=your-email@example.com
# EMAIL_HOST_PASSWORD=your-app-password
# DEFAULT_FROM_EMAIL=TeachLive <no-reply@teachlive.edu>
```

### 3.1 Local Email Testing
During local development, emails are printed directly to the terminal when `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` is set.

### 3.2 Production SMTP Setup
1. Set `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend` in `.env`.
2. Configure `EMAIL_HOST`, `EMAIL_PORT=587`, `EMAIL_USE_TLS=True`.
3. Provide SMTP credentials via `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD`.

---

## 4. Running the Application Locally

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run database migrations
python manage.py migrate

# 3. Seed demo data (optional)
python manage.py seed_data

# 4. Start development server
python manage.py runserver
```

---

## 5. Automated Testing

Run the automated test suites:

```powershell
# Run notifications and email test suite
python manage.py test notifications --keepdb

# Run admin dashboard test suite
python manage.py test accounts.test_admin_dashboard --keepdb

# Run full project test suite
python manage.py test --keepdb

# Run Prompt #10 Secure Enrollment & Access Control tests
python manage.py test classrooms.test_enrollment_access --keepdb
```

---

## 6. Secure Class Enrollment & Access Control (Prompt #10)

TeachLive features an enterprise-grade multi-mode class access and authorization architecture:

### 6.1 Class Access Modes
Instructors configure access permissions when creating or editing a `LiveClass`:
1. **`PUBLIC_LINK` (Public Link)**:
   - Any student with the room link may join.
   - Enforces student authentication, active account status, class live/started status, and concurrent participant capacity limits.
2. **`ENROLLMENT_ONLY` (Enrollment Only)**:
   - Strictly limited to explicitly invited or approved students (`ClassEnrollment`).
   - Unenrolled students are presented with an "Enrollment Required" screen and can submit a structured "Request Access" note.
   - Private classes are omitted from unauthorized students' dashboards.
3. **`PASSWORD_PROTECTED` (Password Protected)**:
   - Students must authenticate and enter the room password.
   - Room passwords are cryptographically hashed using PBKDF2/Django hasher and never exposed in HTML, API, notifications, or emails.

### 6.2 Student Enrollment & Access Requests
- **Instructor Student Management**: Instructors access `/teacher/classes/<room_code>/students/` to search students by name/email, directly enroll or send invitations, review pending access requests with 1-click Approve/Reject, and revoke or restore access.
- **Concurrent Capacity Control**: Prevents join flooding beyond `max_students` by computing active concurrent participants server-side without double-counting reconnecting students.
- **Revocation mid-session**: LiveKit token generation and session heartbeats verify active enrollment status on every heartbeat cycle, rejecting revoked sessions immediately with HTTP 403.
- **Admin Dashboard Integration**: Administrators can audit, revoke enrollments, and approve/reject access requests from `/admin-dashboard/classes/<id>/` with all actions logged to `AdminAuditLog`.

---

## 7. Live Classroom Moderation & Participant Controls (Prompt #11)

TeachLive live classroom supports rich in-room moderation and student participation controls with complete server-side authorization:

### 7.1 Teacher Host Controls & Moderation
- **Participant Roster**: Live panel displaying student names, initials avatar, mic status (`On` / `Off`), camera status (`On` / `Off`), connection state (`Connected`, `Reconnecting`, `Poor Connection`), raised hand status (`✋ Raised Hand`), and join times.
- **Live Participant Count**: Displays real-time attendees ("LIVE 12 Participants") updated via LiveKit events and heartbeat polling, deduplicating reconnects.
- **Remote Mute**: Instructors can remotely mute any attendee microphone. Sends structured `participant_muted` data packet to target client, immediately unpublishing audio and showing an in-room notice.
- **Remove Participant with Confirmation**: Removing a student requires a confirmation modal with an optional reason dropdown (`Disruptive behavior`, `Technical issue`, `Classroom rule violation`, `Other`).
- **Lower Raised Hand**: Instructors can lower any student's raised hand with 1 click.
- **Moderation History**: In-room collapsible history card listing all moderation actions taken during the live session with timestamps and reasons.

### 7.2 Student In-Room Controls
- **Microphone & Camera Toggle**: Students can publish audio and video when allowed, toggling their mic and camera with dedicated toolbar buttons.
- **✋ Raise Hand / Lower Hand**: Students toggle their raised hand state. Broadcasts `hand_raised` / `hand_lowered` events in real time.
- **Hand-Raised Priority Ordering**: Students with raised hands automatically float to the top of the participant list (ordered by `hand_raised_at`).
- **Audio Output & Fullscreen**: Independent incoming volume toggle and stage fullscreen controls.
- **Connection Diagnostics**: Non-blocking banner alerts students when network quality degrades (`Poor connection • reconnecting...`) without terminating the session prematurely.

### 7.3 Session-Level Removal & Security Architecture
- **Non-destructive Session Block**: Student removals are recorded in `LiveClassParticipantModeration(action='REMOVED', active=True)`. The student's permanent `ClassEnrollment` remains intact, but re-entry, token generation, and heartbeats for this active session are blocked with HTTP 403.
- **Removed Student Screen**: Disconnected students are immediately routed to `/live/<room_code>/removed/` displaying a friendly notice ("You have been removed from this live class by the teacher.") with a "Return to Dashboard" action, preventing automated reconnect loops.
- **Attendance Integration**: Removals automatically stamp `leave_time` on `ClassParticipant` and close active `Attendance` records (`status=LEFT`, `total_duration` calculated server-side). Reconnects update existing sessions without creating duplicate rows.
- **Server-Side Authorization**: Every action on `/live/<room_code>/moderate/` and `/live/<room_code>/hand/` verifies user identity, role, class ownership, and class status. Students can never moderate other participants.

### 7.4 Admin Monitoring
- Administrators view all classroom moderation events (`MUTED`, `REMOVED`, `HAND_RAISED`, `HAND_LOWERED`) in `/admin-dashboard/classes/<id>/` under the **Classroom Moderation Audit Log** table.

### 7.5 Automated Test Commands
```bash
# Run Prompt #11 Moderation Test Suite (21 tests)
python manage.py test classrooms.test_moderation

# Run Prompt #10 Secure Enrollment Test Suite (24 tests)
python manage.py test classrooms.test_enrollment_access --keepdb

# Run complete regression suite
python manage.py test --keepdb
```

---

## 8. Class Scheduling, Calendar & Automatic Status Management (Prompt #12)

TeachLive features a complete, timezone-aware scheduling system with automated lifecycle state management, early-start/join windows, schedule conflict detection, interactive monthly calendar views, rescheduling workflows, and class cancellation protections.

### 8.1 Timezone Architecture
- **Configured Timezone**: Timezone is globally configured via `TIME_ZONE = os.getenv('DJANGO_TIME_ZONE', 'Asia/Kolkata')` with `USE_TZ = True`.
- **Offset-Aware Calculations**: All date/time comparisons combine `scheduled_date` and `scheduled_time` using `timezone.make_aware(dt, timezone.get_current_timezone())`. All comparisons against `timezone.now()` are offset-aware, eliminating Python naive datetime errors.
- **Consistent Display**: Datetimes format consistently in local Indian Standard Time (IST / Asia/Kolkata) across teacher dashboards, student calendars, and transactional email notices.

### 8.2 LiveClass Status Transitions & Protection
TeachLive uses `LiveClass.Status` as the authoritative single source of truth:
```
SCHEDULED ───────► LIVE ───────► COMPLETED (Ended)
    │
    └────────────► CANCELLED
```
- **Disallowed Transitions**: The system prevents invalid transitions (e.g. `CANCELLED -> LIVE`, `ENDED -> LIVE`, `ENDED -> SCHEDULED`).
- **Status Computation**: `get_class_status(live_class)` yields `SCHEDULED`, `READY`, `LIVE`, `ENDED`, or `CANCELLED`.
- **LiveKit Token & Access Protection**: Students and teachers cannot obtain LiveKit tokens or enter classes that are `CANCELLED`, `ENDED`, or outside allowed start/join windows.

### 8.3 Early-Start and Join Windows
- **Teacher Early-Start Window (`CLASS_EARLY_START_MINUTES = 15`)**: Instructors can only launch scheduled classes within 15 minutes before the scheduled start time. Prior to this window, class launch requests are blocked server-side with an informative countdown.
- **Student Early-Join Window (`CLASS_EARLY_JOIN_MINUTES = 10`)**: Students can enter the join flow 10 minutes prior to scheduled start, displaying a "Waiting for Instructor" state. The actual LiveKit room token is only issued once the teacher starts the class (`LIVE`).

### 8.4 Teacher Schedule Conflict Detection
- **Overlapping Slot Check**: When scheduling or editing a class, `detect_teacher_schedule_conflict()` checks if the proposed time interval `[start, start + duration)` overlaps with any existing `SCHEDULED` or `LIVE` class hosted by the same teacher.
- **Clear Error Messaging**: "Schedule conflict: You already have another class ('Calculus I') scheduled during this time (10:00 AM - 11:00 AM)."
- **Student Overlap Allowed**: Students may enroll in overlapping classes from different teachers without blocking.

### 8.5 Rescheduling Workflow
- **Authorized Ownership**: Only the host teacher (or admin) can reschedule a `SCHEDULED` class.
- **Blocked on Inactive Sessions**: Classes that are `LIVE`, `ENDED`, or `CANCELLED` cannot be rescheduled.
- **Multi-Channel Notification**: Rescheduling automatically updates the database, dispatches `Notification.Type.TEACHER_CLASS_UPDATE` alerts to enrolled students, sends update emails, and records an `AdminAuditLog(action='CLASS_RESCHEDULED')`.

### 8.6 Cancellation Workflow
- **Confirmation Flow**: Teachers cancel scheduled classes via `/classes/<id>/cancel/` with a confirmation modal/page.
- **Non-Destructive**: The `LiveClass` record is marked `CANCELLED`. Chat history, attendance, and enrollments are preserved for historical reporting.
- **Notification & Email**: Sends cancellation alerts and emails to all enrolled participants and creates an `AdminAuditLog(action='CLASS_CANCELLED')`.

### 8.7 Teacher & Student Calendar Views
- **Teacher Scheduled Classes Page (`/teacher/classes/`)**: Organized into `TODAY`, `UPCOMING`, `PAST`, and `CANCELLED` filter tabs with search, date filter, status filter, and pagination.
- **Teacher Monthly Calendar (`/teacher/calendar/`)**: Interactive calendar grid with `Previous Month`, `Next Month`, and `Today` quick navigation buttons, color-coded class chips, and direct links to class details.
- **Student Schedule Calendar (`/student/calendar/`)**: Displays only authorized classes for the enrolled student, isolating private classes.

### 8.8 Automatic Status Handling & Periodic Tasks
- **Automatic Expiration**: `mark_expired_classes_ended()` identifies `LIVE` sessions that have exceeded their scheduled duration + grace buffer and transitions them to `COMPLETED`.
- **5-Minute Ending Warning**: Teacher classroom displays a warning alert and status badge when 5 minutes remain before expected class duration completion.
- **Cron / Celery Beat Periodic Setup**:
  ```bash
  # Process class expiration and reminders periodically:
  python manage.py shell -c "from classrooms.scheduling import mark_expired_classes_ended; mark_expired_classes_ended()"
  python manage.py send_class_reminders --window 30
  ```

### 8.9 Automated Test Commands
```bash
# Run Prompt #12 Scheduling & Status Test Suite (25 tests)
python manage.py test classrooms.test_scheduling

# Run Complete Prompt #1–#12 Test Suite (174 tests)
python manage.py test
```

---

## 9. Advanced Live Classroom 2.0 (Prompt #14)

TeachLive Live Classroom 2.0 delivers a high-reliability, professional online teaching experience powered by LiveKit WebRTC media transport and Django authentication, scheduling, moderation, attendance, and authorization.

### 9.1 LiveKit Classroom Architecture
- **Media Transport**: LiveKit handles WebRTC audio tracks, camera video tracks, screen-share tracks, active speaker detection, and connection quality statistics.
- **Server-Side Authority (Django)**: Django handles authentication, class access, short-lived JWT token generation, attendance sessions, teacher moderation, and chat rate limiting.
- **Role Isolation**:
  - `Teacher`: Can publish camera, microphone, screen share, and execute remote moderation controls (mute, remove, lower hand, end class).
  - `Student`: Can publish microphone/camera if allowed, view teacher and peer video, view screen share, raise/lower hand, chat, and leave class.
- **Zero Secret Exposure**: Frontend never accesses or receives `LIVEKIT_API_SECRET` or internal credentials. Only short-lived tokens and safe public connection URLs are provided through authenticated endpoints.

### 9.2 Professional Layout & Video Priority
- **Header Bar**: Displays TeachLive branding, Class Title, Subject, Pulsing LIVE badge, Elapsed Time clock, Live Participant Count, Connection Status (🟢/🟡/🔴), Network Quality indicator (Good/Fair/Poor), Role Badge ("Teacher Classroom" vs "Live Class"), and Exit/Leave button.
- **Teacher Video Priority**: On student view, the teacher's broadcast takes visual priority. When the teacher's camera is disabled, a professional "Camera Off" placeholder is displayed with teacher initials (no broken video elements).
- **Screen Sharing**: When the teacher starts screen sharing, the screen stream automatically takes the primary stage with a prominent "Screen Sharing" banner. The teacher camera stream cleanly transitions into a picture-in-picture preview corner or returns to full view upon stopping screen share.
- **Responsive Participant Grid**: Dynamically adapts layout for 1, 2, 3, 4, 5+ participants. Each card displays video or avatar, participant name, microphone status (clear "Muted" badge), hand raised indicator, and speaking outline.
- **Active Speaker Indication**: Uses LiveKit `RoomEvent.ActiveSpeakersChanged` to highlight speaking participants with an outline and status indicator without unnecessary DOM re-renders.

### 9.3 Device Management & Pre-Join Check
- **Pre-Join Device Check Modal**: Allows checking camera preview, microphone status via Web Audio API audio level meter, and selecting camera/microphone input devices before entering the room.
- **In-Class Device Settings**: Teachers and students can switch camera and microphone devices at any time during class.
- **Audio Output Selection (`setSinkId`)**: Supported in Chromium browsers (Chrome, Edge); gracefully hidden on unsupported browsers (Firefox, Safari) to prevent misleading controls.
- **Graceful Error Handling**: Clear user-friendly alerts when permissions are denied ("Camera permission is required to enable video", "Microphone is unavailable. Check your browser permissions") without crashing the classroom.

### 9.4 Connection States & Reconnect Robustness
- **Connection States**:
  - 🟢 **Connected**: Active WebRTC media and chat data channel.
  - 🟡 **Reconnecting...**: Non-blocking warning banner shown during brief network interruptions. Classroom state and media tracks automatically restore when connection recovers.
  - 🔴 **Disconnected**: When reconnect attempts fail, displays a modal with "Try Again" and "Leave Class" options.
- **Network Quality**: Monitors LiveKit connection quality and displays a "Your connection appears unstable" suggestion banner if quality drops to Poor.
- **Attendance Deduplication**: Automatic reconnects and heartbeat pings do NOT spawn duplicate attendance records; existing open attendance sessions are preserved.

### 9.5 Switchable Side Panel & Chat
- **Tabbed Drawer / Panel**: Seamlessly switch between **Chat** and **Participants**.
- **Chat Unread Indicator**: When the chat tab is closed or switched away, a real-time unread badge alerts users of new incoming messages. Opening chat clears the unread count.
- **Chat Safeguards**: Enforces 500-character max length with live character counter, client and server-side rate limiting, and XSS sanitization.
- **Participants Roster**: Searchable list of connected attendees with role badges, microphone status, and raised-hand priority.

### 9.6 Browser Compatibility & Limitations
- **Google Chrome / Microsoft Edge**: Full support for camera, microphone, screen sharing, device switching, and audio output selection (`setSinkId`).
- **Mozilla Firefox**: Full support for camera, microphone, and screen sharing. Audio output device selection (`setSinkId`) is not supported by the browser engine and is automatically hidden.
- **Apple Safari**: Full WebRTC media support. Output sink selection is restricted by WebKit.

### 9.7 Manual Browser Testing Steps
1. **Teacher Test**:
   - Log in as teacher (`prof_albus` or test teacher account).
   - Navigate to `/teacher/classes/` and start a scheduled class.
   - Complete pre-join device check, verify camera preview and mic meter, then enter.
   - Toggle microphone on/off; verify status badge updates to "Muted" / "Mic On".
   - Toggle camera on/off; verify placeholder displays when off.
   - Click "Share Screen"; verify primary stage displays screen share with active banner. Stop screen share and verify normal return.
   - Open Chat and send messages; verify 500-character counter.
   - In Participants tab, test Muting a student, Lowering hand, and Removing participant.
   - Click "End Class", confirm in modal, and verify class transitions to COMPLETED.
2. **Student Test**:
   - Log in as enrolled student (`harry_p`).
   - Navigate to classroom URL `/live/<room_code>/classroom/`.
   - Complete pre-join check, test audio meter, and join.
   - Verify Teacher Video Priority is prominent on stage.
   - Click "Raise Hand"; verify toolbar changes to "✋ Hand Raised" and hand indicator appears on participant tile.
   - Toggle side panel tabs; verify chat unread counter increments when receiving messages on participants tab.
   - Click "Leave Class", confirm in modal, and verify clean redirection to student dashboard with attendance recorded.

---

## 10. Advanced Attendance & Reports System (Prompt #16)

TeachLive includes an enterprise-grade, verifiable Attendance and Reports system ensuring accurate duration calculations, reconnect robustness without double-counting, role-based data isolation, and formula injection-protected CSV exports.

### 10.1 Attendance Percentage Formula
The system calculates student participation percentage using scheduled class duration:
```
Attendance % = min(100.0, max(0.0, round((attended_duration / scheduled_duration) * 100, 1)))
```
- **Duration Bounding**: Bounded between `0.0%` and `100.0%` (never exceeds 100%, never below 0%).
- **Zero-Duration Protection**: If scheduled duration is 0, returns `100.0%` if attended > 0, otherwise `0.0%`.
- **Server-Side Timestamps**: Calculated exclusively from server-side timezone-aware datetimes (`Asia/Kolkata`).

### 10.2 Reconnect & Duration Robustness
- **Deduplication**: Reconnecting to an active class session reuses the student's existing `Attendance` record rather than creating duplicate rows.
- **Non-Double-Counting**: When rejoining after a disconnect or temporary departure, prior session duration is accumulated while excluding the disconnected interval.

### 10.3 Teacher Attendance Dashboard (`/teacher/attendance/`)
- **Real DB Cards**: Total Classes, Completed Classes, Total Students, Total Attendance Sessions, Average Attendance (%), Total Teaching Hours.
- **Live Attendance**: Displays students connected in real-time with session timers, connection state, raised hands, and mute status.
- **Filters & Search**: Server-side, case-insensitive search by student name, student email, class title, and subject. Filter by class, student, date presets (`today`, `yesterday`, `this_week`, `this_month`), custom range (`date_from`, `date_to`), and status (`PRESENT`, `LEFT`, `DISCONNECTED`, `ABSENT`).
- **Pagination**: 20 records per page with query string preservation across page links.

### 10.4 Class Attendance Detail (`/teacher/classes/<class_id>/attendance/`)
- **Strict Teacher Ownership**: Teachers can only view attendance for classes they created (`live_class.teacher == request.user`). Unauthorized access attempts receive HTTP 403 Forbidden.
- **Absent Student Identification**: Accurately tracks enrolled students who never joined, displaying them as `Not Joined (Absent)` with `0 min` duration and `0.0%` attendance.

### 10.5 Teacher Student Attendance Report (`/teacher/students/<student_id>/attendance/`)
- Accessible at `/teacher/students/<student_id>/attendance/`.
- Displays student profile, total classes offered, classes attended, classes missed, total hours, average session duration, overall attendance rate, and full attendance history.

### 10.6 Teacher Comprehensive Reports (`/teacher/reports/attendance/`)
- **Section A**: Class Attendance Report (roster with enrolled, joined, absent counts, avg duration, attendance %).
- **Section B**: Student Attendance Report (student aggregated statistics).
- **Section C**: Date Range Report (filtered performance by time window).
- **Section D**: Attendance Summary KPIs (highest & lowest attendance classes, total teaching hours, student learning hours).

### 10.7 Student Attendance (`/student/attendance/`)
- **Strict Privacy & Isolation**: Students can strictly view only their own attendance records. Never exposes class participants or other student records.
- **Class Attendance Detail (`/student/classes/<class_id>/attendance/`)**: Allowed only if the student is authorized/enrolled for that class (IDOR protected).
- **Summary Cards**: Classes attended, total attendance duration, average session duration, and overall attendance percentage.

### 10.8 Admin Attendance Management (`/admin-dashboard/attendance/`)
- Global visibility across all teachers, classes, and students.
- Filterable by teacher, student, class, date presets, custom date range, and status.
- Summary statistics: Total Classes, Total Students, Total Sessions, Total Attendance Duration, Average Attendance Duration, Overall Attendance Rate.

### 10.9 Secure CSV Export
- Endpoints: `/teacher/attendance/export/`, `/teacher/classes/<class_id>/attendance/export/`, `/admin-dashboard/attendance/export/`.
- **Standard 12 Columns**:
  1. Student Name
  2. Student Email
  3. Class
  4. Subject
  5. Scheduled Date
  6. Scheduled Start
  7. Scheduled Duration (mins)
  8. Join Time
  9. Leave Time
  10. Attendance Duration (mins)
  11. Attendance Status
  12. Attendance Percentage
- **CSV Injection Protection (CWE-1236)**: Sanitizes formulas starting with `=`, `+`, `-`, `@`, tab, or return characters.
- **Encoding**: UTF-8 with RFC 4180 compliance.

### 10.10 Automated Test Commands
```bash
# Run Advanced Attendance & Reports Test Suite (21 tests)
python manage.py test classrooms.test_advanced_attendance_reports

# Run Security Hardening & Production Readiness Test Suite (13 tests)
python manage.py test core.test_security_hardening

# Run Full Test Suite
python manage.py test
```

---

## 11. Production Security Hardening & Deployment Checklist

TeachLive is hardened according to OWASP Top 10 guidelines and Django production standards.

### 11.1 Security Configuration Matrix
- **`DEBUG = False`**: Enforced safely in production. No debug tracebacks, paths, or environment variables are ever leaked to public clients.
- **`SECRET_KEY`**: Sourced strictly via environment variable (`SECRET_KEY`). Raises a fatal `ImproperlyConfigured` exception in production if missing.
- **`ALLOWED_HOSTS`**: Restricted to trusted domains (`live-class-1.onrender.com`, `.onrender.com`, `RENDER_EXTERNAL_HOSTNAME`). Wildcards (`*`) are strictly prohibited.
- **`CSRF_TRUSTED_ORIGINS`**: HTTPS trusted origins configured for `https://live-class-1.onrender.com` and Render subdomains.
- **HTTPS & SSL**: `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')` configured for Render reverse proxy SSL termination; `SECURE_SSL_REDIRECT = True` in production.
- **Secure Cookie Flags**: `SESSION_COOKIE_SECURE = True`, `CSRF_COOKIE_SECURE = True`, `SESSION_COOKIE_HTTPONLY = True`, and `SameSite='Lax'` prevent session theft and CSRF while preserving Google OAuth flows.
- **HSTS**: `SECURE_HSTS_SECONDS = 31536000` (1 year). Subdomains and preload are opt-in via environment variables.
- **Security Headers**: `SECURE_CONTENT_TYPE_NOSNIFF = True`, `SECURE_REFERRER_POLICY = 'same-origin'`, and `X_FRAME_OPTIONS = 'DENY'` (Clickjacking defense).
- **Content-Security-Policy (CSP)**: Non-breaking policy allowing Bootstrap CDN, Google Fonts, Google OAuth, and LiveKit WebRTC (`wss:` and `https:`).
- **Brute-Force Rate Limiting**: IP-based rate limiting on sign-in endpoints (max 5 failed attempts per 5 minutes) via cache.
- **Open-Redirect Protection**: Strict validation with `url_has_allowed_host_and_scheme` on all `next` and redirect parameters.
- **LiveKit Security**: Server-side JWT generation with 2-hour TTL; `LIVEKIT_API_SECRET` is never exposed to frontend code; students cannot choose arbitrary rooms or publish video without instructor role.
- **Health Check**: Lightweight `/health/` probe returning HTTP 200 `{"status": "ok", "service": "TeachLive"}` without exposing credentials or internal traces.

### 11.2 Render Deployment Commands
- **Build Command**:
  ```bash
  ./build.sh
  # Executes: pip install -r requirements.txt && python manage.py collectstatic --no-input && python manage.py migrate
  ```
- **Start Command**:
  ```bash
  gunicorn config.wsgi:application
  # (or ./start.sh which auto-migrates before binding Gunicorn)
  ```

### 11.3 Production Database Note
- **SQLite (Current Default)**: Render Web Services use an ephemeral filesystem unless a Render Persistent Disk is attached.
- **PostgreSQL (Recommended for Persistence)**: Attach a Render PostgreSQL database and set `DATABASE_URL=postgresql://...`. Django automatically parses and applies SSL requirements (`sslmode=require`).



