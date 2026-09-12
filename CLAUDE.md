# ClassUp v2 — Architecture Specification

> Single source of truth for building ClassUp. Multi-tenant SaaS for schools/daycares.
> Last Updated: 2026-09-12
## Testing Requirements
- Every new feature must have unit tests before marking complete
- Integration tests required for all API endpoints
- Test coverage must not drop below 80%
- Run tests before considering any task done
- Tests must be green — never leave failing tests

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
├── middleware/          # tenant.py, auth.py, audit.py, subscription.py, i18n.py
├── models/             # base.py, tenant.py, user.py, student.py, school_class.py,
│                       # attendance.py, message.py, report.py, file_entity.py,
│                       # invitation.py, teacher_invitation.py, notification.py,
│                       # system_settings.py, webhook.py, import_job.py,
│                       # billing.py, academic.py, school_event.py, event_rsvp.py,
│                       # announcement.py, whatsapp_inbound.py, tenant_subscription.py,
│                       # subscription_plan.py, accounting.py
├── schemas/            # common.py, auth.py, tenant.py, user.py, student.py, etc.
├── services/           # auth, tenant, user, student, class, attendance, message,
│                       # report, file, invitation, teacher_invitation, notification,
│                       # email, whatsapp, webhook, import, i18n, realtime,
│                       # billing_service, academic_service, event_service,
│                       # announcement_service, parent_notifier, whatsapp_menu_bot,
│                       # whatsapp_ai_bot, whatsapp_bot_tools, subscription_service,
│                       # gateway_service (Paystack, PayNow), jurisdiction_service,
│                       # accounting_service, ai_config, report_pdf, invoice_pdf
├── api/v1/             # auth, tenants, users, students, classes, attendance,
│                       # messages, reports, files, invitations, admin, webhooks,
│                       # imports, websocket, billing, academic, events,
│                       # announcements, subscriptions, whatsapp, accounting, push
├── web/                # auth, dashboard, students, classes, attendance, messages,
│                       # reports, photos, documents, settings, admin, super_admin,
│                       # onboarding, imports, invitations, teachers, profile, helpers,
│                       # billing, events, accounting, timetable, subscription
├── templates/          # base.html, components/*, auth/*, dashboard/*, students/*,
│                       # classes/*, attendance/*, messages/*, reports/*, photos/*,
│                       # documents/*, settings/*, onboarding/*, imports/*,
│                       # invitations/*, teachers/*, super_admin/*, emails/*,
│                       # billing/*, events/*, accounting/*, timetable/*, subscription/*
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

**tenants.settings JSONB**: `{education_type, enabled_grade_levels[], features{attendance_tracking, messaging, photo_sharing, document_sharing, daily_reports, parent_communication, nap_tracking, bathroom_tracking, fluid_tracking, meal_tracking, diaper_tracking, homework_tracking, grade_tracking, behavior_tracking, timetable_management, subject_management, exam_management, disciplinary_records, whatsapp_enabled, billing, accounting}, terminology{student, students, teacher, teachers, class, classes, parent, parents}, report_config{default_report_type, enabled_sections[]}, whatsapp{enabled, phone_number_id, send_attendance_alerts, send_report_notifications, send_announcements}, branding{primary_color, secondary_color}, billing_currency, billing_banking_details, billing_payment_instructions, billing_overdue_reminders_enabled, billing_overdue_reminder_interval_days, timezone, language}`

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

**report_templates.sections JSONB**: Array of `{id, title, type (CHECKLIST|REPEATABLE_ENTRIES|NARRATIVE|ACADEMIC_GRADES|MEALS|INFO_DISPLAY|SUMMARY|SIGNATURES), display_order, color, fields[{id, label, type (SELECT|TEXT|TIME|NUMBER|TEXTAREA|CHECKBOX|MEAL_ENTRY|SIGNATURE|DATE|CALCULATED), options[], required, auto_calculate}]}`. ACADEMIC_GRADES sections also have: `subjects[{id, name, total_marks}]`, `grading_system[{min, max, grade, description}]`. MEALS sections also have: `meal_options[]`.

