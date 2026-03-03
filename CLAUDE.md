# ClassUp v2 — Architecture Specification

> Single source of truth for building ClassUp. Multi-tenant SaaS for schools/daycares.
> Last Updated: 2026-02-28

## Design Principles

- **SSR First**: Jinja2 + Tailwind CSS. JS only for interactivity (no frameworks).
- **API-First**: All UI actions go through REST API. HTML views are thin wrappers.
- **Multi-Tenant Isolation**: Row-level via `tenant_id` FK on every table. **NEVER** query tenant-scoped tables without `WHERE tenant_id = :tenant_id`.
- **Mobile-First**: Tailwind responsive utilities. Touch targets min 44px.
- **Convention Over Configuration**: Consistent patterns across all modules.
- **No native JS dialogs**: Use `ClassUp.toast(msg, type)` and `ClassUp.confirm(msg, opts)` instead of `alert()`/`confirm()`/`prompt()`.

## Tech Stack

**Backend**: Python 3.12+, FastAPI 0.115+, SQLAlchemy 2.0+ (async), Alembic, Pydantic 2.0+, PyJWT + passlib[bcrypt], Jinja2, arq (Redis-backed tasks), aiosmtplib + resend (email), boto3 (R2), httpx (WhatsApp), pandas (CSV import)

**Frontend**: Tailwind CSS 3.4+ (CDN), Vanilla ES2022+ JS, Heroicons (SVG), Chart.js 4+ (CDN), Flatpickr (CDN), Dropzone.js (CDN), Native WebSocket API

**Infrastructure**: Railway (app + PostgreSQL 16 + Redis 7), Cloudflare R2 (files), SMTP or Resend (email, runtime-configurable), Meta Cloud API (WhatsApp)

## Project Structure

```
app/
├── main.py, config.py, database.py, dependencies.py
├── middleware/          # tenant.py, auth.py, i18n.py
├── models/             # base.py, tenant.py, user.py, student.py, school_class.py,
│                       # attendance.py, message.py, report.py, file_entity.py,
│                       # invitation.py, teacher_invitation.py, notification.py,
│                       # system_settings.py, webhook.py, import_job.py
├── schemas/            # common.py, auth.py, tenant.py, user.py, student.py, etc.
├── services/           # auth, tenant, user, student, class, attendance, message,
│                       # report, file, invitation, teacher_invitation, notification,
│                       # email, whatsapp, webhook, import, i18n, realtime
├── api/v1/             # auth, tenants, users, students, classes, attendance,
│                       # messages, reports, files, invitations, admin, webhooks,
│                       # imports, websocket
├── web/                # auth, dashboard, students, classes, attendance, messages,
│                       # reports, photos, documents, settings, admin, super_admin,
│                       # onboarding, imports, invitations, teachers, profile, helpers
├── templates/          # base.html, components/*, auth/*, dashboard/*, students/*,
│                       # classes/*, attendance/*, messages/*, reports/*, photos/*,
│                       # documents/*, settings/*, onboarding/*, imports/*,
│                       # invitations/*, teachers/*, super_admin/*, emails/*
├── static/             # css/app.css, js/{app,websocket,attendance,messages,
│                       # reports,import,onboarding,search}.js, img/
└── utils/              # security.py, pagination.py, tenant_context.py,
                        # permissions.py, validators.py, helpers.py
translations/{en,af}/messages.json
tests/, scripts/, alembic/
```

## Configuration

Settings via `pydantic-settings` from `.env`. Key vars: `APP_SECRET_KEY`, `APP_BASE_URL`, `DATABASE_URL` (asyncpg), `REDIS_URL`, `JWT_*`, `R2_*`, `WHATSAPP_*`, `DEFAULT_LANGUAGE=en`, `SUPPORTED_LANGUAGES=en,af`, `MAX_UPLOAD_SIZE_MB=10`, `INVITATION_CODE_EXPIRY_DAYS=7`.

Email provider config stored in `system_settings` DB table (not env vars), switchable via super admin UI.

## Database Architecture

**Principles**: UUID7 PKs (time-sortable), `tenant_id` on all tenant tables, soft deletes (`deleted_at`), JSONB for flexible data, timestamps on everything, explicit join tables.

### Tables

**tenants**: id, name, slug (unique), email, phone, address, logo_path, education_type (DAYCARE|PRIMARY_SCHOOL|HIGH_SCHOOL|K12|COMBINED), settings (JSONB), is_active, onboarding_completed, timestamps, deleted_at

