"""Background jobs (ARCHITECTURE.md section 10). Run uvicorn with --workers 1 or every job runs twice."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.agents import internship_intel
from app.agents.central_coordinator import generate_briefing
from app.agents.deadline_guardian import evaluate_due_reminders, user_zone
from app.config import settings
from app.database import SessionLocal
from app.llm import get_llm
from app.models import ensure_user, now
from app.orchestration.outbox import drain

logging.getLogger("apscheduler").setLevel(logging.WARNING)  # the 1s outbox job would flood INFO logs


def _reminders():
    with SessionLocal() as s:
        evaluate_due_reminders(s, now())


def _briefing():
    with SessionLocal() as s:
        generate_briefing(s, ensure_user(s), get_llm())


def _harvest():
    with SessionLocal() as s:
        internship_intel.harvest(s, ensure_user(s),
                                 [internship_intel.JsonFeedAdapter(p) for p in settings.job_feed_paths])


def start_scheduler() -> BackgroundScheduler:
    with SessionLocal() as s:
        user = ensure_user(s)
        hour, minute = map(int, user.daily_briefing_time.split(":"))
        tz = user_zone(user.timezone)
    sched = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1})
    sched.add_job(drain, "interval", args=[SessionLocal], seconds=1, misfire_grace_time=10, id="outbox")
    sched.add_job(_reminders, "interval", seconds=60, misfire_grace_time=30, id="reminders")
    sched.add_job(_briefing, CronTrigger(hour=hour, minute=minute, timezone=tz), misfire_grace_time=1800, id="briefing")
    if settings.job_feed_paths:
        sched.add_job(_harvest, CronTrigger(hour=2, timezone="UTC"), misfire_grace_time=3600, id="harvest")
    sched.start()
    return sched
