"""Agent 3: Deadline Guardian. Deterministic timekeeper - no LLM imports in this module, by design (ADR-002)."""
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.models import Notification, Task, uid
from app.orchestration.contracts import DeadlineConflictDetected, EventPriority
from app.orchestration.outbox import emit, subscribe

AGENT = "DeadlineGuardian"
ACTIVE = ("proposed", "confirmed")
REMINDER_OFFSETS = (timedelta(hours=2), timedelta(hours=48))  # tightest first


def _require_aware(*values):
    for value in values:
        if value is not None and value.tzinfo is None:
            raise ValueError("naive datetime: pass timezone-aware values")


def user_zone(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc  # spec: fall back to UTC on an unknown timezone


def calculate_time_remaining(due_utc: datetime, now_utc: datetime) -> timedelta:
    _require_aware(due_utc, now_utc)
    return due_utc - now_utc


def normalize_interval(due_utc: datetime, start_utc: datetime | None, duration_minutes: int) -> tuple[datetime, datetime]:
    """An instantaneous deadline becomes [due - duration, due]. Converts to UTC first so DST can't skew durations."""
    _require_aware(due_utc, start_utc)
    end = due_utc.astimezone(timezone.utc)
    start = start_utc.astimezone(timezone.utc) if start_utc else end - timedelta(minutes=duration_minutes)
    if start > end:
        raise ValueError("interval starts after it ends")
    return start, end


def overlap(a_start, a_end, b_start, b_end) -> timedelta | None:
    if a_start < b_end and b_start < a_end:  # touching intervals do not collide
        return min(a_end, b_end) - max(a_start, b_start)
    return None


def detect_interval_collisions(tasks) -> list[tuple[object, object, float]]:
    """Returns (task_a, task_b, overlap_minutes) for every colliding pair."""
    # shortcut: O(n^2) pairwise scan; fine for one student's agenda, switch to a sweep line past a few thousand tasks
    found = []
    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            d = overlap(a.scheduled_start_utc, a.scheduled_end_utc, b.scheduled_start_utc, b.scheduled_end_utc)
            if d:
                found.append((a, b, d.total_seconds() / 60))
    return found


@subscribe("TaskProposed", agent=AGENT)
@subscribe("TaskConfirmed", agent=AGENT)
@subscribe("TaskRescheduled", agent=AGENT)
def check_conflicts(session, run, event) -> None:
    task = session.get(Task, event.payload_json["task_id"])
    if task is None or task.status not in ACTIVE:
        return
    others = session.scalars(select(Task).where(
        Task.user_id == task.user_id, Task.id != task.id, Task.status.in_(ACTIVE),
        Task.scheduled_start_utc < task.scheduled_end_utc, Task.scheduled_end_utc > task.scheduled_start_utc,
    )).all()
    for other in others:
        a, b = sorted((task, other), key=lambda t: t.id)  # stable pair order -> one event per pair
        start, end = max(a.scheduled_start_utc, b.scheduled_start_utc), min(a.scheduled_end_utc, b.scheduled_end_utc)
        minutes = int((end - start).total_seconds() // 60)
        emit(session, run, DeadlineConflictDetected, task.user_id,
             natural_key=f"{a.id}|{b.id}|{start.isoformat()}|{end.isoformat()}", parent=event,
             conflict_id=uid(), task_id_a=a.id, task_id_b=b.id, overlap_start_utc=start, overlap_end_utc=end,
             conflict_severity=EventPriority.HIGH if a.domain != b.domain else EventPriority.MEDIUM,
             description=f"'{a.title}' ({a.domain}) overlaps '{b.title}' ({b.domain}) by {minutes} min")


def evaluate_due_reminders(session, now_utc: datetime) -> int:
    """Marks overdue tasks and creates at most one reminder per offset per due date. Returns reminders created."""
    created = 0
    for task in session.scalars(select(Task).where(Task.status.in_(ACTIVE))):
        if task.due_datetime_utc <= now_utc:
            task.status = "overdue"
            continue
        offset = next((o for o in REMINDER_OFFSETS if task.due_datetime_utc - o <= now_utc), None)
        if offset is None or task.status != "confirmed":
            continue
        key = hashlib.sha256(f"reminder|{task.id}|{task.due_datetime_utc.isoformat()}|{offset}".encode()).hexdigest()
        if session.scalar(select(Notification.id).where(Notification.idempotency_hash == key)):
            continue
        hours = int(offset.total_seconds() // 3600)
        session.add(Notification(
            user_id=task.user_id, task_id=task.id, type="deadline_reminder",
            priority="high" if offset <= timedelta(hours=2) else "medium",
            title=f"Due within {hours}h: {task.title}",
            body_markdown=f"**{task.title}** is due at {task.due_datetime_utc:%Y-%m-%d %H:%M} UTC.",
            scheduled_for_utc=task.due_datetime_utc - offset, idempotency_hash=key))
        created += 1
    session.commit()
    return created