**tenants.settings JSONB**: `{education_type, enabled_grade_levels[], features{attendance_tracking, messaging, photo_sharing, document_sharing, daily_reports, parent_communication, nap_tracking, bathroom_tracking, fluid_tracking, meal_tracking, diaper_tracking, homework_tracking, grade_tracking, behavior_tracking, timetable_management, subject_management, exam_management, disciplinary_records, whatsapp_enabled}, terminology{student, students, teacher, teachers, class, classes, parent, parents}, report_config{default_report_type, enabled_sections[]}, whatsapp{enabled, phone_number_id, send_attendance_alerts, send_report_notifications, send_announcements}, branding{primary_color, secondary_color}, timezone, language}`

**users**: id, tenant_id (NULL for SUPER_ADMIN), email, password_hash, first_name, last_name, phone, role (SUPER_ADMIN|SCHOOL_ADMIN|TEACHER|PARENT), avatar_path, is_active, language, whatsapp_phone (E.164), whatsapp_opted_in, last_login_at, timestamps, deleted_at. Unique: (email, tenant_id) WHERE deleted_at IS NULL.

**students**: id, tenant_id, first_name, last_name, date_of_birth, gender, age_group (INFANT|TODDLER|PRESCHOOL|KINDERGARTEN|GRADE_R|GRADE_1..12), grade_level (free text), class_id FK, photo_path, medical_info, allergies, emergency_contacts (JSONB []), notes, enrollment_date, is_active, timestamps, deleted_at

**parent_students**: id, parent_id FK, student_id FK, relationship (PARENT|GUARDIAN|OTHER), is_primary. UNIQUE(parent_id, student_id)

**school_classes**: id, tenant_id, name, description, age_group, grade_level, capacity, is_active, timestamps, deleted_at

**teacher_classes**: id, teacher_id FK, class_id FK, is_primary, assigned_at. UNIQUE(teacher_id, class_id)

**attendance_records**: id, tenant_id, student_id FK, class_id FK, date, status (PRESENT|ABSENT|LATE|EXCUSED), check_in_time, check_out_time, recorded_by FK, notes, timestamps. UNIQUE(student_id, date)

**messages**: id, tenant_id, sender_id FK, message_type (ANNOUNCEMENT|CLASS_ANNOUNCEMENT|STUDENT_MESSAGE|REPLY|CLASS_PHOTO|STUDENT_PHOTO|CLASS_DOCUMENT|STUDENT_DOCUMENT|SCHOOL_DOCUMENT), subject, body, class_id FK, student_id FK, parent_message_id FK (threading), is_read (deprecated), status (SENT|DELIVERED|READ), timestamps, deleted_at

**message_recipients**: id, message_id FK CASCADE, user_id FK, is_read, read_at. UNIQUE(message_id, user_id)

**message_attachments**: id, message_id FK CASCADE, file_entity_id FK, display_order

**file_entities**: id, tenant_id, storage_path, original_name, content_type, file_size, file_category (PHOTO|DOCUMENT|AVATAR|LOGO), uploaded_by FK, created_at, deleted_at. R2 path: `{tenant_id}/{category}/{entity_id}/{uuid}_{filename}`

**daily_reports**: id, tenant_id, student_id FK, class_id FK, template_id FK, report_date, report_data (JSONB), status (DRAFT|FINALIZED), finalized_at, created_by FK, timestamps, deleted_at. UNIQUE(student_id, template_id, report_date)

**report_templates**: id, tenant_id, name, description, report_type (DAILY_ACTIVITY|PROGRESS_REPORT|REPORT_CARD), frequency (DAILY|WEEKLY|TERMLY), applies_to_grade_level (comma-separated), sections (JSONB), display_order, is_active, timestamps, deleted_at

**report_templates.sections JSONB**: Array of `{id, title, type (CHECKLIST|REPEATABLE_ENTRIES|NARRATIVE), display_order, fields[{id, label, type (SELECT|TEXT|TIME|NUMBER|TEXTAREA), options[], required}]}`

**report_data JSONB**: `{sections: {section_id: {field_id: value, ...}, repeatable_section_id: {entries: [{field_id: value}]}}}`

**parent_invitations**: id, tenant_id, student_id FK, email, first_name, last_name, invitation_code (8-char unique), status (PENDING|ACCEPTED|EXPIRED), created_by FK, expires_at, accepted_at, created_at

**teacher_invitations**: id, tenant_id, email, first_name, last_name, invitation_code (8-char unique), status (PENDING|ACCEPTED|EXPIRED|CANCELLED), created_by FK, expires_at, accepted_at, created_at

