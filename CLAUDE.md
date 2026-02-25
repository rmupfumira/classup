# ClassUp v2 — System Architecture & Implementation Specification

> **Purpose**: This document is the single source of truth for a coding agent to build the ClassUp platform from scratch. Every architectural decision, data model, API contract, UI layout, and integration detail is specified here. If something is ambiguous, it is a bug in this document.

> **Last Updated**: 2026-02-13

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Technology Stack](#2-technology-stack)
3. [Project Structure](#3-project-structure)
4. [Configuration & Environment](#4-configuration--environment)
5. [Database Architecture](#5-database-architecture)
6. [Multi-Tenancy Architecture](#6-multi-tenancy-architecture)
7. [Authentication & Authorization](#7-authentication--authorization)
8. [API Design](#8-api-design)
9. [Frontend Architecture](#9-frontend-architecture)
10. [Core Modules](#10-core-modules)
11. [Messaging & Communication](#11-messaging--communication)
12. [File Management](#12-file-management)
13. [Reporting System](#13-reporting-system)
14. [WhatsApp Integration](#14-whatsapp-integration)
15. [Email System](#15-email-system)
16. [WebSocket Real-Time System](#16-websocket-real-time-system)
17. [Tenant Onboarding Wizard](#17-tenant-onboarding-wizard)
18. [Bulk CSV Import](#18-bulk-csv-import)
19. [Internationalization (i18n)](#19-internationalization-i18n)
20. [Webhook System](#20-webhook-system)
21. [Background Tasks](#21-background-tasks)
22. [Error Handling](#22-error-handling)
23. [Testing Strategy](#23-testing-strategy)
24. [Deployment to Railway](#24-deployment-to-railway)
25. [Migration Guide from ClassUp v1](#25-migration-guide-from-classup-v1)

---

## 1. Executive Summary

ClassUp v2 is a multi-tenant SaaS platform for managing schools, daycare centers, and early childhood education facilities. It replaces the existing Spring Boot + Flutter implementation with a Python/FastAPI backend serving server-rendered HTML (Jinja2 + Tailwind CSS + vanilla JavaScript) and a RESTful JSON API suitable for third-party app development.

### Key Design Principles

- **Server-Side Rendering First**: Jinja2 templates with Tailwind CSS. JavaScript used only for interactivity (modals, live search, WebSocket events, form validation). No JS framework.
- **API-First Design**: Every action the UI performs goes through the REST API. The HTML views are thin wrappers around the same API endpoints, enabling third-party apps to build on the same API.
- **Multi-Tenant Isolation**: Row-level tenant isolation using `tenant_id` foreign keys on every table. No shared data leaks.
- **Mobile-First Responsive**: Every page designed mobile-first with Tailwind's responsive utilities. Touch-friendly targets (minimum 44px), swipe gestures where appropriate.
- **Extensibility**: Plugin-like module system. Each domain (attendance, messaging, reports) is a self-contained FastAPI router with its own models, services, and templates.
- **Convention Over Configuration**: Consistent patterns across all modules so a coding agent can learn one module and replicate the pattern.

### What This System Does

- Manages students, teachers, classes, and parents across multiple schools (tenants)
- Tracks daily attendance with check-in/check-out
- Generates flexible, template-driven reports (daily activity reports for daycare, progress reports for schools, report cards)
- Facilitates communication between teachers and parents via in-app messaging, email, and WhatsApp
- Shares photos and documents securely
- Supports multiple education types: daycare, primary, high school, K-12, combined
- Provides real-time updates via WebSockets for notifications, chat, and attendance

---

## 2. Technology Stack

### Backend

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Runtime | Python | 3.12+ | Language runtime |
| Framework | FastAPI | 0.115+ | Async web framework |
| ASGI Server | Uvicorn | 0.34+ | Production ASGI server |
| ORM | SQLAlchemy | 2.0+ | Database ORM (async mode) |
| Migrations | Alembic | 1.14+ | Database schema migrations |
| Validation | Pydantic | 2.0+ | Request/response validation |
| Auth | PyJWT + passlib[bcrypt] | Latest | JWT tokens + password hashing |
| Templates | Jinja2 | 3.1+ | Server-side HTML rendering |
| Task Queue | arq | 0.26+ | Background task processing (Redis-backed) |
| WebSockets | FastAPI WebSocket | Built-in | Real-time communication |
| Email | aiosmtplib + resend | Latest | Transactional email (SMTP or Resend, configurable via super admin UI) |
| File Storage | boto3 | Latest | Cloudflare R2 (S3-compatible) |
| WhatsApp | httpx | Latest | Meta Cloud API HTTP client |
| CSV Parsing | pandas | Latest | Bulk import processing |
| i18n | babel + custom | Latest | Internationalization |
| Testing | pytest + pytest-asyncio + httpx | Latest | Test framework |

### Frontend

| Component | Technology | Purpose |
|-----------|-----------|---------|
| CSS Framework | Tailwind CSS 3.4+ (CDN) | Utility-first styling |
| JavaScript | Vanilla ES2022+ | Interactivity, no framework |
| Icons | Heroicons (SVG inline) | UI icons |
| Charts | Chart.js 4+ (CDN) | Dashboard charts |
| Date Picker | Flatpickr (CDN) | Date/time inputs |
| File Upload | Dropzone.js (CDN) | Drag-and-drop uploads |
| WebSocket | Native WebSocket API | Real-time updates |

### Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Hosting | Railway | Application + PostgreSQL + Redis |
| Database | PostgreSQL 16 | Primary datastore |
| Cache / Pub-Sub | Redis 7 | Caching, task queue, WebSocket pub-sub |
| File Storage | Cloudflare R2 | Photos, documents (S3-compatible) |
| Email | SMTP or Resend | Transactional email delivery (provider chosen at runtime via super admin UI) |
| WhatsApp | Meta Cloud API | Two-way WhatsApp messaging |
| DNS | Cloudflare | DNS management |

---

## 3. Project Structure

```
classup/
├── alembic/                          # Database migrations
│   ├── versions/                     # Migration scripts
│   ├── env.py
│   └── alembic.ini
├── app/
│   ├── __init__.py
│   ├── main.py                       # FastAPI app factory
│   ├── config.py                     # Settings (pydantic-settings)
│   ├── database.py                   # SQLAlchemy async engine + session
│   ├── dependencies.py               # Shared FastAPI dependencies
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── tenant.py                 # Tenant context middleware
│   │   ├── auth.py                   # JWT auth middleware
│   │   └── i18n.py                   # Language detection middleware
│   ├── models/                       # SQLAlchemy models (one file per domain)
│   │   ├── __init__.py               # Re-exports all models
│   │   ├── base.py                   # Base model with id, tenant_id, timestamps
│   │   ├── tenant.py                 # Tenant, TenantSettings
│   │   ├── user.py                   # User, Role enum
│   │   ├── student.py                # Student, ParentStudent, AgeGroup enum
│   │   ├── school_class.py           # SchoolClass, TeacherClass
│   │   ├── attendance.py             # AttendanceRecord
│   │   ├── message.py                # Message, MessageAttachment, MessageType
│   │   ├── report.py                 # DailyReport, ReportTemplate
│   │   ├── file_entity.py            # FileEntity
│   │   ├── invitation.py             # ParentInvitation
│   │   ├── notification.py           # Notification
│   │   ├── webhook.py                # WebhookEndpoint, WebhookEvent
│   │   └── import_job.py             # BulkImportJob
│   ├── schemas/                      # Pydantic request/response schemas
│   │   ├── __init__.py
│   │   ├── common.py                 # Pagination, APIResponse wrapper
│   │   ├── auth.py                   # Login, Register, Token schemas
│   │   ├── tenant.py
│   │   ├── user.py
│   │   ├── student.py
│   │   ├── school_class.py
│   │   ├── attendance.py
│   │   ├── message.py
│   │   ├── report.py
│   │   ├── file.py
│   │   ├── invitation.py
│   │   ├── webhook.py
│   │   └── import_job.py
│   ├── services/                     # Business logic layer
│   │   ├── __init__.py
│   │   ├── auth_service.py
│   │   ├── tenant_service.py
│   │   ├── user_service.py
│   │   ├── student_service.py
│   │   ├── class_service.py
│   │   ├── attendance_service.py
│   │   ├── message_service.py
│   │   ├── report_service.py
│   │   ├── file_service.py           # R2 upload/download/presigned URLs
│   │   ├── invitation_service.py
│   │   ├── notification_service.py
│   │   ├── email_service.py          # Email integration (SMTP / Resend, config from DB)
│   │   ├── whatsapp_service.py       # Meta Cloud API integration
│   │   ├── webhook_service.py
│   │   ├── import_service.py         # CSV bulk import
│   │   ├── i18n_service.py           # Translation service
│   │   └── realtime_service.py       # WebSocket connection manager
│   ├── api/                          # JSON API routes (versioned)
│   │   ├── __init__.py
│   │   ├── v1/
│   │   │   ├── __init__.py           # v1 router aggregator
│   │   │   ├── auth.py
│   │   │   ├── tenants.py
│   │   │   ├── users.py
│   │   │   ├── students.py
│   │   │   ├── classes.py
│   │   │   ├── attendance.py
│   │   │   ├── messages.py
│   │   │   ├── reports.py
│   │   │   ├── files.py
│   │   │   ├── invitations.py
│   │   │   ├── webhooks.py
│   │   │   ├── imports.py
│   │   │   └── websocket.py
│   │   └── deps.py                   # API-specific dependencies
│   ├── web/                          # HTML view routes (Jinja2)
│   │   ├── __init__.py
│   │   ├── auth.py                   # Login, register, forgot password pages
│   │   ├── dashboard.py              # Role-based dashboards
│   │   ├── students.py               # Student CRUD pages
│   │   ├── classes.py                # Class management pages
│   │   ├── attendance.py             # Attendance tracking pages
│   │   ├── messages.py               # Messaging pages
│   │   ├── reports.py                # Report creation/viewing pages
│   │   ├── photos.py                 # Photo gallery pages
│   │   ├── documents.py              # Document management pages
│   │   ├── settings.py               # Tenant settings pages
│   │   ├── admin.py                  # School admin pages
│   │   ├── super_admin.py            # Platform super admin pages
│   │   ├── onboarding.py             # Tenant onboarding wizard
│   │   ├── imports.py                # Bulk import pages
│   │   └── profile.py                # User profile pages
│   ├── templates/                    # Jinja2 HTML templates
│   │   ├── base.html                 # Master layout (nav, sidebar, footer)
│   │   ├── components/               # Reusable UI components (partials)
│   │   │   ├── _navbar.html
│   │   │   ├── _sidebar.html
│   │   │   ├── _mobile_nav.html      # Bottom tab bar for mobile
│   │   │   ├── _pagination.html
│   │   │   ├── _modal.html
│   │   │   ├── _toast.html           # Toast notification component
│   │   │   ├── _data_table.html
│   │   │   ├── _form_field.html
│   │   │   ├── _file_upload.html
│   │   │   ├── _empty_state.html
│   │   │   ├── _search_bar.html
│   │   │   ├── _avatar.html
│   │   │   ├── _badge.html
│   │   │   ├── _card.html
│   │   │   ├── _stats_card.html
│   │   │   └── _confirm_dialog.html
│   │   ├── auth/
│   │   │   ├── login.html
│   │   │   ├── register.html         # Parent registration via invite
│   │   │   ├── forgot_password.html
│   │   │   └── reset_password.html
│   │   ├── dashboard/
│   │   │   ├── super_admin.html
│   │   │   ├── school_admin.html
│   │   │   ├── teacher.html
│   │   │   └── parent.html
│   │   ├── students/
│   │   │   ├── list.html
│   │   │   ├── detail.html
│   │   │   ├── create.html
│   │   │   └── edit.html
│   │   ├── classes/
│   │   │   ├── list.html
│   │   │   ├── detail.html
│   │   │   ├── create.html
│   │   │   └── manage_teachers.html
│   │   ├── attendance/
│   │   │   ├── daily.html            # Daily attendance view (bulk check-in)
│   │   │   ├── history.html
│   │   │   └── student_history.html
│   │   ├── messages/
│   │   │   ├── inbox.html
│   │   │   ├── thread.html
│   │   │   ├── compose.html
│   │   │   └── announcements.html
│   │   ├── reports/
│   │   │   ├── create.html           # Dynamic form from template
│   │   │   ├── view.html
│   │   │   ├── list.html
│   │   │   └── templates/
│   │   │       ├── manage.html       # Admin template CRUD
│   │   │       └── editor.html       # Template section/field builder
│   │   ├── photos/
│   │   │   ├── gallery.html
│   │   │   └── upload.html
│   │   ├── documents/
│   │   │   ├── list.html
│   │   │   └── upload.html
│   │   ├── settings/
│   │   │   ├── general.html
│   │   │   ├── features.html
│   │   │   ├── terminology.html
│   │   │   └── webhooks.html
│   │   ├── onboarding/
│   │   │   ├── step1_school_info.html
│   │   │   ├── step2_education_type.html
│   │   │   ├── step3_classes.html
│   │   │   ├── step4_invite_teachers.html
│   │   │   └── step5_complete.html
│   │   ├── imports/
│   │   │   ├── upload.html
│   │   │   ├── mapping.html          # Column mapping UI
│   │   │   └── results.html
│   │   ├── super_admin/
│   │   │   ├── tenants.html
│   │   │   ├── tenant_detail.html
│   │   │   └── system_stats.html
│   │   └── emails/                   # Email templates (HTML)
│   │       ├── base_email.html
│   │       ├── welcome.html
│   │       ├── parent_invite.html
│   │       ├── report_ready.html
│   │       ├── admin_notification.html
│   │       └── password_reset.html
│   ├── static/
│   │   ├── css/
│   │   │   └── app.css               # Custom styles (minimal, Tailwind handles most)
│   │   ├── js/
│   │   │   ├── app.js                # Global: CSRF, fetch wrapper, toast, modals
│   │   │   ├── websocket.js          # WebSocket client manager
│   │   │   ├── attendance.js         # Attendance page interactivity
│   │   │   ├── messages.js           # Chat/messaging interactivity
│   │   │   ├── reports.js            # Dynamic report form builder
│   │   │   ├── import.js             # CSV import column mapping
│   │   │   ├── onboarding.js         # Wizard step navigation
│   │   │   └── search.js             # Live search with debounce
│   │   └── img/
│   │       ├── logo.svg
│   │       └── empty-states/         # Illustration SVGs for empty states
│   └── utils/
│       ├── __init__.py
│       ├── security.py               # Password hashing, JWT encode/decode
│       ├── pagination.py             # Pagination helper
│       ├── tenant_context.py         # Context variable for current tenant
│       ├── permissions.py            # Role-based permission decorators
│       ├── validators.py             # Custom validators (phone, email, etc.)
│       └── helpers.py                # Date formatting, slug generation, etc.
├── translations/                     # i18n translation files
│   ├── en/
│   │   └── messages.json
│   └── af/
│       └── messages.json
├── tests/
│   ├── conftest.py                   # Test fixtures, test database, test client
│   ├── factories.py                  # Factory Boy model factories
│   ├── test_api/
│   │   ├── test_auth.py
│   │   ├── test_students.py
│   │   ├── test_attendance.py
│   │   └── ...
│   └── test_services/
│       ├── test_auth_service.py
│       ├── test_import_service.py
│       └── ...
├── scripts/
│   ├── seed.py                       # Development seed data
│   └── create_super_admin.py         # CLI to create initial super admin
├── Dockerfile
├── docker-compose.yml                # Local dev (PostgreSQL + Redis)
├── railway.toml                      # Railway deployment config
├── pyproject.toml                    # Project metadata + dependencies
├── requirements.txt                  # Pinned dependencies (generated)
├── .env.example                      # Environment variable template
├── CLAUDE.md                         # Coding agent instructions
└── README.md
```

---

## 4. Configuration & Environment

### Environment Variables

```bash
# === Application ===
APP_NAME=ClassUp
APP_ENV=production                     # development | staging | production
APP_SECRET_KEY=<random-64-char-hex>    # Used for JWT signing + CSRF
APP_BASE_URL=https://classup.co.za     # Public URL of the application
APP_DEBUG=false
APP_LOG_LEVEL=INFO

# === Database ===
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/classup
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=10

# === Redis ===
REDIS_URL=redis://default:pass@host:6379

# === Authentication ===
JWT_SECRET_KEY=${APP_SECRET_KEY}
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440   # 24 hours
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# === Cloudflare R2 ===
R2_ACCOUNT_ID=<cloudflare-account-id>
R2_ACCESS_KEY_ID=<access-key>
R2_SECRET_ACCESS_KEY=<secret-key>
R2_BUCKET_NAME=classup-files
R2_PUBLIC_URL=https://files.classup.co.za  # Optional: custom domain for public files

# === Email ===
# Email provider (SMTP or Resend) is configured at runtime via the super admin UI.
# Settings are stored in the `system_settings` DB table (key='email_config').
# Fallback defaults only:
EMAIL_FROM_ADDRESS=notifications@classup.co.za
EMAIL_FROM_NAME=ClassUp

# === WhatsApp (Meta Cloud API) ===
WHATSAPP_API_URL=https://graph.facebook.com/v21.0
WHATSAPP_PHONE_NUMBER_ID=<phone-number-id>
WHATSAPP_ACCESS_TOKEN=<permanent-token>
WHATSAPP_VERIFY_TOKEN=<webhook-verify-token>
WHATSAPP_BUSINESS_ACCOUNT_ID=<business-account-id>

# === Defaults ===
DEFAULT_LANGUAGE=en
SUPPORTED_LANGUAGES=en,af
MAX_UPLOAD_SIZE_MB=10
INVITATION_CODE_EXPIRY_DAYS=7
```

### Settings Class

```python
# app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "ClassUp"
    app_env: str = "development"
    app_secret_key: str
    app_base_url: str = "http://localhost:8000"
    app_debug: bool = False
    app_log_level: str = "INFO"

    database_url: str
    database_pool_size: int = 20
    database_max_overflow: int = 10

    redis_url: str

    jwt_secret_key: str | None = None  # Falls back to app_secret_key
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440
    jwt_refresh_token_expire_days: int = 30

    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str = "classup-files"
    r2_public_url: str | None = None

    # Email provider config is stored in DB (system_settings table), not env vars.
    # These are fallback defaults only.
    email_from_address: str = "notifications@classup.co.za"
    email_from_name: str = "ClassUp"

    whatsapp_api_url: str = "https://graph.facebook.com/v21.0"
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_business_account_id: str = ""

    default_language: str = "en"
    supported_languages: str = "en,af"
    max_upload_size_mb: int = 10
    invitation_code_expiry_days: int = 7

    @property
    def effective_jwt_secret(self) -> str:
        return self.jwt_secret_key or self.app_secret_key
```

---

## 5. Database Architecture

### Design Principles

1. **UUIDs for all primary keys**: Use `uuid7()` (time-sortable) for better index performance than UUIDv4.
2. **`tenant_id` on every tenant-scoped table**: Never optional. Composite indexes always include `tenant_id` first.
3. **Soft deletes**: `deleted_at` timestamp column on all major entities. Queries filter `WHERE deleted_at IS NULL` by default.
4. **JSONB for flexible data**: Tenant settings, report data, template definitions, emergency contacts.
5. **Timestamps on everything**: `created_at` and `updated_at` (auto-set via SQLAlchemy event hooks).
6. **Explicit join tables**: Many-to-many relationships use named join tables with additional metadata columns.

### Base Model

```python
# app/models/base.py
import uuid
from datetime import datetime
from uuid_extensions import uuid7
from sqlalchemy import Column, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class TenantScopedModel(Base):
    """Abstract base for all tenant-scoped entities."""
    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
```

### Entity-Relationship Diagram (Textual)

```
tenants
  ├── users (tenant_id FK)
  │     ├── SUPER_ADMIN (tenant_id is NULL for super admins)
  │     ├── SCHOOL_ADMIN
  │     ├── TEACHER
  │     └── PARENT
  ├── students (tenant_id FK)
  │     └── parent_students (student_id, user_id) [join table]
  ├── school_classes (tenant_id FK)
  │     └── teacher_classes (class_id, user_id, is_primary) [join table]
  ├── attendance_records (tenant_id FK, student_id, class_id)
  ├── messages (tenant_id FK, sender_id)
  │     ├── message_recipients (message_id, user_id)
  │     └── message_attachments (message_id, file_entity_id)
  ├── daily_reports (tenant_id FK, student_id, template_id)
  ├── report_templates (tenant_id FK)
  ├── file_entities (tenant_id FK)
  ├── parent_invitations (tenant_id FK, student_id)
  ├── notifications (tenant_id FK, user_id)
  ├── webhook_endpoints (tenant_id FK)
  ├── webhook_events (endpoint_id)
  └── bulk_import_jobs (tenant_id FK)
```

### Complete Schema Definitions

#### `tenants`

```sql
CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,               -- "Sunshine Daycare"
    slug            VARCHAR(100) NOT NULL UNIQUE,         -- "sunshine-daycare" (used in URLs)
    email           VARCHAR(255) NOT NULL,                -- Primary contact email
    phone           VARCHAR(50),
    address         TEXT,
    logo_path       VARCHAR(500),                         -- R2 storage path
    education_type  VARCHAR(50) NOT NULL DEFAULT 'DAYCARE', -- DAYCARE|PRIMARY_SCHOOL|HIGH_SCHOOL|K12|COMBINED
    settings        JSONB NOT NULL DEFAULT '{}'::jsonb,   -- See TenantSettings schema below
    is_active       BOOLEAN NOT NULL DEFAULT true,
    onboarding_completed BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_tenants_slug ON tenants(slug) WHERE deleted_at IS NULL;
```

**`tenants.settings` JSONB schema:**

```json
{
  "education_type": "DAYCARE",
  "enabled_grade_levels": ["INFANT", "TODDLER", "PRESCHOOL"],
  "features": {
    "attendance_tracking": true,
    "messaging": true,
    "photo_sharing": true,
    "document_sharing": true,
    "daily_reports": true,
    "parent_communication": true,
    "nap_tracking": true,
    "bathroom_tracking": true,
    "fluid_tracking": true,
    "meal_tracking": true,
    "diaper_tracking": true,
    "homework_tracking": false,
    "grade_tracking": false,
    "behavior_tracking": false,
    "timetable_management": false,
    "subject_management": false,
    "exam_management": false,
    "disciplinary_records": false,
    "whatsapp_enabled": false
  },
  "terminology": {
    "student": "child",
    "students": "children",
    "teacher": "educator",
    "teachers": "educators",
    "class": "class",
    "classes": "classes",
    "parent": "parent",
    "parents": "parents"
  },
  "report_config": {
    "default_report_type": "DAILY_ACTIVITY",
    "enabled_sections": ["meals", "nap", "fluids", "bathroom", "activities", "notes"]
  },
  "whatsapp": {
    "enabled": false,
    "phone_number_id": null,
    "send_attendance_alerts": true,
    "send_report_notifications": true,
    "send_announcements": true
  },
  "branding": {
    "primary_color": "#7C3AED",
    "secondary_color": "#5B21B6"
  },
  "timezone": "Africa/Johannesburg",
  "language": "en"
}
```

#### `users`

```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID REFERENCES tenants(id),          -- NULL for SUPER_ADMIN
    email           VARCHAR(255) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    phone           VARCHAR(50),
    role            VARCHAR(20) NOT NULL,                  -- SUPER_ADMIN|SCHOOL_ADMIN|TEACHER|PARENT
    avatar_path     VARCHAR(500),                          -- R2 storage path
    is_active       BOOLEAN NOT NULL DEFAULT true,
    language        VARCHAR(5) NOT NULL DEFAULT 'en',      -- User's preferred language
    whatsapp_phone  VARCHAR(50),                           -- WhatsApp number (E.164 format)
    whatsapp_opted_in BOOLEAN NOT NULL DEFAULT false,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

-- Email unique per tenant (NULL tenant_id for super admins handled separately)
CREATE UNIQUE INDEX idx_users_email_tenant
    ON users(email, tenant_id) WHERE deleted_at IS NULL AND tenant_id IS NOT NULL;
CREATE UNIQUE INDEX idx_users_email_super
    ON users(email) WHERE deleted_at IS NULL AND tenant_id IS NULL;
CREATE INDEX idx_users_tenant_role ON users(tenant_id, role) WHERE deleted_at IS NULL;
```

#### `students`

```sql
CREATE TABLE students (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    date_of_birth   DATE,
    gender          VARCHAR(10),                           -- MALE|FEMALE|OTHER
    age_group       VARCHAR(30),                           -- INFANT|TODDLER|PRESCHOOL|KINDERGARTEN|GRADE_R|GRADE_1..GRADE_12
    grade_level     VARCHAR(50),                           -- Free-text: "Grade 1", "Year 7", "Form 1"
    class_id        UUID REFERENCES school_classes(id),    -- Current class assignment
    photo_path      VARCHAR(500),
    medical_info    TEXT,
    allergies       TEXT,
    emergency_contacts JSONB NOT NULL DEFAULT '[]'::jsonb, -- [{name, phone, relationship}]
    notes           TEXT,
    enrollment_date DATE NOT NULL DEFAULT CURRENT_DATE,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_students_tenant_class ON students(tenant_id, class_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_students_tenant_age ON students(tenant_id, age_group) WHERE deleted_at IS NULL;
```

#### `parent_students` (Join Table)

```sql
CREATE TABLE parent_students (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id   UUID NOT NULL REFERENCES users(id),
    student_id  UUID NOT NULL REFERENCES students(id),
    relationship VARCHAR(30) NOT NULL DEFAULT 'PARENT',    -- PARENT|GUARDIAN|OTHER
    is_primary  BOOLEAN NOT NULL DEFAULT false,            -- Primary contact
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(parent_id, student_id)
);

CREATE INDEX idx_parent_students_parent ON parent_students(parent_id);
CREATE INDEX idx_parent_students_student ON parent_students(student_id);
```

#### `school_classes`

```sql
CREATE TABLE school_classes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id),
    name        VARCHAR(100) NOT NULL,                     -- "Butterfly Room", "Grade 3A"
    description TEXT,
    age_group   VARCHAR(30),                               -- For daycare: INFANT|TODDLER etc.
    grade_level VARCHAR(50),                               -- For schools: "Grade 3"
    capacity    INTEGER,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX idx_classes_tenant ON school_classes(tenant_id) WHERE deleted_at IS NULL;
```

#### `teacher_classes` (Join Table)

```sql
CREATE TABLE teacher_classes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id  UUID NOT NULL REFERENCES users(id),
    class_id    UUID NOT NULL REFERENCES school_classes(id),
    is_primary  BOOLEAN NOT NULL DEFAULT false,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(teacher_id, class_id)
);

CREATE INDEX idx_teacher_classes_teacher ON teacher_classes(teacher_id);
CREATE INDEX idx_teacher_classes_class ON teacher_classes(class_id);
```

#### `attendance_records`

```sql
CREATE TABLE attendance_records (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    student_id      UUID NOT NULL REFERENCES students(id),
    class_id        UUID NOT NULL REFERENCES school_classes(id),
    date            DATE NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'ABSENT',  -- PRESENT|ABSENT|LATE|EXCUSED
    check_in_time   TIMESTAMPTZ,
    check_out_time  TIMESTAMPTZ,
    recorded_by     UUID NOT NULL REFERENCES users(id),     -- Teacher who recorded
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(student_id, date)                                -- One record per student per day
);

CREATE INDEX idx_attendance_tenant_date ON attendance_records(tenant_id, date);
CREATE INDEX idx_attendance_class_date ON attendance_records(class_id, date);
```

#### `messages`

```sql
CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    sender_id       UUID NOT NULL REFERENCES users(id),
    message_type    VARCHAR(30) NOT NULL,
        -- ANNOUNCEMENT: School-wide from admin
        -- CLASS_ANNOUNCEMENT: Class-wide from teacher
        -- STUDENT_MESSAGE: About specific student (teacher→parent or parent→teacher)
        -- REPLY: Reply in a thread
        -- CLASS_PHOTO: Photo shared with class
        -- STUDENT_PHOTO: Photo shared about specific student
        -- CLASS_DOCUMENT: Document shared with class
        -- STUDENT_DOCUMENT: Document for specific student
        -- SCHOOL_DOCUMENT: School-wide document from admin
    subject         VARCHAR(255),
    body            TEXT NOT NULL,
    class_id        UUID REFERENCES school_classes(id),     -- For class-scoped messages
    student_id      UUID REFERENCES students(id),           -- For student-scoped messages
    parent_message_id UUID REFERENCES messages(id),         -- For threaded replies
    is_read         BOOLEAN NOT NULL DEFAULT false,          -- Deprecated: use message_recipients
    status          VARCHAR(20) NOT NULL DEFAULT 'SENT',     -- SENT|DELIVERED|READ
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_messages_tenant_type ON messages(tenant_id, message_type) WHERE deleted_at IS NULL;
CREATE INDEX idx_messages_thread ON messages(parent_message_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_messages_class ON messages(class_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_messages_student ON messages(student_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_messages_sender ON messages(sender_id) WHERE deleted_at IS NULL;
```

#### `message_recipients`

```sql
CREATE TABLE message_recipients (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id  UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id),
    is_read     BOOLEAN NOT NULL DEFAULT false,
    read_at     TIMESTAMPTZ,

    UNIQUE(message_id, user_id)
);

CREATE INDEX idx_msg_recipients_user_unread ON message_recipients(user_id, is_read)
    WHERE is_read = false;
```

#### `message_attachments`

```sql
CREATE TABLE message_attachments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    file_entity_id  UUID NOT NULL REFERENCES file_entities(id),
    display_order   INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_msg_attachments_message ON message_attachments(message_id);
```

#### `file_entities`

```sql
CREATE TABLE file_entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    storage_path    VARCHAR(500) NOT NULL,                  -- R2 key: {tenant_id}/{type}/{entity_id}/{filename}
    original_name   VARCHAR(255) NOT NULL,
    content_type    VARCHAR(100) NOT NULL,                  -- MIME type
    file_size       BIGINT NOT NULL,                        -- Bytes
    file_category   VARCHAR(20) NOT NULL,                   -- PHOTO|DOCUMENT|AVATAR|LOGO
    uploaded_by     UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_files_tenant ON file_entities(tenant_id) WHERE deleted_at IS NULL;
```

#### `daily_reports`

```sql
CREATE TABLE daily_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    student_id      UUID NOT NULL REFERENCES students(id),
    class_id        UUID NOT NULL REFERENCES school_classes(id),
    template_id     UUID NOT NULL REFERENCES report_templates(id),
    report_date     DATE NOT NULL,
    report_data     JSONB NOT NULL DEFAULT '{}'::jsonb,     -- Polymorphic content matching template
    status          VARCHAR(20) NOT NULL DEFAULT 'DRAFT',   -- DRAFT|FINALIZED
    finalized_at    TIMESTAMPTZ,
    created_by      UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ,

    UNIQUE(student_id, template_id, report_date)
);

CREATE INDEX idx_reports_tenant_date ON daily_reports(tenant_id, report_date) WHERE deleted_at IS NULL;
CREATE INDEX idx_reports_student ON daily_reports(student_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_reports_class_date ON daily_reports(class_id, report_date) WHERE deleted_at IS NULL;
CREATE GIN INDEX idx_reports_data ON daily_reports USING GIN (report_data);
```

#### `report_templates`

```sql
CREATE TABLE report_templates (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID NOT NULL REFERENCES tenants(id),
    name                    VARCHAR(255) NOT NULL,           -- "Daycare Daily Report"
    description             TEXT,
    report_type             VARCHAR(30) NOT NULL,             -- DAILY_ACTIVITY|PROGRESS_REPORT|REPORT_CARD
    frequency               VARCHAR(20) NOT NULL DEFAULT 'DAILY', -- DAILY|WEEKLY|TERMLY
    applies_to_grade_level  VARCHAR(255),                    -- Comma-separated: "TODDLER,PRESCHOOL" or "Grade 1,Grade 2"
    sections                JSONB NOT NULL DEFAULT '[]'::jsonb, -- See Template Sections schema
    display_order           INTEGER NOT NULL DEFAULT 0,
    is_active               BOOLEAN NOT NULL DEFAULT true,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at              TIMESTAMPTZ
);

CREATE INDEX idx_templates_tenant ON report_templates(tenant_id) WHERE deleted_at IS NULL AND is_active = true;
```

**`report_templates.sections` JSONB schema:**

```json
[
  {
    "id": "meals",
    "title": "Meals & Nutrition",
    "type": "CHECKLIST",
    "display_order": 1,
    "fields": [
      {
        "id": "breakfast_amount",
        "label": "Breakfast",
        "type": "SELECT",
        "options": ["All", "Most", "Some", "None", "N/A"],
        "required": true
      },
      {
        "id": "breakfast_notes",
        "label": "Breakfast Notes",
        "type": "TEXT",
        "required": false
      }
    ]
  },
  {
    "id": "fluids",
    "title": "Fluids & Hydration",
    "type": "REPEATABLE_ENTRIES",
    "display_order": 2,
    "fields": [
      { "id": "time", "label": "Time", "type": "TIME", "required": true },
      { "id": "amount", "label": "Amount (ml)", "type": "NUMBER", "required": true },
      { "id": "type", "label": "Type", "type": "SELECT", "options": ["Water", "Milk", "Juice", "Formula"], "required": true }
    ]
  },
  {
    "id": "teacher_notes",
    "title": "Teacher Notes",
    "type": "NARRATIVE",
    "display_order": 3,
    "fields": [
      { "id": "notes", "label": "Notes", "type": "TEXTAREA", "required": false }
    ]
  }
]
```

#### `parent_invitations`

```sql
CREATE TABLE parent_invitations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    student_id      UUID NOT NULL REFERENCES students(id),
    email           VARCHAR(255) NOT NULL,
    invitation_code VARCHAR(8) NOT NULL UNIQUE,
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',  -- PENDING|ACCEPTED|EXPIRED
    created_by      UUID NOT NULL REFERENCES users(id),
    expires_at      TIMESTAMPTZ NOT NULL,
    accepted_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_invitations_code ON parent_invitations(invitation_code) WHERE status = 'PENDING';
CREATE INDEX idx_invitations_email ON parent_invitations(email, tenant_id) WHERE status = 'PENDING';
```

#### `notifications`

```sql
CREATE TABLE notifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID REFERENCES tenants(id),            -- NULL for super admin notifications
    user_id         UUID NOT NULL REFERENCES users(id),
    title           VARCHAR(255) NOT NULL,
    body            TEXT NOT NULL,
    notification_type VARCHAR(50) NOT NULL,                  -- See types below
    reference_type  VARCHAR(50),                             -- "message", "report", "attendance", etc.
    reference_id    UUID,                                    -- ID of related entity
    is_read         BOOLEAN NOT NULL DEFAULT false,
    read_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Notification types:
-- ATTENDANCE_MARKED, ATTENDANCE_LATE, REPORT_FINALIZED, REPORT_READY,
-- MESSAGE_RECEIVED, ANNOUNCEMENT, PHOTO_SHARED, DOCUMENT_SHARED,
-- INVITATION_SENT, TEACHER_ADDED, STUDENT_ADDED, CLASS_CREATED,
-- SETTINGS_CHANGED, IMPORT_COMPLETED, WHATSAPP_MESSAGE

CREATE INDEX idx_notifications_user_unread ON notifications(user_id, is_read)
    WHERE is_read = false;
```

#### `webhook_endpoints`

```sql
CREATE TABLE webhook_endpoints (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id),
    url         VARCHAR(500) NOT NULL,
    secret      VARCHAR(255) NOT NULL,                      -- HMAC signing secret
    events      JSONB NOT NULL DEFAULT '[]'::jsonb,         -- ["student.created", "attendance.marked"]
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_webhooks_tenant ON webhook_endpoints(tenant_id) WHERE is_active = true;
```

#### `webhook_events`

```sql
CREATE TABLE webhook_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    endpoint_id     UUID NOT NULL REFERENCES webhook_endpoints(id),
    event_type      VARCHAR(100) NOT NULL,
    payload         JSONB NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',  -- PENDING|DELIVERED|FAILED
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    response_code   INTEGER,
    response_body   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_webhook_events_status ON webhook_events(status) WHERE status IN ('PENDING', 'FAILED');
```

#### `bulk_import_jobs`

```sql
CREATE TABLE bulk_import_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    import_type     VARCHAR(30) NOT NULL,                    -- STUDENTS|TEACHERS|PARENTS
    file_name       VARCHAR(255) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',  -- PENDING|PROCESSING|COMPLETED|FAILED
    total_rows      INTEGER,
    processed_rows  INTEGER DEFAULT 0,
    success_count   INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    errors          JSONB DEFAULT '[]'::jsonb,               -- [{row: 3, field: "email", error: "Invalid format"}]
    column_mapping  JSONB DEFAULT '{}'::jsonb,               -- {csv_column: db_field} mapping
    created_by      UUID NOT NULL REFERENCES users(id),
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_imports_tenant ON bulk_import_jobs(tenant_id);
```

---

## 6. Multi-Tenancy Architecture

### Tenant Isolation Strategy

**Approach**: Shared database, shared schema, row-level isolation via `tenant_id` column.

This is the simplest approach for a SaaS with many small tenants (schools). Every tenant-scoped query MUST include `WHERE tenant_id = :tenant_id`.

### Tenant Context Flow

```
HTTP Request
  → TenantMiddleware extracts tenant_id from JWT claims
  → Sets context variable (Python contextvars)
  → All service methods read tenant_id from context
  → All repository queries include tenant_id filter
  → Response
```

### Implementation

```python
# app/utils/tenant_context.py
import contextvars
import uuid

_tenant_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "tenant_id", default=None
)
_current_user_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "current_user_id", default=None
)
_current_user_role: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_role", default=None
)

def get_tenant_id() -> uuid.UUID:
    tid = _tenant_id.get()
    if tid is None:
        raise RuntimeError("Tenant context not set")
    return tid

def set_tenant_id(tid: uuid.UUID) -> None:
    _tenant_id.set(tid)

def get_current_user_id() -> uuid.UUID:
    uid = _current_user_id.get()
    if uid is None:
        raise RuntimeError("User context not set")
    return uid

# ... similar for set_current_user_id, get/set_current_user_role
```

```python
# app/middleware/tenant.py
from starlette.middleware.base import BaseHTTPMiddleware
from app.utils.tenant_context import set_tenant_id, set_current_user_id, set_current_user_role
from app.utils.security import decode_jwt

class TenantMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS = {"/", "/login", "/register", "/api/v1/auth/login",
                    "/api/v1/auth/register", "/health", "/static", "/favicon.ico",
                    "/api/v1/whatsapp/webhook"}

    async def dispatch(self, request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in self.EXEMPT_PATHS):
            return await call_next(request)

        token = request.cookies.get("access_token") or \
                request.headers.get("Authorization", "").removeprefix("Bearer ")

        if token:
            payload = decode_jwt(token)
            if payload:
                if payload.get("tenant_id"):
                    set_tenant_id(uuid.UUID(payload["tenant_id"]))
                set_current_user_id(uuid.UUID(payload["sub"]))
                set_current_user_role(payload.get("role"))

        response = await call_next(request)
        return response
```

### Service Pattern (All Services Follow This)

```python
# Every service method that touches tenant data MUST start with:
async def get_students(self, db: AsyncSession, class_id: UUID | None = None) -> list[Student]:
    tenant_id = get_tenant_id()  # Raises if not set
    query = select(Student).where(
        Student.tenant_id == tenant_id,
        Student.deleted_at.is_(None)
    )
    if class_id:
        query = query.where(Student.class_id == class_id)
    result = await db.execute(query)
    return result.scalars().all()
```

### Critical Rule

> **NEVER** write a query against a tenant-scoped table without filtering by `tenant_id`. There are ZERO exceptions. If a SUPER_ADMIN needs cross-tenant data, use a separate code path that explicitly documents why it bypasses tenant filtering.

---

## 7. Authentication & Authorization

### Authentication Flow

```
1. User submits email + password to POST /api/v1/auth/login
2. Server validates credentials against bcrypt hash
3. Server issues JWT containing: {sub: user_id, tenant_id, role, exp}
4. JWT stored in:
   - HttpOnly cookie (for web/HTML views) — name: "access_token"
   - Response body (for API consumers) — {"access_token": "...", "token_type": "bearer"}
5. All subsequent requests:
   - Web views: cookie sent automatically
   - API clients: Authorization: Bearer <token> header
6. Server validates JWT on every request via TenantMiddleware
```

### JWT Payload

```json
{
  "sub": "user-uuid",
  "tenant_id": "tenant-uuid",
  "role": "TEACHER",
  "name": "Jane Doe",
  "exp": 1707868800,
  "iat": 1707782400,
  "jti": "unique-token-id"
}
```

### Role Hierarchy

```
SUPER_ADMIN  →  Full platform access (no tenant_id)
  └── SCHOOL_ADMIN  →  Full access within their tenant
        └── TEACHER  →  Access to assigned classes + their students
              └── PARENT  →  Read-only access to own children's data
```

### Permission Decorator

```python
# app/utils/permissions.py
from functools import wraps
from fastapi import HTTPException

def require_role(*allowed_roles: str):
    """Decorator for route handlers that enforces role-based access."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            role = get_current_user_role()
            if role not in allowed_roles and role != "SUPER_ADMIN":
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# Usage in routes:
@router.post("/students")
@require_role("SCHOOL_ADMIN", "TEACHER")
async def create_student(...):
    ...
```

### Permission Matrix

| Resource | SUPER_ADMIN | SCHOOL_ADMIN | TEACHER | PARENT |
|----------|:-----------:|:------------:|:-------:|:------:|
| Tenants CRUD | ✅ | ❌ | ❌ | ❌ |
| Users: create admin | ✅ | ✅ | ❌ | ❌ |
| Users: create teacher | ✅ | ✅ | ❌ | ❌ |
| Classes CRUD | ✅ | ✅ | ❌ | ❌ |
| Students CRUD | ✅ | ✅ | ✅ (own classes) | ❌ |
| Students: view | ✅ | ✅ | ✅ (own classes) | ✅ (own children) |
| Attendance: record | ✅ | ✅ | ✅ (own classes) | ❌ |
| Attendance: view | ✅ | ✅ | ✅ (own classes) | ✅ (own children) |
| Reports: create/edit | ✅ | ✅ | ✅ (own classes) | ❌ |
| Reports: view | ✅ | ✅ | ✅ (own classes) | ✅ (own children) |
| Reports: finalize | ✅ | ✅ | ✅ (own classes) | ❌ |
| Report templates CRUD | ✅ | ✅ | ❌ | ❌ |
| Messages: send announcement | ✅ | ✅ | ✅ (class only) | ❌ |
| Messages: send to parent | ✅ | ✅ | ✅ | ❌ |
| Messages: reply | ✅ | ✅ | ✅ | ✅ (own threads) |
| Photos: share | ✅ | ✅ | ✅ (own classes) | ❌ |
| Photos: view | ✅ | ✅ | ✅ (own classes) | ✅ (own children) |
| Documents: share school-wide | ✅ | ✅ | ❌ | ❌ |
| Documents: share class | ✅ | ✅ | ✅ (own classes) | ❌ |
| Settings: view | ✅ | ✅ | ❌ | ❌ |
| Settings: edit | ✅ | ✅ | ❌ | ❌ |
| Webhooks CRUD | ✅ | ✅ | ❌ | ❌ |
| Bulk import | ✅ | ✅ | ❌ | ❌ |
| Invitations: create | ✅ | ✅ | ✅ | ❌ |

### Parent Registration Flow

```
1. Admin/Teacher creates invitation for parent (specifying email + student)
2. System generates 8-char alphanumeric code, sends email to parent
3. Parent clicks link → lands on /register?code=XXXXXXXX
4. Parent enters: invitation code + email → System validates match
5. If valid → Parent sets password → Account created → Auto-login
6. Parent is automatically linked to the student via parent_students table
```

---

## 8. API Design

### URL Structure

```
# JSON API (for third-party apps and frontend fetch calls)
/api/v1/auth/...
/api/v1/students/...
/api/v1/classes/...
/api/v1/attendance/...
/api/v1/messages/...
/api/v1/reports/...
/api/v1/files/...
/api/v1/invitations/...
/api/v1/webhooks/...
/api/v1/imports/...
/api/v1/tenant/...
/api/v1/admin/...         # Super admin endpoints

# HTML views (server-rendered pages)
/login
/register
/dashboard
/students/...
/classes/...
/attendance/...
/messages/...
/reports/...
/photos/...
/documents/...
/settings/...
/onboarding/...
/imports/...
/admin/...                # Super admin pages
```

### API Response Envelope

All JSON API responses use a consistent envelope:

```json
// Success (single entity)
{
  "status": "success",
  "data": { ... },
  "message": "Student created successfully"
}

// Success (list with pagination)
{
  "status": "success",
  "data": [ ... ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total_items": 147,
    "total_pages": 8,
    "has_next": true,
    "has_prev": false
  }
}

// Error
{
  "status": "error",
  "message": "Validation failed",
  "errors": [
    {"field": "email", "message": "Invalid email format"},
    {"field": "first_name", "message": "This field is required"}
  ]
}
```

### Pydantic Response Wrapper

```python
# app/schemas/common.py
from pydantic import BaseModel
from typing import TypeVar, Generic

T = TypeVar("T")

class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_prev: bool

class APIResponse(BaseModel, Generic[T]):
    status: str = "success"
    data: T | None = None
    message: str | None = None
    errors: list[dict] | None = None
    pagination: PaginationMeta | None = None
```

### Pagination Convention

- Default page size: 20
- Max page size: 100
- Query params: `?page=1&page_size=20`
- Sorting: `?sort_by=created_at&sort_order=desc`
- Filtering: `?class_id=xxx&status=PRESENT&date=2026-02-13`

### Complete API Endpoint Reference

#### Auth (`/api/v1/auth`)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/login` | Login with email + password | No |
| POST | `/register` | Register parent via invitation code | No |
| POST | `/refresh` | Refresh access token | Yes |
| POST | `/logout` | Clear auth cookie | Yes |
| POST | `/forgot-password` | Send password reset email | No |
| POST | `/reset-password` | Reset password with token | No |
| GET | `/me` | Get current user profile | Yes |
| PUT | `/me` | Update current user profile | Yes |
| PUT | `/me/password` | Change password | Yes |

#### Tenants (`/api/v1/tenant`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| GET | `/configuration` | Get tenant config (features, terminology) | Any authenticated |
| PUT | `/configuration` | Update tenant config | SCHOOL_ADMIN |
| PUT | `/branding` | Update logo + colors | SCHOOL_ADMIN |

#### Super Admin (`/api/v1/admin`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| GET | `/tenants` | List all tenants | SUPER_ADMIN |
| POST | `/tenants` | Create new tenant | SUPER_ADMIN |
| GET | `/tenants/{id}` | Get tenant details | SUPER_ADMIN |
| PUT | `/tenants/{id}` | Update tenant | SUPER_ADMIN |
| DELETE | `/tenants/{id}` | Deactivate tenant (soft) | SUPER_ADMIN |
| GET | `/stats` | Platform-wide statistics | SUPER_ADMIN |

#### Users (`/api/v1/users`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| GET | `/` | List users (filterable by role) | SCHOOL_ADMIN |
| POST | `/` | Create user (admin or teacher) | SCHOOL_ADMIN |
| GET | `/{id}` | Get user details | SCHOOL_ADMIN |
| PUT | `/{id}` | Update user | SCHOOL_ADMIN |
| DELETE | `/{id}` | Deactivate user (soft) | SCHOOL_ADMIN |
| GET | `/teachers` | List all teachers | SCHOOL_ADMIN |
| GET | `/parents` | List all parents | SCHOOL_ADMIN, TEACHER |

#### Students (`/api/v1/students`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| GET | `/` | List students (filter by class, age_group) | SCHOOL_ADMIN, TEACHER |
| POST | `/` | Create student | SCHOOL_ADMIN, TEACHER |
| GET | `/{id}` | Get student details | SCHOOL_ADMIN, TEACHER, PARENT (own) |
| PUT | `/{id}` | Update student | SCHOOL_ADMIN, TEACHER |
| DELETE | `/{id}` | Deactivate student (soft) | SCHOOL_ADMIN |
| GET | `/{id}/parents` | List student's parents | SCHOOL_ADMIN, TEACHER |
| POST | `/{id}/parents` | Link parent to student | SCHOOL_ADMIN, TEACHER |
| DELETE | `/{id}/parents/{parent_id}` | Unlink parent | SCHOOL_ADMIN |
| GET | `/my-children` | List parent's own children | PARENT |

#### Classes (`/api/v1/classes`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| GET | `/` | List all classes | SCHOOL_ADMIN, TEACHER |
| POST | `/` | Create class | SCHOOL_ADMIN |
| GET | `/{id}` | Get class details + students | SCHOOL_ADMIN, TEACHER |
| PUT | `/{id}` | Update class | SCHOOL_ADMIN |
| DELETE | `/{id}` | Deactivate class (soft) | SCHOOL_ADMIN |
| GET | `/{id}/students` | List students in class | SCHOOL_ADMIN, TEACHER |
| GET | `/{id}/teachers` | List teachers for class | SCHOOL_ADMIN |
| POST | `/{id}/teachers` | Assign teacher to class | SCHOOL_ADMIN |
| DELETE | `/{id}/teachers/{teacher_id}` | Remove teacher from class | SCHOOL_ADMIN |
| PUT | `/{id}/set-primary` | Set as teacher's primary class | TEACHER |
| GET | `/my-classes` | Get teacher's assigned classes | TEACHER |

#### Attendance (`/api/v1/attendance`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| GET | `/` | Get attendance records (filter by class, date) | SCHOOL_ADMIN, TEACHER |
| POST | `/` | Record single attendance | TEACHER |
| POST | `/bulk` | Bulk attendance (entire class) | TEACHER |
| PUT | `/{id}` | Update attendance record | TEACHER |
| GET | `/class/{class_id}/date/{date}` | Get class attendance for date | SCHOOL_ADMIN, TEACHER |
| GET | `/student/{student_id}` | Get student attendance history | Any (scoped) |
| GET | `/stats` | Attendance statistics | SCHOOL_ADMIN |

#### Messages (`/api/v1/messages`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| GET | `/` | List messages (inbox) | Any authenticated |
| POST | `/` | Send message | SCHOOL_ADMIN, TEACHER |
| GET | `/{id}` | Get message + thread | Any (recipient or sender) |
| POST | `/{id}/reply` | Reply to message | Any (thread participant) |
| PUT | `/{id}/read` | Mark as read | Any (recipient) |
| GET | `/unread-count` | Get unread message count | Any authenticated |
| GET | `/announcements` | List announcements | Any authenticated |

#### Reports (`/api/v1/reports`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| GET | `/` | List reports (filter by class, student, date) | SCHOOL_ADMIN, TEACHER |
| POST | `/` | Create report | TEACHER |
| GET | `/{id}` | Get report details | Any (scoped) |
| PUT | `/{id}` | Update draft report | TEACHER |
| POST | `/{id}/finalize` | Finalize report (triggers parent notification) | TEACHER |
| GET | `/student/{student_id}` | Get student's reports | Any (scoped) |
| GET | `/templates` | List active templates | SCHOOL_ADMIN, TEACHER |
| POST | `/templates` | Create template | SCHOOL_ADMIN |
| GET | `/templates/{id}` | Get template details | SCHOOL_ADMIN |
| PUT | `/templates/{id}` | Update template | SCHOOL_ADMIN |
| DELETE | `/templates/{id}` | Deactivate template | SCHOOL_ADMIN |
| GET | `/templates/for-student/{student_id}` | Get matching templates for student | TEACHER |

#### Files (`/api/v1/files`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| POST | `/upload` | Upload file (multipart/form-data) | SCHOOL_ADMIN, TEACHER |
| GET | `/{id}/url` | Get presigned download URL (1h expiry) | Any (scoped) |
| DELETE | `/{id}` | Delete file | SCHOOL_ADMIN, TEACHER (own) |

#### Invitations (`/api/v1/invitations`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| GET | `/` | List invitations | SCHOOL_ADMIN, TEACHER |
| POST | `/` | Create parent invitation | SCHOOL_ADMIN, TEACHER |
| POST | `/verify` | Verify invitation code + email (public) | No auth |
| DELETE | `/{id}` | Cancel invitation | SCHOOL_ADMIN |
| POST | `/{id}/resend` | Resend invitation email | SCHOOL_ADMIN, TEACHER |

#### Webhooks (`/api/v1/webhooks`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| GET | `/` | List webhook endpoints | SCHOOL_ADMIN |
| POST | `/` | Create webhook endpoint | SCHOOL_ADMIN |
| PUT | `/{id}` | Update webhook endpoint | SCHOOL_ADMIN |
| DELETE | `/{id}` | Delete webhook endpoint | SCHOOL_ADMIN |
| GET | `/{id}/events` | List recent events for endpoint | SCHOOL_ADMIN |
| POST | `/{id}/test` | Send test webhook | SCHOOL_ADMIN |

#### Imports (`/api/v1/imports`)

| Method | Path | Description | Roles |
|--------|------|-------------|-------|
| POST | `/upload` | Upload CSV + specify import type | SCHOOL_ADMIN |
| POST | `/{id}/start` | Start import with column mapping | SCHOOL_ADMIN |
| GET | `/{id}` | Get import job status | SCHOOL_ADMIN |
| GET | `/{id}/errors` | Get import errors | SCHOOL_ADMIN |
| GET | `/` | List import jobs | SCHOOL_ADMIN |

#### WhatsApp (`/api/v1/whatsapp`)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/webhook` | Meta verification challenge | No (Meta verifies) |
| POST | `/webhook` | Receive inbound messages | No (HMAC verified) |
| POST | `/send` | Send WhatsApp message | SCHOOL_ADMIN, TEACHER |

#### WebSocket (`/api/v1/ws`)

| Path | Description | Auth |
|------|-------------|------|
| `/api/v1/ws/{token}` | WebSocket connection (token in URL for auth) | JWT in path |

---

## 9. Frontend Architecture

### Design System

**Color Palette:**

| Token | Hex | Usage |
|-------|-----|-------|
| `primary-500` | `#7C3AED` | Buttons, links, active states |
| `primary-600` | `#6D28D9` | Hover states |
| `primary-700` | `#5B21B6` | Active/pressed states |
| `primary-50` | `#F5F3FF` | Light backgrounds |
| `gray-50` | `#F9FAFB` | Page background |
| `gray-100` | `#F3F4F6` | Card backgrounds |
| `gray-900` | `#111827` | Primary text |
| `gray-500` | `#6B7280` | Secondary text |
| `green-500` | `#10B981` | Success states |
| `red-500` | `#EF4444` | Error/danger states |
| `amber-500` | `#F59E0B` | Warning states |
| `blue-500` | `#3B82F6` | Info states |

**Typography:**

- Font: `Inter` via Google Fonts CDN (fallback: `system-ui, -apple-system, sans-serif`)
- Headings: font-weight 600-700
- Body: font-weight 400
- Scale: text-xs (12px), text-sm (14px), text-base (16px), text-lg (18px), text-xl (20px), text-2xl (24px)

**Spacing:** Tailwind defaults (4px base unit). Consistent padding: cards 6 (24px), sections 8 (32px).

**Border Radius:** `rounded-lg` (8px) for cards, `rounded-md` (6px) for buttons/inputs, `rounded-full` for avatars.

**Shadows:** `shadow-sm` for cards, `shadow-md` for dropdowns/modals.

### Layout Structure

```
┌─────────────────────────────────────────────────────┐
│  Navbar (sticky top)                                │
│  ┌──────┬───────────────────────────────────────┐   │
│  │Logo  │ Search    Class Selector  🔔 Avatar ▼ │   │
│  └──────┴───────────────────────────────────────┘   │
├──────────┬──────────────────────────────────────────┤
│ Sidebar  │  Main Content Area                       │
│ (desktop │  ┌────────────────────────────────────┐  │
│  only)   │  │ Page Header (title + actions)      │  │
│          │  ├────────────────────────────────────┤  │
│ Dashboard│  │                                    │  │
│ Students │  │ Content (cards, tables, forms)     │  │
│ Classes  │  │                                    │  │
│ Attend.  │  │                                    │  │
│ Messages │  │                                    │  │
│ Reports  │  │                                    │  │
│ Photos   │  │                                    │  │
│ Docs     │  │                                    │  │
│ Settings │  │                                    │  │
│          │  └────────────────────────────────────┘  │
├──────────┴──────────────────────────────────────────┤
│ Mobile Bottom Nav (visible < md breakpoint)          │
│ ┌────────┬────────┬────────┬────────┬────────┐      │
│ │  Home  │Students│Attend. │Messages│  More  │      │
│ └────────┴────────┴────────┴────────┴────────┘      │
└─────────────────────────────────────────────────────┘
```

### Mobile Behavior

- **Breakpoint**: `md` (768px) is the mobile/desktop split
- **Sidebar**: Hidden on mobile, replaced by bottom tab navigation
- **Tables**: Become card lists on mobile (`<md` uses stacked card layout)
- **Touch targets**: All interactive elements minimum 44x44px
- **Swipe**: Messages support swipe-to-reply (via touch events in JS)
- **Pull-to-refresh**: Supported on mobile via custom JS

### Base Template (`templates/base.html`)

```html
<!DOCTYPE html>
<html lang="{{ current_language }}" class="h-full bg-gray-50">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}ClassUp{% endblock %}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    fontFamily: { sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'] },
                    colors: {
                        primary: {
                            50: '#F5F3FF', 100: '#EDE9FE', 200: '#DDD6FE', 300: '#C4B5FD',
                            400: '#A78BFA', 500: '#7C3AED', 600: '#6D28D9', 700: '#5B21B6',
                            800: '#4C1D95', 900: '#3B0764'
                        }
                    }
                }
            }
        }
    </script>
    <link rel="stylesheet" href="{{ url_for('static', path='css/app.css') }}">
    {% block head %}{% endblock %}
</head>
<body class="h-full font-sans text-gray-900 antialiased">
    {% if current_user %}
        {% include 'components/_navbar.html' %}
        <div class="flex h-[calc(100vh-4rem)]">
            {% include 'components/_sidebar.html' %}
            <main class="flex-1 overflow-y-auto p-4 md:p-8">
                <!-- Flash messages / Toasts -->
                <div id="toast-container" class="fixed top-20 right-4 z-50 space-y-2"></div>
                {% block content %}{% endblock %}
            </main>
        </div>
        {% include 'components/_mobile_nav.html' %}
    {% else %}
        <main class="min-h-screen">
            {% block auth_content %}{% endblock %}
        </main>
    {% endif %}

    <script src="{{ url_for('static', path='js/app.js') }}"></script>
    {% block scripts %}{% endblock %}
</body>
</html>
```

### JavaScript Architecture (`static/js/app.js`)

```javascript
// === Global Utilities ===

const ClassUp = {
    // Authenticated fetch wrapper with CSRF and error handling
    async fetch(url, options = {}) {
        const defaults = {
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin'  // Sends cookies
        };
        const response = await fetch(url, { ...defaults, ...options });
        if (response.status === 401) {
            window.location.href = '/login';
            return;
        }
        if (!response.ok) {
            const error = await response.json();
            ClassUp.toast(error.message || 'Something went wrong', 'error');
            throw error;
        }
        return response.json();
    },

    // Toast notifications
    toast(message, type = 'success', duration = 4000) {
        const container = document.getElementById('toast-container');
        const colors = {
            success: 'bg-green-50 border-green-500 text-green-800',
            error: 'bg-red-50 border-red-500 text-red-800',
            warning: 'bg-amber-50 border-amber-500 text-amber-800',
            info: 'bg-blue-50 border-blue-500 text-blue-800'
        };
        const toast = document.createElement('div');
        toast.className = `p-4 rounded-lg border-l-4 shadow-md ${colors[type]}
                           transform transition-all duration-300 translate-x-full`;
        toast.textContent = message;
        container.appendChild(toast);
        requestAnimationFrame(() => toast.classList.remove('translate-x-full'));
        setTimeout(() => {
            toast.classList.add('translate-x-full');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    },

    // Confirm dialog
    async confirm(message, { title = 'Confirm', confirmText = 'Confirm', danger = false } = {}) {
        // Returns a Promise<boolean> — renders a modal dialog
        // Implementation: creates modal DOM, resolves on button click
    },

    // Debounce for search inputs
    debounce(fn, delay = 300) {
        let timer;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => fn(...args), delay);
        };
    },

    // Format dates using tenant timezone
    formatDate(dateString, format = 'short') { /* ... */ },
    formatTime(dateString) { /* ... */ },
    timeAgo(dateString) { /* ... */ }
};
```

### HTML-First Interactivity Patterns

The frontend uses progressive enhancement. Pages work without JavaScript (forms submit normally), and JS enhances the experience.

**Pattern 1: Live Search**
```html
<input type="text" id="student-search" placeholder="Search students..."
       class="w-full rounded-md border-gray-300 ..."
       data-search-url="/api/v1/students"
       data-search-target="#student-list">
```
```javascript
// search.js binds to all [data-search-url] elements
document.querySelectorAll('[data-search-url]').forEach(input => {
    input.addEventListener('input', ClassUp.debounce(async (e) => {
        const url = `${input.dataset.searchUrl}?q=${e.target.value}`;
        const { data } = await ClassUp.fetch(url);
        const target = document.querySelector(input.dataset.searchTarget);
        // Re-render list with results (using template literals)
    }, 300));
});
```

**Pattern 2: Inline Actions (no page reload)**
```html
<button data-action="delete" data-url="/api/v1/students/{{ student.id }}"
        data-confirm="Delete this student?" class="text-red-600 hover:text-red-800">
    Delete
</button>
```

**Pattern 3: Modal Forms**
```html
<button data-modal="create-student" class="btn-primary">Add Student</button>
<dialog id="create-student" class="rounded-xl shadow-xl p-0 w-full max-w-lg">
    <form method="dialog" action="/api/v1/students" data-ajax="true">
        <!-- Form fields -->
    </form>
</dialog>
```

---

## 10. Core Modules

### 10.1 Student Management

**Features:**
- Full CRUD with search, filter, pagination
- Photo upload for student avatar
- Emergency contacts (stored as JSONB array)
- Medical info and allergies fields
- Dual categorization: `age_group` (enum) + `grade_level` (free text)
- Link parents via invitation system
- View student's attendance history, reports, messages

**Student Detail Page Sections:**
1. Profile header (photo, name, age, class, enrollment date)
2. Parent/Guardian list with contact info
3. Attendance summary (current month: present %, absent %, late %)
4. Recent reports (last 5)
5. Emergency contacts
6. Medical information
7. Action buttons: Edit, Create Report, Send Message to Parents, Share Photo

### 10.2 Class Management

**Features:**
- CRUD with capacity tracking
- Assign/remove teachers (many-to-many via `teacher_classes`)
- View student roster with attendance status indicators
- Primary teacher designation
- Class-based filtering throughout the system

**Teacher Multi-Class Support:**
- Teachers with multiple assigned classes see a class selector dropdown in the navbar
- Selecting a class changes the context for attendance, reports, and messaging
- The selected class ID is stored in a cookie (`selected_class_id`) for persistence across page loads
- Primary class is the default selection on login

### 10.3 Attendance Tracking

**Daily Attendance Page Flow:**
1. Teacher selects class (auto-selected if only one) and date (defaults to today)
2. System loads all students in the class
3. Displays a list with each student's name, photo, and status toggle buttons:
   - `PRESENT` (green) | `LATE` (amber) | `ABSENT` (red) | `EXCUSED` (gray)
4. Check-in time is auto-recorded when marked PRESENT or LATE
5. "Save All" button does a bulk POST to `/api/v1/attendance/bulk`
6. Real-time: WebSocket broadcasts attendance updates to connected admins/parents

**Bulk Attendance Request:**
```json
POST /api/v1/attendance/bulk
{
  "class_id": "uuid",
  "date": "2026-02-13",
  "records": [
    {"student_id": "uuid", "status": "PRESENT", "check_in_time": "2026-02-13T07:45:00+02:00"},
    {"student_id": "uuid", "status": "ABSENT", "notes": "Parent called, sick"},
    {"student_id": "uuid", "status": "LATE", "check_in_time": "2026-02-13T08:15:00+02:00"}
  ]
}
```

**Check-out:** Teachers can record check-out time during the day by updating individual records.

**Notifications triggered:**
- When a student is marked ABSENT → notification to parents (in-app + email + WhatsApp if enabled)
- When a student is marked LATE → notification to parents

**Statistics Dashboard (Admin):**
- Attendance rate by class (current week/month)
- Chronic absenteeism alerts (students below 90% attendance)
- Late arrival trends

---

## 11. Messaging & Communication

### Message Types

| Type | Sender | Recipients | Scope |
|------|--------|-----------|-------|
| `ANNOUNCEMENT` | SCHOOL_ADMIN | All parents in tenant | School-wide |
| `CLASS_ANNOUNCEMENT` | TEACHER | All parents in class | Class-wide |
| `STUDENT_MESSAGE` | TEACHER | Specific student's parents | Student-specific |
| `REPLY` | Any participant | Thread participants | Thread |
| `CLASS_PHOTO` | TEACHER | All parents in class | Class-wide |
| `STUDENT_PHOTO` | TEACHER | Specific student's parents | Student-specific |
| `CLASS_DOCUMENT` | TEACHER | All parents in class | Class-wide |
| `STUDENT_DOCUMENT` | TEACHER | Specific student's parents | Student-specific |
| `SCHOOL_DOCUMENT` | SCHOOL_ADMIN | All parents in tenant | School-wide |

### Inbox View

- **Tabs**: All | Announcements | Photos | Documents
- **Each message card shows**: Sender avatar, name, subject/preview, timestamp, unread badge, attachment icon
- **Mobile**: Full-width cards, tap to open thread
- **Desktop**: Split view (inbox list left, thread detail right)

### Thread View

- Chronological messages in a chat-style layout (bubbles)
- Sender's messages right-aligned (purple), received messages left-aligned (gray)
- File attachments shown inline (image thumbnails, document icons with download button)
- Reply input at bottom with attachment button
- Read receipts shown as subtle "Seen" indicator

### Recipient Resolution Logic

When a message is created, the system resolves recipients automatically:

```python
# Pseudocode for recipient resolution
if message_type == "ANNOUNCEMENT":
    recipients = all_parents_in_tenant()
elif message_type == "CLASS_ANNOUNCEMENT" or message_type in ("CLASS_PHOTO", "CLASS_DOCUMENT"):
    recipients = all_parents_with_children_in_class(class_id)
elif message_type in ("STUDENT_MESSAGE", "STUDENT_PHOTO", "STUDENT_DOCUMENT"):
    recipients = parents_of_student(student_id)
elif message_type == "SCHOOL_DOCUMENT":
    recipients = all_parents_in_tenant()
elif message_type == "REPLY":
    recipients = original_thread_participants()
```

---

## 12. File Management

### Upload Flow

```
1. User selects file(s) via Dropzone.js widget
2. Client-side validation: file type + size limit
   - Photos: jpg, jpeg, png, webp, heic — max 5 MB
   - Documents: pdf, docx, doc — max 10 MB
3. Client compresses images (if photo, using canvas resize to max 1920px width)
4. POST /api/v1/files/upload (multipart/form-data)
   - Fields: file, file_category (PHOTO|DOCUMENT)
5. Server-side validation:
   - python-magic for MIME type detection (prevents extension spoofing)
   - Size limit enforcement
6. Upload to R2 at path: {tenant_id}/{category}/{entity_id}/{uuid}_{original_name}
7. Create FileEntity record in database
8. Return file entity ID
9. Attach to message via message_attachments join table
```

### Presigned URL Strategy

```python
# NEVER store presigned URLs long-term. Generate on every fetch.
async def get_presigned_url(self, file_entity: FileEntity, expires_in: int = 3600) -> str:
    """Generate a presigned URL for downloading a file. Default 1 hour expiry."""
    return self.s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': self.bucket_name, 'Key': file_entity.storage_path},
        ExpiresIn=expires_in
    )
```

### R2 Storage Structure

```
classup-files/
├── {tenant_id}/
│   ├── PHOTO/
│   │   └── {entity_id}/{uuid}_{filename}.jpg
│   ├── DOCUMENT/
│   │   └── {entity_id}/{uuid}_{filename}.pdf
│   ├── AVATAR/
│   │   └── {user_id}/{uuid}_{filename}.jpg
│   └── LOGO/
│       └── {uuid}_{filename}.png
```

---

## 13. Reporting System

### Template-Driven Architecture

Reports are entirely driven by templates stored in the database. No report logic is hardcoded. Admins create and customize templates via the UI.

### Report Creation Flow

```
1. Teacher navigates to Reports → Create
2. Teacher selects student
3. System fetches matching templates for that student:
   - Match student.age_group against template.applies_to_grade_level
   - Match student.grade_level against template.applies_to_grade_level
   - Include templates with NULL applies_to_grade_level (universal)
4. Teacher selects template from dropdown
5. System renders dynamic form based on template.sections JSONB
6. Teacher fills in data
7. Save as DRAFT (can edit later) or FINALIZE (triggers parent notification)
```

### Dynamic Form Rendering

Each section type renders differently:

**CHECKLIST sections:**
```html
<div class="space-y-4">
  <!-- For each field in section.fields -->
  <div class="form-group">
    <label>{{ field.label }}</label>
    {% if field.type == "SELECT" %}
      <select name="sections.{{ section.id }}.{{ field.id }}">
        {% for option in field.options %}
          <option>{{ option }}</option>
        {% endfor %}
      </select>
    {% elif field.type == "TEXT" %}
      <input type="text" name="sections.{{ section.id }}.{{ field.id }}">
    {% elif field.type == "TIME" %}
      <input type="time" name="sections.{{ section.id }}.{{ field.id }}">
    {% endif %}
  </div>
</div>
```

**REPEATABLE_ENTRIES sections:**
```html
<!-- Rendered as a dynamic table with Add/Remove buttons -->
<!-- JavaScript handles adding/removing rows -->
<table id="section-{{ section.id }}">
  <thead>
    <tr>
      {% for field in section.fields %}
        <th>{{ field.label }}</th>
      {% endfor %}
      <th>Actions</th>
    </tr>
  </thead>
  <tbody>
    <!-- Rows added dynamically via JS -->
  </tbody>
</table>
<button type="button" onclick="addRow('{{ section.id }}')">+ Add Entry</button>
```

**NARRATIVE sections:**
```html
<textarea name="sections.{{ section.id }}.{{ field.id }}"
          rows="4" class="w-full rounded-md border-gray-300 ..."></textarea>
```

### Report Data Storage

The `report_data` JSONB column stores the filled-in data matching the template structure:

```json
{
  "sections": {
    "meals": {
      "breakfast_amount": "All",
      "breakfast_notes": "Ate everything",
      "lunch_amount": "Most",
      "lunch_notes": ""
    },
    "fluids": {
      "entries": [
        {"time": "09:00", "amount": 120, "type": "Water"},
        {"time": "11:30", "amount": 200, "type": "Milk"}
      ]
    },
    "teacher_notes": {
      "notes": "Great day! Participated well in art activities."
    }
  }
}
```

### Finalization

When a report is finalized (status changes from DRAFT → FINALIZED):
1. `finalized_at` timestamp is set
2. Report becomes read-only (no further edits)
3. Notification sent to all parents of the student:
   - In-app notification via WebSocket
   - Email via Resend
   - WhatsApp message if enabled and parent opted in
4. Webhook event `report.finalized` dispatched

---

## 14. WhatsApp Integration

### Architecture

Uses the **Meta Cloud API** (formerly WhatsApp Business API) for two-way messaging.

### Setup Requirements

1. Meta Business Account with WhatsApp Business Platform access
2. Registered phone number with WhatsApp
3. Approved message templates in Meta Business Manager
4. Webhook endpoint configured in Meta app settings

### Message Templates (Must Be Pre-Approved by Meta)

| Template Name | Purpose | Variables |
|---------------|---------|-----------|
| `attendance_alert` | "Your child {{1}} was marked {{2}} today at {{3}}." | child_name, status, school_name |
| `report_ready` | "A new {{1}} report is ready for {{2}}. Log in to view: {{3}}" | report_type, child_name, url |
| `announcement` | "📢 {{1}}: {{2}}" | school_name, subject |
| `parent_invite` | "You've been invited to join {{1}} on ClassUp. Use code: {{2}}" | school_name, code |
| `welcome` | "Welcome to {{1}}! Your account is ready. Log in at {{2}}" | school_name, url |

### Outbound Flow

```python
# app/services/whatsapp_service.py
class WhatsAppService:
    async def send_template_message(
        self, to_phone: str, template_name: str, 
        language_code: str, parameters: list[str]
    ) -> dict:
        """Send a pre-approved template message."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,  # E.164 format: +27821234567
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in parameters]
                }]
            }
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_url}/{self.phone_number_id}/messages",
                json=payload,
                headers={"Authorization": f"Bearer {self.access_token}"}
            )
            return response.json()

    async def send_text_message(self, to_phone: str, body: str) -> dict:
        """Send a free-form text message (only within 24h conversation window)."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": body}
        }
        # ... same HTTP call pattern
```

### Inbound Flow (Webhook)

```
1. Parent sends WhatsApp message to the school's WhatsApp number
2. Meta forwards to POST /api/v1/whatsapp/webhook
3. Server verifies HMAC signature
4. Extract: sender phone, message body, message type
5. Look up parent by whatsapp_phone in users table
6. If found → create Message record (type: REPLY or STUDENT_MESSAGE)
7. Notify relevant teachers via WebSocket
8. If not found → auto-reply: "This number is not registered. Please contact your school."
```

### Webhook Endpoint

```python
@router.get("/webhook")
async def verify_webhook(request: Request):
    """Meta webhook verification challenge."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(403)

@router.post("/webhook")
async def receive_webhook(request: Request):
    """Process inbound WhatsApp messages."""
    body = await request.json()
    # Verify HMAC signature from X-Hub-Signature-256 header
    # Process messages in body.entry[*].changes[*].value.messages[*]
    # Each message has: from (phone), type, text.body, timestamp
```

### Opt-In/Opt-Out

Parents must explicitly opt in to WhatsApp notifications. This is managed via:
- `users.whatsapp_opted_in` boolean field
- Parent settings page has toggle switch
- First interaction sends opt-in confirmation template

---

## 15. Email System

### Provider Architecture

Email delivery supports **two providers**, switchable at runtime via the super admin UI at `/admin/email-settings`:

- **SMTP** — connects to any SMTP server (e.g. `mail.classup.co.za:465`). Uses `aiosmtplib`. Port 465 = implicit SSL, port 587 = STARTTLS.
- **Resend** — managed email API via `resend-python`. Requires an API key from resend.com.

Configuration is stored in the `system_settings` DB table (key=`email_config`, JSONB), **not** in environment variables. The `SystemSettings` model (`app/models/system_settings.py`) is a simple key-value store.

**Config shape** stored in `system_settings.value`:

```json
{
  "provider": "smtp",          // "smtp" or "resend"
  "enabled": true,
  "from_email": "notifications@classup.co.za",
  "from_name": "ClassUp",
  "smtp_host": "mail.classup.co.za",
  "smtp_port": 465,
  "smtp_username": "user@classup.co.za",
  "smtp_password": "secret",
  "smtp_use_tls": true,
  "resend_api_key": "re_xxxxxxxxxxxx"
}
```

The `from_name` is a fallback — tenant-scoped emails (invitations, reports, attendance alerts) automatically use the tenant's name as the sender display name.

### API Endpoints (Super Admin only)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/admin/email-settings` | Get current config (secrets masked) |
| PUT | `/api/v1/admin/email-settings` | Save/update config |
| POST | `/api/v1/admin/email-settings/test` | Send test email (optional `{"to": "..."}`) |

### Service Pattern

```python
# app/services/email_service.py
# Loads config from DB on every send. If not configured or enabled=false, skips silently.
# All convenience methods (send_parent_invitation, send_password_reset, etc.) delegate to send().
# send() accepts an optional from_name kwarg used for tenant-scoped emails.
```

### Email Triggers

| Event | Template | Recipient | Subject Pattern |
|-------|----------|-----------|-----------------|
| Tenant created | `welcome.html` | New school admin | "Welcome to ClassUp!" |
| Parent invited | `parent_invite.html` | Parent email | "You're invited to join {school}" |
| Report finalized | `report_ready.html` | Student's parents | "{report_type} ready for {child_name}" |
| Password reset | `password_reset.html` | Requesting user | "Reset your ClassUp password" |
| Admin notification | `admin_notification.html` | School admins | Various (class created, student added, etc.) |
| Attendance absence | `attendance_alert.html` | Student's parents | "{child_name} was marked absent today" |

### Email Template Base

```html
<!-- templates/emails/base_email.html -->
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: 'Inter', Arial, sans-serif; background: #F9FAFB; padding: 40px 20px;">
  <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 12px;
              box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow: hidden;">
    <div style="background: {{ tenant.branding.primary_color | default('#7C3AED') }};
                padding: 24px; text-align: center;">
      {% if tenant and tenant.logo_url %}
        <img src="{{ tenant.logo_url }}" alt="{{ tenant.name }}" style="max-height: 48px;">
      {% else %}
        <h1 style="color: white; margin: 0; font-size: 24px;">
          {{ tenant.name if tenant else 'ClassUp' }}
        </h1>
      {% endif %}
    </div>
    <div style="padding: 32px;">
      {% block content %}{% endblock %}
    </div>
    <div style="padding: 16px 32px; background: #F3F4F6; text-align: center;
                font-size: 12px; color: #6B7280;">
      {{ tenant.name if tenant else 'ClassUp' }} · Powered by ClassUp
    </div>
  </div>
</body>
</html>
```

---

## 16. WebSocket Real-Time System

### Architecture

FastAPI's built-in WebSocket support with Redis Pub/Sub for multi-instance coordination (Railway may run multiple instances).

```
Client ↔ WebSocket ↔ FastAPI ↔ Redis Pub/Sub ↔ Other FastAPI instances
```

### Connection Manager

```python
# app/services/realtime_service.py
import asyncio
import json
from fastapi import WebSocket
import redis.asyncio as aioredis

class ConnectionManager:
    def __init__(self, redis_url: str):
        self.active_connections: dict[str, list[WebSocket]] = {}  # user_id → [ws, ws, ...]
        self.redis = aioredis.from_url(redis_url)

    async def connect(self, websocket: WebSocket, user_id: str, tenant_id: str):
        await websocket.accept()
        key = f"{tenant_id}:{user_id}"
        self.active_connections.setdefault(key, []).append(websocket)

    async def disconnect(self, websocket: WebSocket, user_id: str, tenant_id: str):
        key = f"{tenant_id}:{user_id}"
        if key in self.active_connections:
            self.active_connections[key].remove(websocket)

    async def send_to_user(self, user_id: str, tenant_id: str, event: dict):
        """Send event to a specific user (all their connected sessions)."""
        key = f"{tenant_id}:{user_id}"
        # Publish to Redis for multi-instance support
        await self.redis.publish(f"ws:{key}", json.dumps(event))

    async def broadcast_to_tenant(self, tenant_id: str, event: dict):
        """Send event to all connected users in a tenant."""
        await self.redis.publish(f"ws:tenant:{tenant_id}", json.dumps(event))

    async def broadcast_to_role(self, tenant_id: str, role: str, event: dict):
        """Send event to all users with a specific role in a tenant."""
        await self.redis.publish(f"ws:tenant:{tenant_id}:role:{role}", json.dumps(event))
```

### WebSocket Events

```json
// Notification event
{
  "type": "notification",
  "data": {
    "id": "uuid",
    "title": "New Message",
    "body": "Ms. Smith sent a class announcement",
    "notification_type": "MESSAGE_RECEIVED",
    "reference_type": "message",
    "reference_id": "uuid",
    "created_at": "2026-02-13T10:30:00+02:00"
  }
}

// Attendance update event
{
  "type": "attendance_update",
  "data": {
    "student_id": "uuid",
    "student_name": "John Doe",
    "class_id": "uuid",
    "status": "PRESENT",
    "check_in_time": "2026-02-13T07:45:00+02:00",
    "recorded_by": "Ms. Smith"
  }
}

// Message received event
{
  "type": "message_received",
  "data": {
    "message_id": "uuid",
    "sender_name": "Ms. Smith",
    "preview": "Don't forget tomorrow's field trip!",
    "message_type": "CLASS_ANNOUNCEMENT",
    "created_at": "2026-02-13T10:30:00+02:00"
  }
}

// Unread count update
{
  "type": "unread_count",
  "data": {
    "messages": 3,
    "notifications": 7
  }
}
```

### Client-Side WebSocket

```javascript
// static/js/websocket.js
class ClassUpWebSocket {
    constructor(token) {
        this.token = token;
        this.ws = null;
        this.handlers = new Map();
        this.reconnectAttempts = 0;
        this.maxReconnect = 5;
        this.reconnectDelay = 1000;
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${protocol}//${window.location.host}/api/v1/ws/${this.token}`);

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            const handler = this.handlers.get(data.type);
            if (handler) handler(data.data);
        };

        this.ws.onclose = () => {
            if (this.reconnectAttempts < this.maxReconnect) {
                setTimeout(() => {
                    this.reconnectAttempts++;
                    this.connect();
                }, this.reconnectDelay * Math.pow(2, this.reconnectAttempts));
            }
        };

        this.ws.onopen = () => { this.reconnectAttempts = 0; };
    }

    on(eventType, handler) { this.handlers.set(eventType, handler); }
}

// Usage (initialized in base.html for authenticated users):
const ws = new ClassUpWebSocket('{{ access_token }}');
ws.on('notification', (data) => {
    ClassUp.toast(data.title, 'info');
    updateNotificationBadge();
});
ws.on('unread_count', (data) => {
    document.getElementById('unread-messages').textContent = data.messages;
    document.getElementById('unread-notifications').textContent = data.notifications;
});
ws.connect();
```

---

## 17. Tenant Onboarding Wizard

### 5-Step Wizard Flow

After a SUPER_ADMIN creates a new tenant and its first SCHOOL_ADMIN account, the admin's first login redirects to the onboarding wizard if `tenant.onboarding_completed == false`.

**Step 1: School Information**
- School name (pre-filled), address, phone, timezone
- Logo upload (drag-and-drop)
- Primary color picker (for branding)

**Step 2: Education Type & Features**
- Select education type: Daycare | Primary | High School | K-12 | Combined
- System auto-enables relevant features based on selection
- Admin can toggle individual features on/off

**Step 3: Create Classes**
- Add initial classes with name, age group/grade level, capacity
- Minimum 1 class required
- Quick-add buttons for common class names based on education type

**Step 4: Invite Teachers**
- Add teacher email addresses
- Assign each teacher to one or more classes
- System sends invitation emails
- Skip button available (can add later)

**Step 5: Complete**
- Summary of what was set up
- Quick links: "Add Students", "Customize Report Templates", "Go to Dashboard"
- Sets `tenant.onboarding_completed = true`

### UI Implementation

Multi-step wizard using vanilla JS:
- Steps shown as horizontal progress bar at top
- Each step is a `<section>` that hides/shows
- Back/Next buttons with validation before proceeding
- Data saved to server on each step (not just at the end) to prevent data loss

---

## 18. Bulk CSV Import

### Supported Import Types

| Type | Required Columns | Optional Columns |
|------|-----------------|------------------|
| `STUDENTS` | `first_name`, `last_name` | `date_of_birth`, `gender`, `age_group`, `grade_level`, `class_name`, `medical_info`, `allergies`, `parent_email`, `parent_phone`, `emergency_contact_name`, `emergency_contact_phone` |
| `TEACHERS` | `first_name`, `last_name`, `email` | `phone`, `class_names` (comma-separated) |

### Import Flow

```
1. Admin uploads CSV file on the import page
2. Server parses CSV headers and first 5 rows for preview
3. Admin maps CSV columns to system fields via drag-and-drop UI
4. Admin clicks "Start Import"
5. Background task (arq) processes rows:
   a. Validate each row
   b. Create/update records
   c. Track success/error counts
   d. Log per-row errors with field-level detail
6. WebSocket notifies admin when complete
7. Admin views results: success count, error count, downloadable error report
```

### Column Mapping UI

```
CSV Column          →    System Field
─────────────────────────────────────
"Student Name"      →    [first_name ▼]    (dropdown of valid system fields)
"Surname"           →    [last_name ▼]
"DOB"               →    [date_of_birth ▼]
"Grade"             →    [grade_level ▼]
"Class"             →    [class_name ▼]
"Mom Email"         →    [parent_email ▼]
"Allergies?"        →    [allergies ▼]
"ID Number"         →    [-- skip -- ▼]    (option to skip unmapped columns)
```

### Error Handling

Each row that fails validation is logged with details:

```json
{
  "errors": [
    {"row": 3, "field": "email", "value": "notanemail", "message": "Invalid email format"},
    {"row": 7, "field": "date_of_birth", "value": "31/13/2020", "message": "Invalid date format. Use YYYY-MM-DD"},
    {"row": 12, "field": "class_name", "value": "Grade 99", "message": "Class 'Grade 99' does not exist"}
  ]
}
```

The import is **non-transactional by default**: successful rows are committed, failed rows are skipped and reported. Admin can fix the CSV and re-import just the failed rows.

---

## 19. Internationalization (i18n)

### Supported Languages

- `en` — English (default)
- `af` — Afrikaans

### Translation File Structure

```json
// translations/en/messages.json
{
  "common": {
    "save": "Save",
    "cancel": "Cancel",
    "delete": "Delete",
    "edit": "Edit",
    "search": "Search",
    "loading": "Loading...",
    "no_results": "No results found",
    "confirm_delete": "Are you sure you want to delete this?"
  },
  "auth": {
    "login": "Log In",
    "logout": "Log Out",
    "email": "Email Address",
    "password": "Password",
    "forgot_password": "Forgot Password?",
    "invalid_credentials": "Invalid email or password"
  },
  "students": {
    "title": "Students",
    "add_student": "Add Student",
    "first_name": "First Name",
    "last_name": "Last Name",
    "date_of_birth": "Date of Birth"
  },
  "attendance": {
    "title": "Attendance",
    "present": "Present",
    "absent": "Absent",
    "late": "Late",
    "excused": "Excused",
    "check_in": "Check In",
    "check_out": "Check Out",
    "marked_absent": "{{child_name}} was marked absent today"
  }
  // ... all UI strings
}
```

```json
// translations/af/messages.json
{
  "common": {
    "save": "Stoor",
    "cancel": "Kanselleer",
    "delete": "Verwyder",
    "edit": "Wysig",
    "search": "Soek",
    "loading": "Laai tans...",
    "no_results": "Geen resultate gevind nie",
    "confirm_delete": "Is jy seker jy wil dit verwyder?"
  },
  "auth": {
    "login": "Meld Aan",
    "logout": "Meld Af",
    "email": "E-posadres",
    "password": "Wagwoord",
    "forgot_password": "Wagwoord Vergeet?",
    "invalid_credentials": "Ongeldige e-pos of wagwoord"
  },
  "students": {
    "title": "Leerlinge",
    "add_student": "Voeg Leerling By",
    "first_name": "Voornaam",
    "last_name": "Van"
  }
  // ...
}
```

### Implementation

**Language Detection Priority:**
1. User's `language` field in the database (explicit choice)
2. `Accept-Language` HTTP header
3. Tenant's default language from settings
4. Application default (`en`)

**Jinja2 Integration:**

```python
# app/services/i18n_service.py
import json

class I18nService:
    def __init__(self):
        self.translations: dict[str, dict] = {}
        self._load_translations()

    def _load_translations(self):
        for lang in ["en", "af"]:
            path = f"translations/{lang}/messages.json"
            with open(path) as f:
                self.translations[lang] = json.load(f)

    def t(self, key: str, lang: str = "en", **kwargs) -> str:
        """Translate a dot-notation key. E.g., t('common.save', 'af')"""
        keys = key.split(".")
        value = self.translations.get(lang, self.translations["en"])
        for k in keys:
            value = value.get(k, None)
            if value is None:
                # Fallback to English
                value = self.translations["en"]
                for k2 in keys:
                    value = value.get(k2, key)
                    if isinstance(value, str):
                        break
                break
        if isinstance(value, str):
            for param_key, param_value in kwargs.items():
                value = value.replace(f"{{{{{param_key}}}}}", str(param_value))
        return value if isinstance(value, str) else key
```

**In Jinja2 templates:**
```html
<!-- The `t` function is injected as a Jinja2 global -->
<button>{{ t('common.save') }}</button>
<h1>{{ t('students.title') }}</h1>
<p>{{ t('attendance.marked_absent', child_name=student.first_name) }}</p>
```

**Terminology Overrides:** In addition to static i18n, tenant-specific terminology (from `tenant.settings.terminology`) overrides standard translations. For example, a daycare might use "child" instead of "student":

```python
# Combined in template context:
def get_term(key: str) -> str:
    """Get tenant-specific term, falling back to i18n translation."""
    tenant_terms = current_tenant.settings.get("terminology", {})
    return tenant_terms.get(key) or i18n.t(f"terminology.{key}", current_language)
```

---

## 20. Webhook System

### Supported Events

| Event | Trigger | Payload |
|-------|---------|---------|
| `student.created` | Student created | Student object |
| `student.updated` | Student updated | Student object (changed fields) |
| `student.deleted` | Student soft-deleted | `{id, name}` |
| `attendance.marked` | Attendance recorded | AttendanceRecord object |
| `attendance.bulk` | Bulk attendance | `{class_id, date, records: [...]}` |
| `report.created` | Report created | Report summary |
| `report.finalized` | Report finalized | Report summary + student info |
| `message.sent` | Message sent | Message summary |
| `teacher.added` | Teacher created | User summary |
| `parent.registered` | Parent completed registration | User summary |
| `class.created` | Class created | Class object |
| `import.completed` | Bulk import finished | Import job summary |

### Delivery Mechanism

```python
# app/services/webhook_service.py
import hashlib, hmac, json
import httpx

class WebhookService:
    async def dispatch(self, tenant_id: UUID, event_type: str, payload: dict):
        """Find matching endpoints and enqueue delivery."""
        endpoints = await self._get_active_endpoints(tenant_id, event_type)
        for endpoint in endpoints:
            # Enqueue as background task for async delivery
            await enqueue_task("deliver_webhook", {
                "endpoint_id": str(endpoint.id),
                "event_type": event_type,
                "payload": payload
            })

    async def deliver(self, endpoint_id: UUID, event_type: str, payload: dict):
        """Deliver webhook with HMAC signature and retry logic."""
        endpoint = await self._get_endpoint(endpoint_id)

        body = json.dumps({"event": event_type, "data": payload, "timestamp": utcnow_iso()})
        signature = hmac.new(
            endpoint.secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-ClassUp-Signature": f"sha256={signature}",
            "X-ClassUp-Event": event_type
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(endpoint.url, content=body, headers=headers)
                await self._log_event(endpoint_id, event_type, payload,
                                      "DELIVERED", response.status_code, response.text)
            except Exception as e:
                await self._log_event(endpoint_id, event_type, payload,
                                      "FAILED", None, str(e))
                # Retry up to 3 times with exponential backoff
```

### Retry Policy

- Max 3 attempts
- Exponential backoff: 1 min, 5 min, 30 min
- After 3 failures: event marked as FAILED, no more retries
- Failed events visible in admin UI with response details

---

## 21. Background Tasks

### arq Worker Configuration

```python
# app/worker.py
from arq import create_pool
from arq.connections import RedisSettings

async def send_email_task(ctx, to: str, subject: str, template: str, context: dict):
    email_service = ctx["email_service"]
    await email_service.send(to, subject, template, context)

async def deliver_webhook_task(ctx, endpoint_id: str, event_type: str, payload: dict):
    webhook_service = ctx["webhook_service"]
    await webhook_service.deliver(UUID(endpoint_id), event_type, payload)

async def process_import_task(ctx, job_id: str):
    import_service = ctx["import_service"]
    await import_service.process(UUID(job_id))

async def send_whatsapp_task(ctx, to_phone: str, template: str, params: list):
    whatsapp_service = ctx["whatsapp_service"]
    await whatsapp_service.send_template_message(to_phone, template, "en", params)

class WorkerSettings:
    functions = [send_email_task, deliver_webhook_task, process_import_task, send_whatsapp_task]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 300  # 5 minutes max per job
```

### Task Enqueue Helper

```python
# app/services/task_queue.py
from arq import create_pool

_redis_pool = None

async def get_pool():
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _redis_pool

async def enqueue_task(task_name: str, kwargs: dict, defer_by: int | None = None):
    pool = await get_pool()
    await pool.enqueue_job(task_name, **kwargs, _defer_by=defer_by)
```

---

## 22. Error Handling

### Exception Hierarchy

```python
# app/exceptions.py
class ClassUpException(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code

class NotFoundException(ClassUpException):
    def __init__(self, resource: str = "Resource"):
        super().__init__(f"{resource} not found", 404)

class ForbiddenException(ClassUpException):
    def __init__(self, message: str = "You don't have permission to perform this action"):
        super().__init__(message, 403)

class UnauthorizedException(ClassUpException):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, 401)

class ConflictException(ClassUpException):
    def __init__(self, message: str = "Resource already exists"):
        super().__init__(message, 409)

class ValidationException(ClassUpException):
    def __init__(self, errors: list[dict]):
        super().__init__("Validation failed", 422)
        self.errors = errors
```

### Global Exception Handlers

```python
# In main.py
@app.exception_handler(ClassUpException)
async def classup_exception_handler(request: Request, exc: ClassUpException):
    if _wants_json(request):
        return JSONResponse(
            status_code=exc.status_code,
            content={"status": "error", "message": exc.message,
                     "errors": getattr(exc, "errors", None)}
        )
    # HTML error page
    return templates.TemplateResponse(
        f"errors/{exc.status_code}.html",
        {"request": request, "message": exc.message},
        status_code=exc.status_code
    )

def _wants_json(request: Request) -> bool:
    """Check if request expects JSON (API) or HTML (web)."""
    return (
        request.url.path.startswith("/api/") or
        "application/json" in request.headers.get("accept", "")
    )
```

---

## 23. Testing Strategy

### Test Categories

1. **Unit tests**: Service methods with mocked database
2. **Integration tests**: API endpoints with real test database
3. **Factory tests**: Model factories for generating test data

### Test Setup

```python
# tests/conftest.py
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

TEST_DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5432/classup_test"

@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest_asyncio.fixture
async def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

@pytest_asyncio.fixture
async def auth_client(client, db_session):
    """Client with authentication (SCHOOL_ADMIN by default)."""
    # Create test tenant + admin user, get JWT
    tenant = await create_test_tenant(db_session)
    admin = await create_test_user(db_session, tenant.id, "SCHOOL_ADMIN")
    token = create_jwt(admin)
    client.cookies.set("access_token", token)
    client.headers["Authorization"] = f"Bearer {token}"
    return client, tenant, admin
```

### Minimum Test Coverage Requirements

- Authentication: login, register, token refresh, password reset
- CRUD operations for all entities (happy path + error cases)
- Permission enforcement (ensure PARENT can't access TEACHER endpoints)
- Tenant isolation (ensure tenant A can't see tenant B's data)
- Bulk operations (attendance, import)
- File upload/download
- WebSocket connection and event delivery
- Webhook delivery and retry

---

## 24. Deployment to Railway

### Railway Services

| Service | Type | Notes |
|---------|------|-------|
| `classup-web` | Web | FastAPI app (Uvicorn) |
| `classup-worker` | Worker | arq background task processor |
| `postgresql` | Database | Railway managed PostgreSQL |
| `redis` | Cache | Railway managed Redis |

### `railway.toml`

```toml
[build]
builder = "dockerfile"
dockerfilePath = "Dockerfile"

[deploy]
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2"
healthcheckPath = "/health"
healthcheckTimeout = 10
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

### `Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System dependencies (for python-magic, asyncpg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run Alembic migrations on startup
RUN chmod +x scripts/start.sh

EXPOSE $PORT

CMD ["scripts/start.sh"]
```

### `scripts/start.sh`

```bash
#!/bin/bash
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting application..."
if [ "$WORKER_MODE" = "true" ]; then
    echo "Starting arq worker..."
    arq app.worker.WorkerSettings
else
    echo "Starting web server..."
    uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_WORKERS:-2}
fi
```

### Railway Environment Variables

Set all variables from Section 4 in Railway's environment variable settings. Railway automatically provides:
- `PORT` — the port to bind to
- `DATABASE_URL` — from the PostgreSQL plugin (convert to asyncpg format in config)
- `REDIS_URL` — from the Redis plugin

### Worker Service

Deploy a second Railway service from the same repo with environment variable:
```
WORKER_MODE=true
```

This starts the arq worker instead of Uvicorn.

---

## 25. Migration Guide from ClassUp v1

### Data Migration Strategy

Since ClassUp v1 uses the same PostgreSQL database schema concepts (UUIDs, tenant_id, same entity relationships), data migration involves:

1. **Export v1 data** as CSV/JSON from the Spring Boot PostgreSQL database
2. **Map column names** (v1 uses camelCase in Java, v2 uses snake_case — but the DB columns are already snake_case in v1)
3. **Run import scripts** that read v1 dumps and insert into v2 tables
4. **Password hashes** are bcrypt in both versions — they transfer directly

### Key Differences

| Aspect | v1 (Spring Boot) | v2 (FastAPI) |
|--------|-------------------|--------------|
| ORM | Hibernate/JPA | SQLAlchemy 2.0 async |
| Migrations | Liquibase (YAML) | Alembic (Python) |
| Auth | Spring Security + JWT | Custom JWT + passlib |
| File Storage | Same R2 setup | Same R2 setup (compatible) |
| Email | Spring Events + JavaMailSender | SMTP / Resend (DB-configured, super admin UI) |
| Frontend | Flutter (mobile/web) | Jinja2 + Tailwind (web) |
| Real-time | None (polling) | WebSocket + Redis Pub/Sub |
| WhatsApp | Not implemented | Meta Cloud API |
| i18n | Not implemented | Babel + JSON translation files |

### What Transfers Directly

- All R2 file storage paths (same structure)
- Password hashes (bcrypt)
- JSONB data (tenant settings, report data, template sections)
- Invitation codes (if still valid)

---

## Appendix A: Coding Agent Instructions

### How to Use This Document

1. **Read the full document first** before writing any code.
2. **Start with**: Database models → Alembic migrations → Config → Auth → then work module by module.
3. **Follow the project structure** exactly as specified in Section 3.
4. **Every service method** must call `get_tenant_id()` as its first line when accessing tenant-scoped data.
5. **Every API endpoint** must specify allowed roles using the `@require_role()` decorator.
6. **Every HTML template** extends `base.html` and uses Tailwind utility classes.
7. **Never use a JS framework**. Use vanilla JavaScript with the patterns in Section 9.
8. **Test each module** before moving to the next.

### Build Order

```
Phase 1: Foundation
  1. Project scaffolding (folders, pyproject.toml, requirements.txt)
  2. Config + Settings class
  3. Database setup (SQLAlchemy async engine, session factory)
  4. Base models (Base, TenantScopedModel)
  5. Alembic setup + initial migration
  6. Health check endpoint

Phase 2: Auth + Tenancy
  7. User model + Tenant model
  8. JWT utilities (encode, decode, password hashing)
  9. Auth API endpoints (login, register, logout, me)
  10. Tenant middleware
  11. Auth middleware (cookie + bearer)
  12. Login/Register HTML pages
  13. Role permission utilities

Phase 3: Core Entities
  14. Student model + CRUD API + HTML pages
  15. Class model + CRUD API + HTML pages
  16. Teacher-class assignment (join table + API)
  17. Parent-student relationship (join table)
  18. Base layout template (navbar, sidebar, mobile nav)
  19. Dashboard pages (per role)

Phase 4: Attendance
  20. Attendance model + API
  21. Bulk attendance endpoint
  22. Attendance HTML pages (daily view, history)
  23. Attendance statistics

Phase 5: Messaging
  24. Message model + recipients + attachments
  25. Message API endpoints
  26. Inbox, thread, compose HTML pages
  27. Recipient resolution logic

Phase 6: Files
  28. File entity model
  29. R2 service (upload, download, presigned URLs)
  30. File upload API
  31. Photo gallery pages
  32. Document management pages

Phase 7: Reports
  33. Report template model
  34. Daily report model
  35. Template CRUD API + admin UI
  36. Dynamic report form (create, view)
  37. Report finalization + notification

Phase 8: Communication
  38. Email service (SMTP + Resend, DB-configured via super admin UI)
  39. Email templates
  40. Notification model + service
  41. WebSocket setup (connection manager, Redis pub/sub)
  42. Client-side WebSocket
  43. WhatsApp service (outbound)
  44. WhatsApp webhook (inbound)

Phase 9: Advanced Features
  45. Parent invitation system
  46. Tenant onboarding wizard
  47. Bulk CSV import
  48. i18n setup + translation files
  49. Webhook system

Phase 10: Polish
  50. Error pages (404, 403, 500)
  51. Empty states for all list views
  52. Loading states and skeletons
  53. Mobile responsiveness audit
  54. Background task worker setup
  55. Seed data script
  56. Deployment configuration
```

### Code Style Rules

- **Python**: Follow PEP 8. Use type hints everywhere. Async functions for all DB operations.
- **SQL**: Use SQLAlchemy ORM (not raw SQL) unless performance requires it.
- **HTML**: Semantic elements (`<nav>`, `<main>`, `<section>`, `<article>`). Accessible (`aria-` attributes, proper labels).
- **CSS**: Tailwind utility classes only. No custom CSS except for animations and truly custom components.
- **JavaScript**: ES2022+. No `var`. `const` by default, `let` when reassignment needed. No jQuery.
- **Naming**: snake_case for Python/SQL, camelCase for JavaScript, kebab-case for CSS classes and URLs.
- **Comments**: Document "why", not "what". Every service method has a docstring.

---

*End of specification. This document is version 1.0 and should be updated as implementation decisions evolve.*
