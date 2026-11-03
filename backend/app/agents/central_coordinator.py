"""Agent 5: Central Coordination - the 'central brain'. Only wakes up for cross-arm problems:
conflicts the specialists can't resolve locally, and the daily briefing. It recommends; it never
moves a user's task by itself.
"""
import hashlib
import logging
from datetime import timedelta

from sqlalchemy import select

from app.agents.deadline_guardian import ACTIVE, user_zone
from app.llm import LLMProvider, wrap_untrusted
from app.models import AuditEvent, Notification, Task, UserPreferences, now
from app.orchestration.outbox import subscribe, track_run

AGENT = "CentralCoordinator"
MAX_ALERTS = 3  # notification-fatigue ceiling per briefing
CONFLICT_TYPES = ("cross_domain_conflict", "schedule_conflict")
log = logging.getLogger(__name__)


def arbitrate(a, b, academic_weight: float):
    """Deterministic priority rule. Returns (keep, move, reason)."""
    if a.domain != b.domain and "academic" in (a.domain, b.domain):
        academic, other = (a, b) if a.domain == "academic" else (b, a)
        if academic_weight >= 0.5:
            return academic, other, f"academic priority weight {academic_weight:.1f} favours coursework"
        return other, academic, f"academic priority weight {academic_weight:.1f} favours {other.domain}"
    keep, move = sorted((a, b), key=lambda t: t.created_at)
    return keep, move, "first-come, first-served"


def _hash(*parts) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


@subscribe("DeadlineConflictDetected", agent=AGENT)
def on_conflict(session, run, event) -> None:
    payload = event.payload_json
    a, b = session.get(Task, payload["task_id_a"]), session.get(Task, payload["task_id_b"])
    if a is None or b is None:
        return
    key = _hash("conflict", event.idempotency_key)
    if session.scalar(select(Notification.id).where(Notification.idempotency_hash == key)):
        return
    user = session.get(UserPreferences, a.user_id)
    keep, move, reason = arbitrate(a, b, user.academic_priority_weight)
    cross = a.domain != b.domain
    if not cross and (a.scheduled_start_utc, a.scheduled_end_utc) == (b.scheduled_start_utc, b.scheduled_end_utc):
        # same slot, same domain: almost always one event reported by two sources (e.g. email + syllabus)
        session.add(Notification(
            user_id=a.user_id, task_id=move.id, type="possible_duplicate", priority="low",
            title=f"Possible duplicate: {move.title}"[:255],
            body_markdown=f"**{keep.title}** and **{move.title}** have the same domain and time slot. "
                          f"If they are the same event, cancel **{move.title}**.",
            scheduled_for_utc=now(), idempotency_hash=key))
        return
    session.add(Notification(
        user_id=a.user_id, task_id=move.id, type="cross_domain_conflict" if cross else "schedule_conflict",
        priority="high" if cross else "medium", title=f"Conflict: {keep.title} vs {move.title}"[:255],
        body_markdown=f"{payload['description']}.\n\n**Recommendation:** keep **{keep.title}**, "
                      f"reschedule **{move.title}** ({reason}).",
        scheduled_for_utc=now(), idempotency_hash=key))


def edf_briefing(tasks, alerts, tz) -> str:
    """Rule-based fallback: Earliest-Deadline-First agenda straight from the database."""
    lines = ["# Daily briefing", ""]
    if alerts:
        lines += ["## Conflicts", *[f"- {n.title}" for n in alerts], ""]
    lines.append("## Upcoming (earliest deadline first)")
    for t in tasks:
        lines.append(f"- **{t.due_datetime_utc.astimezone(tz):%a %d %b %H:%M}** - {t.title} ({t.domain}, {t.status})")
    if not tasks:
        lines.append("- Nothing due in the next 7 days.")
    return "\n".join(lines)


def generate_briefing(session, user: UserPreferences, llm: LLMProvider, now_utc=None) -> Notification:
    """Upserts today's briefing. Falls back to EDF if the LLM fails, so there is always a briefing."""
    now_utc = now_utc or now()
    tz = user_zone(user.timezone)
    tasks = session.scalars(select(Task).where(
        Task.user_id == user.id, Task.status.in_((*ACTIVE, "overdue")),
        Task.due_datetime_utc <= now_utc + timedelta(days=7)).order_by(Task.due_datetime_utc)).all()
    alerts = session.scalars(select(Notification).where(
        Notification.user_id == user.id, Notification.type.in_(CONFLICT_TYPES), Notification.dismissed_at.is_(None))
        .order_by(Notification.created_at.desc()).limit(MAX_ALERTS)).all()
    fallback = edf_briefing(tasks, alerts, tz)

    with track_run(session, AGENT, "daily_briefing") as run:
        run.model_name = llm.name
        try:
            # task titles came from emails, so the agenda is untrusted input too
            body = llm.synthesize_briefing(wrap_untrusted(fallback, "agenda", str(now_utc.date()))) or ""
            run.prompt_tokens, run.completion_tokens = llm.last_usage
        except Exception as exc:
            log.warning("briefing LLM failed, using EDF fallback: %s", exc)
            session.add(AuditEvent(user_id=user.id, action="briefing_llm_fallback", details_json={"error": repr(exc)}))
            body = ""
        body = body if body.strip() else fallback

    local_day = now_utc.astimezone(tz).date()
    key = _hash("briefing", user.id, local_day)
    briefing = session.scalar(select(Notification).where(Notification.idempotency_hash == key))
    if briefing is None:
        briefing = Notification(user_id=user.id, type="daily_briefing", priority="medium",
                                title=f"Daily briefing {local_day}", body_markdown=body,
                                scheduled_for_utc=now_utc, idempotency_hash=key)
        session.add(briefing)
    else:
        briefing.body_markdown = body
    session.commit()
    return briefing
