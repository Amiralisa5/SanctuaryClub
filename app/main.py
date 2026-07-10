import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .database import SessionLocal, engine
from .database import Base
from .routers import account, admin, auth, client, coach
from .security import LoginRequired
from .seed import seed_all
from .services.attendance import auto_mark_absent
from .web import render

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sanctuaryclub")

scheduler: BackgroundScheduler | None = None


def run_auto_attendance() -> None:
    db = SessionLocal()
    try:
        marked = auto_mark_absent(db)
        if marked:
            logger.info("Auto-attendance marked %d booking(s) absent", marked)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    if config.AUTO_CREATE_TABLES:
        Base.metadata.create_all(engine)
    seed_all()
    if not config.DISABLE_SCHEDULER:
        scheduler = BackgroundScheduler(timezone=config.TIMEZONE)
        scheduler.add_job(run_auto_attendance, "interval",
                          minutes=config.AUTO_ATTENDANCE_INTERVAL_MINUTES)
        scheduler.start()
        logger.info("Auto-attendance scheduler started (every %d min)",
                    config.AUTO_ATTENDANCE_INTERVAL_MINUTES)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="SanctuaryClub",
    description="Multi-coach home gym coaching platform: programs, bookings, attendance.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, same_site="lax")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

Path(config.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=config.UPLOAD_DIR), name="media")


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    return render(request, "error.html", status_code=403,
                  message=getattr(exc, "detail", "You do not have access to this page."))


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return render(request, "error.html", status_code=404, message="Page not found.")


app.include_router(auth.router)
app.include_router(account.router)
app.include_router(admin.router)
app.include_router(coach.router)
app.include_router(client.router)