**report_data JSONB**: `{sections: {section_id: {field_id: value, ...}, repeatable_section_id: {entries: [{field_id: value}]}, academic_grades_section_id: {subject_id: {marks_obtained, remarks}}}}`

**parent_invitations**: id, tenant_id, student_id FK, email, first_name, last_name, invitation_code (8-char unique), status (PENDING|ACCEPTED|EXPIRED), created_by FK, expires_at, accepted_at, created_at

**teacher_invitations**: id, tenant_id, email, first_name, last_name, invitation_code (8-char unique), status (PENDING|ACCEPTED|EXPIRED|CANCELLED), created_by FK, expires_at, accepted_at, created_at

**notifications**: id, tenant_id, user_id FK, title, body, notification_type, reference_type, reference_id, is_read, read_at, created_at. Types: ATTENDANCE_MARKED, ATTENDANCE_LATE, REPORT_FINALIZED, REPORT_READY, MESSAGE_RECEIVED, ANNOUNCEMENT, PHOTO_SHARED, DOCUMENT_SHARED, INVITATION_SENT, TEACHER_ADDED, STUDENT_ADDED, CLASS_CREATED, SETTINGS_CHANGED, IMPORT_COMPLETED, WHATSAPP_MESSAGE, INVOICE_SENT, INVOICE_OVERDUE, PAYMENT_RECEIVED, EVENT_INVITED, EVENT_REMINDER, EVENT_RSVP_CONFIRMED, EVENT_CANCELLED, CHILD_LINKED

**webhook_endpoints**: id, tenant_id, url, secret (HMAC), events (JSONB []), is_active, timestamps

**webhook_events**: id, endpoint_id FK, event_type, payload (JSONB), status (PENDING|DELIVERED|FAILED), attempts, last_attempt_at, response_code, response_body, created_at

**bulk_import_jobs**: id, tenant_id, import_type (STUDENTS|TEACHERS|PARENTS), file_name, status (PENDING|PROCESSING|COMPLETED|FAILED), total_rows, processed_rows, success_count, error_count, errors (JSONB [{row, field, error}]), column_mapping (JSONB), created_by FK, completed_at, created_at

**system_settings**: id, key (unique), value (JSONB), timestamps. Platform-wide (not tenant-scoped). Email config stored here as key='email_config'.

**subjects**: id, tenant_id, name, code (unique per tenant), description, default_total_marks (default 100), category, display_order, is_active, timestamps, deleted_at

**class_subjects**: id, class_id FK CASCADE, subject_id FK CASCADE, total_marks (nullable, overrides subject default), is_compulsory (default true), display_order, timestamps. UNIQUE(class_id, subject_id). Property: `effective_total_marks` (returns total_marks or subject default)

**grading_systems**: id, tenant_id, name, description, is_default, is_active, grades (JSONB `[{min, max, grade, description, points}]`), timestamps, deleted_at

**billing_fee_items**: id, tenant_id, name, description, amount (Numeric 12,2), frequency (MONTHLY|TERMLY|ANNUALLY|ONCE_OFF), applies_to (ALL|CLASS), class_id FK (nullable), is_active, display_order, deleted_at, timestamps

**billing_invoices**: id, tenant_id, student_id FK CASCADE, invoice_number (unique per tenant+year, format INV-YYYY-NNNN), billing_period_start/end, due_date, subtotal, total_amount, amount_paid, balance (all Numeric 12,2), status (DRAFT|SENT|PARTIALLY_PAID|PAID|OVERDUE|CANCELLED), notes, created_by FK, sent_at, last_reminder_sent_at, deleted_at, timestamps

**billing_invoice_items**: id, invoice_id FK CASCADE, fee_item_id FK SET NULL, description, quantity, unit_amount, total_amount (Numeric)

**billing_payments**: id, tenant_id, invoice_id FK CASCADE, student_id FK CASCADE, amount (Numeric 12,2), payment_method (CASH|BANK_TRANSFER|EFT|CARD|CHEQUE|OTHER), reference_number, payment_date, notes, recorded_by FK, deleted_at, timestamps

**chart_accounts**: id, tenant_id, code (unique per tenant), name, type (INCOME|EXPENSE|ASSET|LIABILITY), description, is_active, is_system (protects code 4000 Tuition Fees from edits/delete), display_order, deleted_at, timestamps. Default chart seeded on tenant create.

