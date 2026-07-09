import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .utils import now


class Role(str, enum.Enum):
    ADMIN = "ADMIN"
    COACH = "COACH"
    CLIENT = "CLIENT"


class BookingStatus(str, enum.Enum):
    BOOKED = "BOOKED"
    CANCELLED = "CANCELLED"


class AttendanceStatus(str, enum.Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    EXCUSED = "EXCUSED"


def _enum(e):
    return Enum(e, native_enum=False, length=20)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(_enum(Role))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    coach_profile: Mapped["Coach | None"] = relationship(back_populates="user", uselist=False)
    client_profile: Mapped["Client | None"] = relationship(back_populates="user", uselist=False)


class Coach(Base):
    __tablename__ = "coaches"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    bio: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="coach_profile")
    clients: Mapped[list["Client"]] = relationship(back_populates="coach")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    coach_id: Mapped[int] = mapped_column(ForeignKey("coaches.id"))
    notes: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="client_profile")
    coach: Mapped[Coach] = relationship(back_populates="clients")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="client")
    plans: Mapped[list["PlanMonth"]] = relationship(back_populates="client")


class PlanMonth(Base):
    """Monthly subscription plan; quota=None means unlimited sessions."""

    __tablename__ = "plan_months"
    __table_args__ = (UniqueConstraint("client_id", "year", "month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    quota: Mapped[int | None] = mapped_column(Integer, nullable=True)

    client: Mapped[Client] = relationship(back_populates="plans")

    @property
    def quota_label(self) -> str:
        return "Unlimited" if self.quota is None else str(self.quota)


class TimeSection(Base):
    """One of the 8 fixed 2-hour slots between 06:00 and 22:00."""

    __tablename__ = "time_sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    index: Mapped[int] = mapped_column(Integer, unique=True)
    start_hour: Mapped[int] = mapped_column(Integer)
    end_hour: Mapped[int] = mapped_column(Integer)

    @property
    def label(self) -> str:
        return f"{self.start_hour:02d}:00-{self.end_hour:02d}:00"


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    coach_id: Mapped[int] = mapped_column(ForeignKey("coaches.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("time_sections.id"))
    status: Mapped[BookingStatus] = mapped_column(_enum(BookingStatus), default=BookingStatus.BOOKED)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    client: Mapped[Client] = relationship(back_populates="bookings")
    coach: Mapped[Coach] = relationship()
    section: Mapped[TimeSection] = relationship()
    attendance: Mapped["Attendance | None"] = relationship(back_populates="booking", uselist=False)


class Attendance(Base):
    __tablename__ = "attendances"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), unique=True)
    status: Mapped[AttendanceStatus] = mapped_column(_enum(AttendanceStatus))
    auto: Mapped[bool] = mapped_column(Boolean, default=False)
    marked_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    # Client self-check-in data
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    rpe: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    booking: Mapped[Booking] = relationship(back_populates="attendance")


class ProgramWeek(Base):
    __tablename__ = "program_weeks"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    coach_id: Mapped[int] = mapped_column(ForeignKey("coaches.id"))
    week_start: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    client: Mapped[Client] = relationship()
    coach: Mapped[Coach] = relationship()
    days: Mapped[list["WorkoutDay"]] = relationship(
        back_populates="week", order_by="WorkoutDay.day_index", cascade="all, delete-orphan"
    )


class WorkoutDay(Base):
    __tablename__ = "workout_days"
    __table_args__ = (UniqueConstraint("program_week_id", "day_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    program_week_id: Mapped[int] = mapped_column(ForeignKey("program_weeks.id"))
    day_index: Mapped[int] = mapped_column(Integer)  # 0 = Saturday .. 6 = Friday
    title: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    week: Mapped[ProgramWeek] = relationship(back_populates="days")
    items: Mapped[list["WorkoutItem"]] = relationship(
        back_populates="day", order_by="WorkoutItem.position", cascade="all, delete-orphan"
    )

    DAY_NAMES = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    @property
    def day_name(self) -> str:
        return self.DAY_NAMES[self.day_index]


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(String(255), default="")


class WorkoutItem(Base):
    __tablename__ = "workout_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    workout_day_id: Mapped[int] = mapped_column(ForeignKey("workout_days.id"))
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    sets: Mapped[int] = mapped_column(Integer, default=3)
    reps: Mapped[str] = mapped_column(String(50), default="10")
    target_weight: Mapped[str] = mapped_column(String(50), default="")
    rest_seconds: Mapped[int] = mapped_column(Integer, default=90)
    notes: Mapped[str] = mapped_column(Text, default="")

    day: Mapped[WorkoutDay] = relationship(back_populates="items")
    exercise: Mapped[Exercise] = relationship()


class CapacityOverride(Base):
    """Capacity override for a specific date + section.

    coach_id NULL means a gym-wide override; otherwise per-coach.
    """

    __tablename__ = "capacity_overrides"
    __table_args__ = (UniqueConstraint("date", "section_id", "coach_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    section_id: Mapped[int] = mapped_column(ForeignKey("time_sections.id"))
    coach_id: Mapped[int | None] = mapped_column(ForeignKey("coaches.id"), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer)

    section: Mapped[TimeSection] = relationship()
    coach: Mapped[Coach | None] = relationship()


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(100), default="")
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)

    user: Mapped[User | None] = relationship()
