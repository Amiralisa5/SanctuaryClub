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
