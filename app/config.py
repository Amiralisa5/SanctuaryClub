import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./sanctuary.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Tehran")

# Gym operating hours: 06:00 - 22:00 split into 2-hour sections
OPEN_HOUR = 6
CLOSE_HOUR = 22
SECTION_HOURS = 2

# Default slot capacities (admin-configurable at runtime via Setting rows)
GYM_DEFAULT_CAPACITY = 20
COACH_DEFAULT_CAPACITY = 6

# Attendance rules
CHECKIN_MINUTES_BEFORE = 10   # check-in opens 10 min before section start
CHECKIN_MINUTES_AFTER = 20    # check-in closes 20 min after section start
AUTO_ABSENT_MINUTES_AFTER_END = 10  # auto-absent 10 min after section end
AUTO_ATTENDANCE_INTERVAL_MINUTES = 10

# Rescheduling / cancellation cutoff
RESCHEDULE_CUTOFF_HOURS = 2

# Monthly plan quota choices (None = unlimited)
PLAN_QUOTAS = [4, 8, 12, 16, 20, 22, None]

# Bootstrap admin account
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@sanctuary.club")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Sanctuary Admin")

DISABLE_SCHEDULER = os.environ.get("DISABLE_SCHEDULER", "") == "1"

# Email notifications: with SMTP_HOST set, mail is sent via SMTP; otherwise the
# console backend logs the message and stores it in the email log only.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "1") == "1"
EMAIL_FROM = os.environ.get("EMAIL_FROM", "no-reply@sanctuary.club")

# Public base URL used in OAuth redirect URIs and password-reset links
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")

# OAuth sign-in providers (buttons appear on the login page; flows need these set)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")

# Password reset links expire after this many minutes; tokens are single-use
RESET_TOKEN_TTL_MINUTES = int(os.environ.get("RESET_TOKEN_TTL_MINUTES", "60"))

# Media uploads (exercise demo videos/images), served from /media
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "./media")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
ALLOWED_MEDIA_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".gif", ".jpg", ".jpeg", ".png", ".webp"}

# When "1" (default), tables are created via SQLAlchemy create_all on startup —
# convenient for local dev and tests. Set to "0" in production and manage the
# schema with Alembic migrations instead (alembic upgrade head).
AUTO_CREATE_TABLES = os.environ.get("AUTO_CREATE_TABLES", "1") == "1"
