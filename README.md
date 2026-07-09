# SanctuaryClub

Home Gym Coaching Platform

## Overview
A multi-coach home gym management platform with role-based access control (Admin, Coach, Client), training program management, booking system, and automated attendance tracking.

## Quick start

```bash
pip install -r requirements.txt

# optional: create demo coach/client accounts alongside the bootstrap admin
python -m app.demo

uvicorn app.main:app --reload
```

Open http://localhost:8000 and sign in:

| Role | Email | Password |
| --- | --- | --- |
| Admin | `admin@sanctuary.club` | `admin123` |
| Coach (demo) | `coach@sanctuary.club` | `coach123` |
| Client (demo) | `client@sanctuary.club` | `client123` |

Override the bootstrap admin with the `ADMIN_EMAIL` / `ADMIN_PASSWORD` / `ADMIN_NAME` environment variables. Change `SECRET_KEY` in production.

The app uses SQLite out of the box; point `DATABASE_URL` at PostgreSQL for production, e.g.:

```bash
export DATABASE_URL=postgresql+psycopg2://user:pass@localhost/sanctuaryclub
```

Interactive API documentation is served at `/docs` (Swagger UI) and `/redoc`.

### Docker (app + PostgreSQL in one command)

```bash
docker compose up --build
```

This starts a `postgres:16` container with a persistent volume, waits for it to
become healthy, runs `alembic upgrade head` to create the schema, seeds the time
sections / capacity defaults / bootstrap admin, and serves the app on
http://localhost:8000 — already linked to the database via `DATABASE_URL`.
Override `SECRET_KEY`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD` in
`docker-compose.yml` for anything beyond local use.

### Database migrations (Alembic)

Schema versions live in `alembic/versions/`. Alembic reads the same
`DATABASE_URL` as the app.

```bash
alembic upgrade head                          # apply pending migrations
alembic revision --autogenerate -m "message"  # after changing app/models.py
alembic check                                 # verify models match migrations
```

Locally the app still auto-creates missing tables on startup for convenience.
In production (and in Docker) set `AUTO_CREATE_TABLES=0` so the schema is
managed exclusively by migrations.

### Tests

```bash
python -m pytest tests/
```

## Features

### Must Have (MVP)
- ✅ Multi-coach accounts with authentication and roles (Admin, Coach, Client)
- ✅ Client profiles with coach assignment and data isolation
- ✅ Weekly training program builder with day-by-day workouts
- ✅ Attendance tracking per session with monthly aggregation
- ✅ Client self-check-in with weight, RPE, completion percentage, and notes
- ✅ Per-client scheduling with bulk monthly booking wizard
- ✅ Rescheduling policy (up to 2 hours before start)
- ✅ Admin management for coaches/clients and capacity configuration
- ✅ Mobile-friendly design
- ✅ Privacy & access control with audit logs
- ✅ Web UI with role-based dashboards

### Should Have
- 🔲 Program templates
- ✅ Exercise library with tags
- ✅ Calendar views for coach and client
- 🔲 Email notifications
- 🔲 Progress metrics dashboard
- 🔲 Media uploads (demo videos/links)
- ✅ Slot capacity enforcement (gym-wide and per-coach)

## Technical Stack
- **Framework**: Python — FastAPI
- **Frontend**: Server-rendered Jinja2 templates, responsive CSS (no JS build step)
- **Database**: PostgreSQL (SQLite fallback for development/tests)
- **ORM**: SQLAlchemy 2.0 with Alembic migrations
- **Deployment**: Docker Compose (app + PostgreSQL 16, migrations run on boot)
- **Authentication**: Cookie-based sessions (signed via `itsdangerous`) with roles; PBKDF2-SHA256 password hashing
- **Logging**: Python `logging` + database-backed audit log
- **API Documentation**: OpenAPI / Swagger UI at `/docs`
- **Background Services**: APScheduler job every 10 minutes for auto-attendance

## Architecture

### Project layout
```
app/
  main.py             # FastAPI app, session middleware, background scheduler
  config.py           # env-driven settings and business constants
  models.py           # SQLAlchemy models (User, Coach, Client, Booking, ...)
  security.py         # password hashing, session auth, role guards
  audit.py            # audit log helper
  seed.py             # bootstrap time sections, capacity defaults, admin user
  demo.py             # optional demo data (python -m app.demo)
  services/
    scheduling.py     # capacity + quota rules, booking, reschedule, bulk wizard
    attendance.py     # check-in window, auto-absent job, monthly aggregation
  routers/            # auth, admin, coach, client HTML routes
  templates/          # Jinja2 role-based dashboards
  static/style.css    # mobile-friendly styling
tests/                # pytest suite for auth, scheduling, attendance rules
alembic/              # database migrations (alembic upgrade head)
Dockerfile            # app image
docker-compose.yml    # app + PostgreSQL 16 stack
```

### Data Model
- **User**: Identity-based authentication with roles
- **Coach**: Linked to User, manages clients
- **Client**: Linked to User and primary Coach
- **PlanMonth**: Monthly subscription plans with quotas (4, 8, 12, 16, 20, 22, Unlimited)
- **TimeSection**: 8 time slots (06:00-08:00, 08:00-10:00, ..., 20:00-22:00)
- **Booking**: Session bookings with status tracking
- **Attendance**: Present/Absent/Excused tracking with auto-marking
- **ProgramWeek**: Weekly training programs
- **WorkoutDay**: Daily workouts within programs
- **Exercise**: Exercise library
- **WorkoutItem**: Exercises within workout days
- **CapacityOverride**: Per date/section capacity (gym-wide or per-coach)
- **Setting**: Admin-editable capacity defaults
- **AuditLog**: Who did what, when

### Scheduling System
- **Operating Hours**: 06:00 - 22:00
- **Time Slots**: 8 sections × 2 hours each
- **Capacity**:
  - Gym-wide default: 20 per section
  - Per-coach default: 6 per section
  - Configurable per date/section (admin UI)
- **Quotas**: bookings count against the client's PlanMonth; cancelled sessions free the slot
- **Rescheduling/cancellation**: allowed up to 2 hours before section start
- **Bulk wizard**: pick a month, weekdays, and section — books every matching future date, reporting any skipped dates (full slots, quota reached, past dates)

### Attendance Rules
- **Auto-Present**: Check-in window from -10 minutes to +20 minutes relative to section start
- **Auto-Absent**: Automatically marked absent at +10 minutes after section end if not checked in
- **Background Service**: Runs every 10 minutes to process auto-attendance
- **Manual override**: coaches can mark Present/Absent/Excused at any time
- **Monthly aggregation**: booked/present/absent/excused/pending per client per month

### Privacy & Access Control
- Coaches can only see and manage their own clients (bookings, programs, attendance)
- Clients can only see their own data
- Admin manages accounts, coach assignment, and capacity
- Sensitive actions (logins, bookings, attendance marks, admin changes) are written to the audit log

### Timezone
- **Default**: Asia/Tehran (configurable via `APP_TIMEZONE`); all times are gym-local