**notifications**: id, tenant_id, user_id FK, title, body, notification_type, reference_type, reference_id, is_read, read_at, created_at. Types: ATTENDANCE_MARKED, ATTENDANCE_LATE, REPORT_FINALIZED, REPORT_READY, MESSAGE_RECEIVED, ANNOUNCEMENT, PHOTO_SHARED, DOCUMENT_SHARED, INVITATION_SENT, TEACHER_ADDED, STUDENT_ADDED, CLASS_CREATED, SETTINGS_CHANGED, IMPORT_COMPLETED, WHATSAPP_MESSAGE

**webhook_endpoints**: id, tenant_id, url, secret (HMAC), events (JSONB []), is_active, timestamps

**webhook_events**: id, endpoint_id FK, event_type, payload (JSONB), status (PENDING|DELIVERED|FAILED), attempts, last_attempt_at, response_code, response_body, created_at

**bulk_import_jobs**: id, tenant_id, import_type (STUDENTS|TEACHERS|PARENTS), file_name, status (PENDING|PROCESSING|COMPLETED|FAILED), total_rows, processed_rows, success_count, error_count, errors (JSONB [{row, field, error}]), column_mapping (JSONB), created_by FK, completed_at, created_at

**system_settings**: id, key (unique), value (JSONB), timestamps. Platform-wide (not tenant-scoped). Email config stored here as key='email_config'.

### ER Summary

```
tenants → users, students, school_classes, attendance_records, messages,
          daily_reports, report_templates, file_entities, parent_invitations,
          teacher_invitations, notifications, webhook_endpoints, bulk_import_jobs
students ←→ users (via parent_students)
school_classes ←→ users (via teacher_classes)
messages → message_recipients, message_attachments → file_entities
```

## Multi-Tenancy

Shared DB, row-level isolation. TenantMiddleware extracts tenant_id from JWT → sets contextvars → all services call `get_tenant_id()` first. Exempt paths: `/`, `/login`, `/register`, `/api/v1/auth/*`, `/health`, `/static`, `/api/v1/whatsapp/webhook`.

## Authentication & Authorization

**Flow**: POST /api/v1/auth/login → bcrypt validate → JWT issued → stored as HttpOnly cookie `access_token` (web) + response body (API). JWT payload: `{sub, tenant_id, role, name, exp, iat, jti}`.

**Roles**: SUPER_ADMIN (no tenant_id) → SCHOOL_ADMIN (full tenant access) → TEACHER (own classes) → PARENT (own children, read-only)

**Permission decorator**: `@require_role("SCHOOL_ADMIN", "TEACHER")` — SUPER_ADMIN always passes.

### Permission Matrix

| Resource | ADMIN | TEACHER | PARENT |
|----------|:-----:|:-------:|:------:|
| Tenants/Users CRUD | Yes | No | No |
| Classes CRUD | Yes | No | No |
| Students CRUD | Yes | Own classes | No |
| Students view | Yes | Own classes | Own children |
| Attendance record | Yes | Own classes | No |
| Attendance view | Yes | Own classes | Own children |
| Reports create/edit/finalize | Yes | Own classes | No |
| Reports view | Yes | Own classes | Own children |
| Report templates CRUD | Yes | No | No |
| Messages: announcement | Yes | Class only | No |
| Messages: reply | Yes | Yes | Own threads |
| Photos share/view | Yes | Own classes | Own children (view) |
| Documents: school-wide | Yes | No | No |
| Documents: class | Yes | Own classes | No |
| Settings, Webhooks, Import | Yes | No | No |
| Invitations create | Yes | Yes | No |

### Parent Registration Flow

1. Admin/Teacher creates invitation (email, first_name, last_name, student_id)
2. System generates 8-char code, sends email
3. Parent clicks link → `/register?code=XXXXXXXX&email=parent@example.com`
4. System looks up invitation → pre-fills form (name/email read-only)
5. Parent sets password → account created → auto-linked to student → auto-login

> Registration URL includes both `code` and `email` as query params. The `register_url` is built in the invitation API — email template must NOT append additional params.

## API Design

**URL pattern**: `/api/v1/{resource}` (JSON API), `/{resource}` (HTML views)

**Response envelope**: `{status, data, message, errors[], pagination{page, page_size, total_items, total_pages, has_next, has_prev}}`

**Pagination**: `?page=1&page_size=20` (default 20, max 100), `?sort_by=created_at&sort_order=desc`

### Endpoints

**Auth** `/api/v1/auth`: POST login, register, refresh, logout, forgot-password, reset-password; GET/PUT /me; PUT /me/password