**bank_accounts**: id, tenant_id, name, bank_name, account_number, branch_code, account_type (OPERATING|SAVINGS|PETTY_CASH|OTHER), currency (default ZAR), opening_balance (Numeric 14,2), opening_balance_date, is_default (only one per tenant), is_active, deleted_at, timestamps

**vendors**: id, tenant_id, name, contact_person, email, phone, address, vat_number, banking_details (JSONB), notes, is_active, deleted_at, timestamps

**accounting_transactions**: id, tenant_id, date, type (INCOME|EXPENSE|TRANSFER), amount (Numeric 14,2), currency (default ZAR), account_id FK chart_accounts, bank_account_id FK bank_accounts, transfer_to_bank_account_id FK bank_accounts (for TRANSFER), vendor_id FK vendors, student_id FK students, billing_payment_id FK billing_payments (UNIQUE — for idempotent auto-link), description, reference, vat_amount, vat_rate, receipt_file_id FK file_entities, created_by FK users, deleted_at, timestamps. Single-entry: one row = one money movement. P&L / cash position derived by aggregating these rows.

**school_events**: id, tenant_id, created_by FK users, title, description, event_type (PARENT_MEETING|PARENT_TEACHER_CONFERENCE|ASSEMBLY|OUTING|SPORTS|OTHER), scope (SCHOOL|CLASS|STUDENT), class_id FK (nullable), student_id FK (nullable), starts_at, ends_at (nullable), timezone (IANA), location, rsvp_required, rsvp_deadline, reminder_24h_sent_at, reminder_1h_sent_at, cancelled_at, deleted_at, timestamps. Audience resolved by scope: SCHOOL → all opted-in parents; CLASS → parents of students in that class; STUDENT → parents of that student.

**event_rsvps**: id, event_id FK CASCADE, user_id FK users, response (YES|NO|MAYBE), responded_at, created_at. UNIQUE(event_id, user_id) — latest response overwrites (upsert). Signed public URL (`/events/{id}/rsvp?u=&r=&sig=`) lets a parent RSVP from the email/WhatsApp without logging in — HMAC of `(event_id, user_id, response)` with `app_secret_key`.

**announcements**: id, tenant_id, created_by FK, title, body, severity (INFO|IMPORTANT|URGENT|EMERGENCY), scope (SCHOOL|CLASS), class_id FK (nullable), deleted_at, timestamps. Notifications: in-app for all, email + WhatsApp for URGENT/EMERGENCY (URGENT/EMERGENCY always trigger the external channels; INFO/IMPORTANT stay in-app unless the tenant opts in).

**whatsapp_inbound_messages**: id, from_phone (indexed), message_type, text (up to 8000 chars), meta_message_id (unique), raw_payload (JSONB), tenant_id FK (nullable — NULL for unknown senders), matched_user_id FK (nullable), auto_replied (bool), auto_reply_error (text), created_at (indexed). Dedup boundary for Meta webhook retries. Written from `app/api/v1/whatsapp.py:process_inbound_message`.

**subscription_plans**: id, code (unique — STARTER / GROWTH / SCALE / etc.), name, description, price_monthly, price_annually (Numeric 12,2), currency (ZAR by default), max_students, max_staff, features (JSONB — attendance_tracking, messaging, billing, accounting, whatsapp_enabled, whatsapp_ai_enabled, etc.), trial_days, is_active, display_order, timestamps. Platform-wide catalogue managed by super admin at `/admin/subscription-plans`.

**tenant_subscriptions**: id, tenant_id FK (indexed, unique per active row), plan_id FK subscription_plans, status (TRIALING|ACTIVE|PAST_DUE|CANCELLED|SUSPENDED), billing_frequency (MONTHLY|ANNUAL), trial_start, trial_end, current_period_start, current_period_end, grace_period_end, cancelled_at, failed_payment_count, paystack_customer_code, paystack_subscription_code, paystack_email_token, paystack_authorization_code, timestamps. Enforced by `SubscriptionMiddleware` — non-ACTIVE/TRIALING tenants redirect to `/subscription`.

