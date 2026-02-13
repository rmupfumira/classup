# First Prompt for Claude Code

Read CLAUDE.md thoroughly — it is the complete system architecture specification for this project.

## What to build now

Start with **Phase 1: Foundation** and **Phase 2: Auth + Tenancy** from the Build Order in Appendix A.

Specifically, build these in order:

### Phase 1: Foundation
1. Project scaffolding — create the full folder structure exactly as defined in Section 3
2. `pyproject.toml` with all dependencies from Section 2
3. `requirements.txt` (pinned versions)
4. `.env.example` with all environment variables from Section 4
5. `app/config.py` — Settings class using pydantic-settings
6. `app/database.py` — SQLAlchemy async engine + session factory
7. `app/models/base.py` — Base and TenantScopedModel
8. All model files from `app/models/` (tenant, user, student, school_class, attendance, message, report, file_entity, invitation, notification, webhook, import_job)
9. Alembic setup + initial migration that creates all tables
10. `app/main.py` — FastAPI app factory with a `/health` endpoint
11. `docker-compose.yml` for local dev (Redis only — I already have PostgreSQL running locally)

### Phase 2: Auth + Tenancy
12. `app/utils/security.py` — JWT encode/decode, password hashing with passlib[bcrypt]
13. `app/utils/tenant_context.py` — context variables
14. `app/utils/permissions.py` — role-based permission decorators
15. `app/middleware/tenant.py` — tenant context middleware
16. `app/middleware/auth.py` — auth middleware
17. `app/schemas/auth.py` and `app/schemas/common.py` — Pydantic schemas
18. `app/services/auth_service.py` — login, register, token refresh
19. `app/api/v1/auth.py` — all auth API endpoints from the spec
20. `app/web/auth.py` — login and register HTML page routes
21. `app/templates/base.html` — master layout with navbar, sidebar, mobile nav
22. `app/templates/auth/login.html` and `register.html`
23. `app/templates/components/` — all reusable components (_navbar, _sidebar, _mobile_nav, _toast, _modal, etc.)
24. `app/static/js/app.js` — global utilities (fetch wrapper, toast, confirm, debounce)
25. `app/static/css/app.css` — minimal custom styles
26. `app/exceptions.py` — exception hierarchy + global handlers
27. `scripts/create_super_admin.py` — CLI script to create the first super admin user
28. `scripts/seed.py` — development seed data (1 tenant, 1 admin, 2 teachers, 1 class, 5 students)

## Important design decisions

- **Redis is optional for local dev.** When `REDIS_URL` is not set or Redis is unavailable:
  - Background tasks should run in-process (call the function directly instead of enqueueing to arq)
  - WebSocket should use an in-memory connection manager without Redis pub/sub
  - This must be transparent — the rest of the code just calls `enqueue_task()` and it works either way
- **Auth uses both cookies AND bearer tokens.** Web views use HttpOnly cookies. API clients use Authorization header. Both must work.
- **Tailwind via CDN** (`<script src="https://cdn.tailwindcss.com">`) with the custom config from Section 9. This is fine for now.
- **Inter font** from Google Fonts CDN.
- **All database operations must be async** using SQLAlchemy 2.0 async patterns.
- **Use uuid7** (time-sortable) for all primary keys.
- **Every tenant-scoped query** must filter by tenant_id. No exceptions.

## My local environment

- Python 3.12
- PostgreSQL running on localhost:5432
- Database name: `classup` (create it if the seed script needs it)
- No Redis locally (make it optional as described above)
- OS: Windows

## Quality expectations

- Type hints on every function
- Docstrings on every service method
- The login page must look polished and professional — purple gradient branding, centered card, mobile responsive
- The base layout must have working navigation with role-based sidebar items
- After building, give me the commands to:
  1. Set up a virtual environment
  2. Install dependencies
  3. Run migrations
  4. Create a super admin
  5. Seed test data
  6. Start the server