**Tenant** `/api/v1/tenant`: GET/PUT /configuration; PUT /branding

**Admin** `/api/v1/admin` (SUPER_ADMIN): CRUD /tenants; GET /stats; GET/PUT /email-settings; POST /email-settings/test

**Users** `/api/v1/users` (SCHOOL_ADMIN): CRUD; GET /teachers, /parents

**Students** `/api/v1/students`: CRUD; GET/POST /{id}/parents; DELETE /{id}/parents/{pid}; GET /my-children (PARENT)

**Classes** `/api/v1/classes`: CRUD; GET /{id}/students, /{id}/teachers; POST /{id}/teachers; DELETE /{id}/teachers/{tid}; PUT /{id}/set-primary; GET /my-classes

**Attendance** `/api/v1/attendance`: GET /; POST / (single), /bulk; PUT /{id}; GET /class/{cid}/date/{d}, /student/{sid}, /stats

**Messages** `/api/v1/messages`: GET / (inbox), /{id}, /unread-count, /announcements; POST /, /{id}/reply; PUT /{id}/read

**Reports** `/api/v1/reports`: CRUD; POST /{id}/finalize; GET /student/{sid}; CRUD /templates; GET /templates/for-student/{sid}

**Files** `/api/v1/files`: POST /upload; GET /{id}/url; DELETE /{id}

**Invitations** `/api/v1/invitations`: GET /; POST /, /verify (no auth); DELETE /{id}; POST /{id}/resend

**Webhooks** `/api/v1/webhooks`: CRUD; GET /{id}/events; POST /{id}/test

**Imports** `/api/v1/imports`: POST /upload, /{id}/start; GET /{id}, /{id}/errors, /

**WhatsApp** `/api/v1/whatsapp`: GET/POST /webhook (Meta); POST /send

**WebSocket**: `/api/v1/ws/{token}` — JWT in URL path

## Frontend Architecture

**Design**: Primary color `#7C3AED` (purple), Inter font, Tailwind CDN with custom config. `rounded-lg` cards, `rounded-md` buttons, `shadow-sm`/`shadow-md`.

**Layout**: Sticky navbar (logo, search, class selector, notifications, avatar) + sidebar (desktop) + bottom tab nav (mobile, < md breakpoint). Main content area with toast container.

**JS Global** (`ClassUp` object): `fetch()` wrapper (handles 401 redirect, error toasts), `toast(msg, type)`, `confirm(msg, opts)` → Promise<boolean>, `debounce(fn, delay)`, date formatters.

**Patterns**: Data attributes for progressive enhancement: `data-search-url`/`data-search-target` for live search, `data-action`/`data-url`/`data-confirm` for inline actions, `data-modal` + `<dialog>` for modal forms.

**Teacher multi-class**: Class selector dropdown in navbar, selected class stored in `selected_class_id` cookie. Primary class is default on login.

## Core Modules

### Attendance
Daily page: select class + date → student list with PRESENT/LATE/ABSENT/EXCUSED toggles → bulk POST. Check-in time auto-recorded. Notifications on ABSENT/LATE to parents (in-app + email + WhatsApp). Admin stats: rates by class, chronic absenteeism alerts.

### Messaging
Types: ANNOUNCEMENT (school-wide), CLASS_ANNOUNCEMENT, STUDENT_MESSAGE, REPLY, CLASS_PHOTO, STUDENT_PHOTO, CLASS_DOCUMENT, STUDENT_DOCUMENT, SCHOOL_DOCUMENT. Auto-resolves recipients based on type. Threaded chat-style view. Inbox tabs: All | Announcements | Photos | Documents.

### Files
Upload via Dropzone.js → client validation → POST multipart → python-magic MIME check → R2 upload → FileEntity record. Presigned URLs generated on every fetch (1h expiry). Photos max 5MB, documents max 10MB.

### Reports
Template-driven (no hardcoded logic). Template sections: CHECKLIST (select/text fields), REPEATABLE_ENTRIES (dynamic table rows), NARRATIVE (textarea). Report creation: select student → match templates by age_group/grade_level → fill form → save as DRAFT or FINALIZE. Finalization triggers notifications to parents + webhook `report.finalized`.

## Email System

Two providers: **SMTP** (aiosmtplib, ports 465/587) and **Resend** (resend-python). Config stored in `system_settings` table (key=`email_config`), managed via super admin UI at `/admin/email-settings`.

Config shape: `{provider, enabled, from_email, from_name, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_tls, resend_api_key}`