### ER Summary

```
tenants → users, students, school_classes, attendance_records, messages,
          daily_reports, report_templates, file_entities, parent_invitations,
          teacher_invitations, notifications, webhook_endpoints, bulk_import_jobs,
          subjects, grading_systems, billing_fee_items, billing_invoices, billing_payments,
          chart_accounts, bank_accounts, vendors, accounting_transactions,
          school_events, announcements, tenant_subscriptions, whatsapp_inbound_messages
students ←→ users (via parent_students)
school_classes ←→ users (via teacher_classes)
school_classes ←→ subjects (via class_subjects)
messages → message_recipients, message_attachments → file_entities
billing_invoices → billing_invoice_items, billing_payments
billing_invoices → students (student_id)
billing_payments → accounting_transactions (auto-link, idempotent)
accounting_transactions → chart_accounts, bank_accounts, vendors, students, file_entities
school_events → event_rsvps → users
tenant_subscriptions → subscription_plans
whatsapp_inbound_messages → users (matched_user_id), tenants (nullable)
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
| Billing: fee items, invoices, payments | Yes | No | No |
| Billing: view own invoices/statement | Yes | No | Own children |
| Academic: subjects, grading CRUD | Yes | No | No |
| Academic: view class subjects | Yes | Own classes | No |
| Events: create/edit/cancel | Yes | Yes | No |
| Events: view + RSVP | Yes | Own classes | Own children |
| Announcements: create | Yes | Yes | No |
| Announcements: view | Yes | Own classes | Own children |
| WhatsApp bot | — | — | Own opt-in |
| Accounting: transactions, banks, vendors | Yes | No | No |

### Parent Registration Flow

Two entry paths — the admin picks one when adding a student.

**A. Captured during student enrollment (recommended default)** — the Add Student form has a Parent/Guardian section (`app/templates/students/create.html`). Admin fills first/last/email/phone/relationship/is_primary/send_whatsapp_invite for each parent (`+ Add another parent` for multiples). On submit:
- Existing PARENT user on this tenant matched by email → link via `parent_students`, flip `whatsapp_opted_in=True` + set `whatsapp_phone` if the admin ticked the WhatsApp box, fire the "new child added to your profile" email + WhatsApp mirror.
- No existing user → create a `ParentInvitation`, send email invite; if the admin gave a phone + ticked WhatsApp, also fire the `parent_signup` template so the parent gets a one-tap signup link on WhatsApp.
- Per-parent failure never rolls back the student — the response returns `{student, parent_results}` so the UI can surface which invites succeeded / failed. Sibling case handled: if the admin picked a sibling with parents to inherit, the parent section dims (parents are inherited via `link_parent`, which also fires the "new child added" notifications).

**B. Standalone invite (from the student detail page or `/invitations`)** — same underlying `InvitationService` path.

**Registration completion (same for both paths)**:
1. Parent clicks the link → `/register?code=XXXXXXXX&email=parent@example.com`
2. System looks up invitation → pre-fills form (name/email read-only)
3. Parent sets password → account created → auto-linked to student → auto-login

> Registration URL includes both `code` and `email` as query params. The `register_url` is built in the invitation API — email template must NOT append additional params.

**Orphan cleanup (POPIA/GDPR)** — when a student is soft-deleted or a parent is unlinked, any parent whose ONLY child at this tenant was that student gets their User row soft-deleted (`is_active=False`, `deleted_at` set). Historical `parent_students` rows survive so old messages/attendance stay accurate. Any PENDING `parent_invitations` for the departing student are marked EXPIRED so a stale link can't create a link to a tombstoned student.

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

**Billing** `/api/v1/billing`: CRUD /fee-items; GET/POST /invoices; POST /invoices/generate; GET/PUT/DELETE /invoices/{id}; POST /invoices/{id}/send, /invoices/{id}/cancel; GET/POST /payments; DELETE /payments/{id}; GET /students/{sid}/statement, /students/{sid}/balance; GET /my-children/balances (PARENT); GET /summary

**Academic** `/api/v1/academic`: CRUD /subjects; GET/POST /classes/{cid}/subjects; PUT/DELETE /classes/{cid}/subjects/{sid}; POST /classes/{cid}/subjects/bulk; CRUD /grading-systems; POST /setup-defaults

**Accounting** `/api/v1/accounting` (gated by `accounting` feature): CRUD /accounts, /banks, /vendors; POST /expenses, /income, /transfers; GET /transactions; PUT/DELETE /transactions/{id}; GET /dashboard, /reports/profit-loss, /reports/expenses-by-category, /reports/cash-position

**Events** `/api/v1/events`: GET / (staff — all events on tenant), GET /my (parent — events they're invited to), POST /, PUT /{id}, DELETE /{id} (soft cancel), POST /{id}/rsvp (authenticated — records the parent's response), GET /{id} (detail with RSVP summary)

**Announcements** `/api/v1/announcements`: CRUD; GET /my (parent view)

**Subscriptions** `/api/v1/subscriptions`: GET / (current tenant's plan + status), GET /plans (public catalogue), POST /initialize-payment (Paystack/PayNow — returns hosted-checkout URL), POST /webhook/paystack, POST /webhook/paynow

**WhatsApp** `/api/v1/whatsapp`: GET/POST /webhook (Meta); POST /send

**WebSocket**: `/api/v1/ws/{token}` — JWT in URL path

**Super Admin** `/api/v1/admin` (SUPER_ADMIN only, in addition to email settings above): GET /whatsapp-messages (last N inbound); GET/PUT /whatsapp-settings; POST /whatsapp-settings/test-connection; POST /whatsapp-settings/send-test; GET/PUT /ai-config; PUT /tenants/{id}/features (toggle whatsapp / whatsapp_ai / accounting / billing / etc.)

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
Template-driven (no hardcoded logic). Section types: CHECKLIST (select/text fields), REPEATABLE_ENTRIES (dynamic table rows), NARRATIVE (textarea), ACADEMIC_GRADES (subject marks table with auto-grading), MEALS (meal intake tracking), INFO_DISPLAY (student metadata), SUMMARY (auto-calculated totals), SIGNATURES (print-only). ACADEMIC_GRADES sections prefer database `class_subjects` over template-embedded subjects. Report creation: select student → match templates by grade_level → fill form → save as DRAFT or FINALIZE. Finalization triggers notifications to parents + webhook `report.finalized`. Default templates auto-created per education type (DAYCARE → daily report, PRIMARY/HIGH_SCHOOL → report card).

### Academic
Subjects managed per tenant with code (unique), default_total_marks, category. Subjects assigned to classes via `class_subjects` join table (with optional total_marks override, is_compulsory flag). Grading systems define grade boundaries (`[{min, max, grade, description}]`); one can be marked `is_default`. Report cards use class subjects + grading system for academic performance section. Setup defaults available per education type (primary: 9 subjects, high school: 10 subjects).

## Email System

Two providers: **SMTP** (aiosmtplib, ports 465/587) and **Resend** (resend-python). Config stored in `system_settings` table (key=`email_config`), managed via super admin UI at `/admin/email-settings`.

Config shape: `{provider, enabled, from_email, from_name, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_tls, resend_api_key}`

Tenant-scoped emails use tenant name as sender display name. Service loads config from DB on every send; skips silently if not configured.

**Triggers**: tenant created (welcome), parent invited (`parent_signup`), report finalized (`report_ready`), password reset, attendance absence, admin notifications, invoice sent, invoice overdue reminder, payment received, event invitation (with ICS attachment), event reminder (T-24h / T-1h), event RSVP confirmed, new child linked to existing parent account.

## Billing

Fee items define charges (amount, frequency MONTHLY|TERMLY|ANNUALLY|ONCE_OFF, applies to ALL or specific CLASS). Invoices generated per student with line items from fee items; auto-numbered INV-YYYY-NNNN. Lifecycle: DRAFT → SENT (triggers email to parents) → PARTIALLY_PAID/PAID/OVERDUE/CANCELLED. Payments recorded against invoices; balance auto-recalculated. Overdue detection runs on `/billing` dashboard load (`check_overdue_invoices()`). Recurring overdue reminders configurable via Settings > Billing (`billing_overdue_reminders_enabled`, `billing_overdue_reminder_interval_days` default 7, tracked by `last_reminder_sent_at`). Parents see invoices (non-draft), statements, and balances for their children. Settings stored in `tenant.settings` JSONB: `billing_currency`, `billing_banking_details`, `billing_payment_instructions`. Recording a payment auto-creates a matching INCOME accounting transaction (see Accounting).

## Accounting

In-house single-entry bookkeeping for schools — eliminates the need for a separate package like Sage. One transaction row = one money movement. Gated by `tenant.settings.features.accounting`.

**Defaults seeded on tenant create**: chart of accounts (7 income + 13 expense + 1 asset + 2 liability), one default Operating Account bank. The Tuition Fees account (code `4000`) is `is_system=True` — protected from delete/deactivate, used as the auto-link target for billing payments.

**Concepts**:
- **Chart of accounts** — buckets for income (4xxx), expenses (5xxx), assets (1xxx), liabilities (2xxx). Admins add custom categories.
- **Bank accounts** — physical school bank accounts. Live balance = opening_balance + INCOME + transfers in − EXPENSE − transfers out (up to as_of date).
- **Vendors** — suppliers (optional on expense entry).
- **Transactions** — INCOME, EXPENSE, or TRANSFER. Each row references one account + one bank.

**Auto-link from Billing**: when `BillingPayment` is recorded, `accounting_service.link_billing_payment()` creates a matching INCOME transaction against the Tuition Fees account on the default bank. Idempotent — guarded by UNIQUE on `accounting_transactions.billing_payment_id`. Auto-linked rows can't be edited or deleted directly (must remove the source payment).

**Reports**:
- **Profit & Loss** — income & expense aggregates by account + totals + net, for a date range.
- **Expenses by Category** — sum + count per expense account, sorted desc, with percentage shares.
- **Cash Position** — live balance per bank as of a date, with grand total.
- **Dashboard summary** — this-month income/expense/net + cash total + per-bank balances.

UI: `/accounting` (admin only when feature enabled). Sub-nav: Dashboard, Expenses, Income, Vendors, Banks, Chart of Accounts, Reports. Print-friendly CSS on Reports page.

## Events

School events with per-parent RSVP. Staff create events with a scope (SCHOOL / CLASS / STUDENT), the audience is resolved from the scope, and each parent in that audience gets email + WhatsApp invitations with tap-to-respond controls.

**Types**: `PARENT_MEETING`, `PARENT_TEACHER_CONFERENCE`, `ASSEMBLY`, `OUTING`, `SPORTS`, `OTHER`.

**Audience resolution** (in `event_service.resolve_audience`):
- SCHOOL → every opted-in parent in the tenant.
- CLASS → parents of students in `class_id`.
- STUDENT → parents of `student_id`.

**Notification chain (per parent, best-effort each channel)**:
- Email with a `.ics` attachment (`event_service.build_ics`) — RFC 5545 compliant, stable UID = `classup-event-{id}@classup.co.za` so updating an event replaces the calendar entry instead of duplicating. Method REQUEST on create/update, CANCEL on soft-cancel; SEQUENCE bumps with `updated_at`.
- Email carries a signed RSVP URL: `/events/{id}/rsvp?u=<user_id>&r=<YES|NO|MAYBE>&sig=<HMAC>`. The HMAC of `(event_id, user_id, response)` with `app_secret_key` is the authorisation — the endpoint bypasses auth for signed-link RSVPs (see `AuthMiddleware` exempt list).
- WhatsApp: `event_invited` template if opted-in.

**Reminders** — arq periodic worker checks every 15 minutes for events starting in the next 24h (`reminder_24h_sent_at` NULL) and next 1h (`reminder_1h_sent_at` NULL). Fires WhatsApp `event_reminder` + updates the sent-at column so it fires exactly once per window.

**RSVP** — one row per (event, user) in `event_rsvps`, upserted so the latest response wins. Recording an RSVP fires the `event_rsvp_confirmed` template (WhatsApp) so the parent has a receipt.

**UI**:
- Staff at `/events` — list (upcoming / all tabs) + Create Event modal + per-event detail with RSVP roll-up.
- Parent at `/events` — the same list filtered to events they're invited to, with inline YES/MAYBE/NO buttons that call `POST /api/v1/events/{id}/rsvp`.
- Cancelling an event soft-cancels (sets `cancelled_at`), fires the CANCEL calendar update to every parent, and shows the event with `line-through` styling in the list.

## WhatsApp Integration

Meta Cloud API. Config lives in `system_settings.whatsapp_config` (managed via `/admin/whatsapp-settings`) — env vars are fallback only. Outbound via httpx POST to `graph.facebook.com`. Inbound webhook is HMAC-verified, persisted to `whatsapp_inbound_messages`, deduped by `meta_message_id`, and dispatched to the bot.

**Meta-approved templates (11)**:
- Utility: `attendance_alert`, `welcome`, `report_ready`, `event_invited`, `event_rsvp_confirmed`, `invoice_sent`, `payment_received`, `invoice_overdue`
- Marketing (Meta forced the reclassification on these — auto-opt-in via `users.whatsapp_opted_in` covers us): `announcement`, `event_reminder`, `parent_signup`
- `parent_signup` replaced the old `parent_invite` — Meta locks a deleted template name for 30 days, so a rename was required. The code moves out of the body into a dynamic URL button so the template reads as pure copy.

**Outbound flow** — `WhatsAppService.send_template_message` accepts `parameters` (body vars), `button_url_variables` (dynamic URL button suffixes, one per button), and `header_document_media_id` (for a doc-header template — upload via `upload_media()` first). Media sends: `send_document_from_bytes` / `send_image_from_bytes` are the one-call helpers.

**Parent notification gate** — every parent-facing WhatsApp send goes through `parent_notifier._can_notify_whatsapp(db, user)`. Requires (all of): user active + `whatsapp_phone` set + `whatsapp_opted_in=True`, tenant `settings.features.whatsapp_enabled`, plan `features.whatsapp_enabled`, service configured. Any missing piece is a silent skip — logged at INFO with the specific reason so operators can grep. Sends never raise; a failure logs and returns False.

**Inbound bot modes** — resolved by `resolve_bot_mode(tenant, plan)`:
- **MENU** (default) — `whatsapp_menu_bot.py` state machine with interactive buttons + lists, backed by 6 read-only tool functions (attendance, reports, balance, invoices, events, children).
- **AI** (opt-in per tenant + plan feature `whatsapp_ai_enabled`) — `whatsapp_ai_bot.py` runs a Claude tool-use loop against the same tools, session cached in Redis with 24h TTL. Falls back to MENU if `ai_config` isn't set.
- **STOP / START** replies are handled first: STOP flips `whatsapp_opted_in=False`, START flips it back on.

**Onboarding for unknown-but-recognized numbers** — an inbound from an E.164 phone that matches `users.phone` (but not `whatsapp_phone`) offers the parent a one-tap "Yes, opt me in" flow that copies the phone to `whatsapp_phone` and sets `whatsapp_opted_in=True`.

**Media in the bot** — the AI bot can attach PDFs (invoice, report card via WeasyPrint) and images (photos) as native WhatsApp media, uploaded via Meta's Media API so no public URLs leak.

**Testing / send-test** — Super admin `/admin/whatsapp-settings` has a live inbound feed (last 20 messages) plus a per-template test-send that lets an admin fire a real template to their own number.

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

Events: student.created/updated/deleted, attendance.marked/bulk, report.created/finalized, message.sent, teacher.added, parent.registered, class.created, import.completed, invoice.sent, payment.recorded, event.created/cancelled, event.rsvp_received, announcement.published. HMAC-signed delivery (`X-ClassUp-Signature: sha256=...`). Retry: 3 attempts, exponential backoff (1m, 5m, 30m).

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

## SaaS Subscriptions

The platform runs as a monetised SaaS. Every tenant has a `TenantSubscription` on one of the `subscription_plans` (STARTER / GROWTH / SCALE — configurable per market). The `SubscriptionMiddleware` gates every non-exempt request: a status of `TRIALING` (and `trial_end >= today`), `ACTIVE`, or `PAST_DUE` (still within grace) passes; everything else is redirected to `/subscription`.

**Feature gates** — plans carry a `features` JSONB dict (`whatsapp_enabled`, `whatsapp_ai_enabled`, `billing`, `accounting`, `timetable_management`, etc.). The `require_feature("<key>")` dependency (`app/utils/permissions.py`) enforces per-endpoint: raises `FeatureLockedException` (402 JSON for APIs, redirect to `/subscription?locked=<key>` for web).

**Payment providers** — `gateway_service` abstracts Paystack and PayNow. Each has its own credentials table entry; the checkout flow is `POST /api/v1/subscriptions/initialize-payment` → returns a hosted-checkout URL → parent completes on provider → webhook (`/webhook/paystack` or `/webhook/paynow`) verifies HMAC + updates the subscription. Idempotent by transaction reference.

**Jurisdiction** — `jurisdiction_service` maps tenant country → currency + timezone + tax rules + preferred payment provider. Set via super admin at `/admin/tenants/{id}`.

**Billing frequency** — subscriptions carry `billing_frequency` (MONTHLY | ANNUAL). Plans expose `price_monthly` and `price_annually`; the parent picks at checkout.

## Deep-link handling (WhatsApp / email URLs)

Every URL we drop into a WhatsApp template button or email hyperlink must survive an unauthenticated tap: the browser opens the URL cold, the user isn't logged in, and they need to land back on the intended page after login. The `_require_auth` helper in each `app/web/*` module preserves `?next=<url>` on the login redirect; the login POST handler honours it. **Any new deep-linkable route must go through `_require_auth`** (not raw `RedirectResponse(url="/login")`) so the `next=` survives.

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
10. **Events**: school_events + event_rsvps, ICS-attached email, WhatsApp templates, arq reminder worker
11. **Accounting**: chart of accounts, banks, vendors, transactions, P&L / cash-position reports, billing auto-link
12. **SaaS**: subscription_plans + tenant_subscriptions, subscription middleware, Paystack + PayNow, jurisdiction service
13. **WhatsApp Bot**: menu-mode state machine, AI-mode Claude tool-use loop, media (PDF/image), parent_notifier gate
14. **Polish**: Error pages, empty states, loading states, mobile audit, worker setup, seed data, deployment

## Recent architecture changes (since 2026-03-09)

Kept as a running log — most-recent first.

- **Parent capture at student enrollment** (`StudentCreate.parents[]` + inline UI section in `students/create.html`) — admins now attach parent details (name, email, phone, primary flag, "send WhatsApp signup link" checkbox) as part of Add Student. Existing parents get linked + notified "new child added"; new parents get invited (email + optional WhatsApp signup nudge). Per-parent failures never roll back the student — response returns `{student, parent_results}`.
- **Parent-notifier gate** — every parent-facing WhatsApp goes through `parent_notifier._can_notify_whatsapp` with 6 conditions; each fail path logs at INFO with the specific reason so operators can grep.
- **`parent_signup` template** — replaces `parent_invite` (Meta locked the deleted name for 30 days). Body is pure copy, invitation code moved to a dynamic URL button. Category is Marketing (Meta forced it — the auto-opt-in gate covers us).
- **Events module** — full end-to-end: create, audience resolution by scope, ICS calendar invites, signed one-tap RSVP URLs, T-24h and T-1h WhatsApp reminders via an arq worker.
- **Orphan cleanup** — student deletes and parent-unlinks now cascade to soft-delete parents whose only child at this tenant has left; pending invitations for departing students get marked EXPIRED.
- **11 Meta-approved templates** — the full transactional set (attendance, welcome, reports, invoices trio, events trio, announcement, parent_signup) with proper spacing, dynamic URL buttons where appropriate, and per-template category assignments.
- **Deep-link auth preservation** — `billing._require_auth` preserves `?next=` on login redirects so WhatsApp URL buttons land the parent on the target page after login. Same pattern still needed across other web modules (see task #124).
- **Mobile-friendliness first pass** — reports create/edit tables scroll edge-to-edge, sticky Save bar clears the mobile bottom nav, attendance floating Save button repositioned, timetable list stops clipping, accounting report tables wrapped for scroll.
