"""Create demo accounts and data for local exploration.

Usage: python -m app.demo
Accounts: coach@sanctuary.club / coach123, client@sanctuary.club / client123
"""
from sqlalchemy import select

from .database import Base, SessionLocal, engine
from .models import Client, Coach, PlanMonth, Role, User
from .security import hash_password
from .seed import seed_all
from .utils import now


def main() -> None:
    Base.metadata.create_all(engine)
    seed_all()
    db = SessionLocal()
    try:
        if db.scalar(select(User).where(User.email == "coach@sanctuary.club")):
            print("Demo data already present.")
            return
        coach_user = User(email="coach@sanctuary.club", password_hash=hash_password("coach123"),
                          full_name="Demo Coach", role=Role.COACH)
        client_user = User(email="client@sanctuary.club", password_hash=hash_password("client123"),
                           full_name="Demo Client", role=Role.CLIENT)
        db.add_all([coach_user, client_user])
        db.flush()
        coach = Coach(user_id=coach_user.id, bio="Strength & conditioning")
        db.add(coach)
        db.flush()
        client = Client(user_id=client_user.id, coach_id=coach.id)
        db.add(client)
        db.flush()
        current = now()
        db.add(PlanMonth(client_id=client.id, year=current.year, month=current.month, quota=12))
        db.commit()
        print("Demo data created:")
        print("  admin@sanctuary.club / admin123")
        print("  coach@sanctuary.club / coach123")
        print("  client@sanctuary.club / client123 (12-session plan this month)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