Tenant-scoped emails use tenant name as sender display name. Service loads config from DB on every send; skips silently if not configured.

**Triggers**: tenant created (welcome), parent invited (parent_invite), report finalized (report_ready), password reset, attendance absence, admin notifications.

## WhatsApp Integration

Meta Cloud API. Pre-approved templates: `attendance_alert`, `report_ready`, `announcement`, `parent_invite`, `welcome`. Outbound via httpx POST to `graph.facebook.com`. Inbound via webhook (HMAC verified) → lookup parent by whatsapp_phone → create Message. Opt-in required (`users.whatsapp_opted_in`).

## WebSocket Real-Time

FastAPI WebSocket + Redis Pub/Sub (multi-instance). ConnectionManager keyed by `{tenant_id}:{user_id}`. Events: `notification`, `attendance_update`, `message_received`, `unread_count`. Client reconnects with exponential backoff (max 5 attempts).

## Onboarding Wizard (5 Steps)

Shown on first SCHOOL_ADMIN login if `tenant.onboarding_completed == false`:
1. School info (name, address, timezone, logo, colors)
2. Education type + feature toggles
3. Create classes (min 1)
4. Invite teachers (optional)
5. Summary + quick links → sets `onboarding_completed = true`

Data saved per step (not just at end).

## Bulk CSV Import

Types: STUDENTS (required: first_name, last_name), TEACHERS (required: first_name, last_name, email). Flow: upload CSV → preview + column mapping UI → background task processes rows → WebSocket notifies on completion. Non-transactional: successful rows commit, failed rows logged with `{row, field, value, message}`.

## i18n

Languages: en (default), af (Afrikaans). JSON translation files at `translations/{lang}/messages.json`. Detection priority: user.language → Accept-Language header → tenant default → app default. Jinja2 `t()` global function with dot-notation keys. Tenant terminology overrides via `tenant.settings.terminology`.

## Webhooks

Events: student.created/updated/deleted, attendance.marked/bulk, report.created/finalized, message.sent, teacher.added, parent.registered, class.created, import.completed. HMAC-signed delivery (`X-ClassUp-Signature: sha256=...`). Retry: 3 attempts, exponential backoff (1m, 5m, 30m).

## Background Tasks (arq)

Functions: send_email, deliver_webhook, process_import, send_whatsapp. Worker config: max_jobs=10, job_timeout=300s. Separate Railway service with `WORKER_MODE=true`.

## Error Handling

Exception hierarchy: ClassUpException(400) → NotFoundException(404), ForbiddenException(403), UnauthorizedException(401), ConflictException(409), ValidationException(422). Global handler returns JSON for `/api/*` requests, HTML error pages otherwise.

## Testing

pytest + pytest-asyncio + httpx AsyncClient. Fixtures: db_session (test DB), client, auth_client (with JWT). Cover: auth, CRUD, permissions, tenant isolation, bulk ops, file upload, WebSocket, webhooks.

## Deployment (Railway)

Services: classup-web (Uvicorn, 2 workers), classup-worker (arq), PostgreSQL, Redis. Dockerfile: python:3.12-slim + libmagic1 + libpq-dev. Start script runs `alembic upgrade head` then uvicorn or arq based on `WORKER_MODE`.

## Code Style

- Python: PEP 8, type hints, async for DB ops. Every service method calls `get_tenant_id()` first.
- Every API endpoint uses `@require_role()`. Every template extends `base.html`.
- HTML: Semantic elements, aria attributes. CSS: Tailwind only. JS: ES2022+, const/let, no jQuery.
- Naming: snake_case (Python/SQL), camelCase (JS), kebab-case (CSS/URLs).

## Build Order

1. **Foundation**: Scaffolding, config, DB setup, base models, Alembic, health check
2. **Auth + Tenancy**: User/Tenant models, JWT, auth API, middleware, login/register pages, permissions
3. **Core Entities**: Student/Class CRUD + pages, teacher-class/parent-student joins, base layout, dashboards
4. **Attendance**: Model, API (single + bulk), pages, statistics
5. **Messaging**: Message model + recipients + attachments, API, inbox/thread/compose pages
6. **Files**: FileEntity, R2 service, upload API, photo gallery, document pages
7. **Reports**: Templates + reports models, template CRUD, dynamic form, finalization + notifications
8. **Communication**: Email service (dual provider), email templates, notifications, WebSocket, WhatsApp
9. **Advanced**: Invitations, onboarding wizard, CSV import, i18n, webhooks
10. **Polish**: Error pages, empty states, loading states, mobile audit, worker setup, seed data, deployment
