"""Shared steps every perception agent uses to turn evidence into a proposed task."""
from datetime import datetime, timedelta, timezone

from app.agents.deadline_guardian import normalize_interval
from app.llm import ExtractedTask
from app.models import AuditEvent, EvidenceSource, Task
from app.orchestration.contracts import TaskProposed
from app.orchestration.outbox import emit

LOW_CONFIDENCE = 0.3


def validate_candidate_date(iso: str | None, anchor: datetime) -> tuple[datetime | None, float]:
    """Deterministic check of an LLM-proposed timestamp. Returns (utc datetime or None, confidence)."""
    if not iso:
        return None, 0.0
    try:
        value = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None, 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=anchor.tzinfo)  # assume the sender's timezone
    value = value.astimezone(timezone.utc)
    if not anchor - timedelta(days=1) <= value <= anchor + timedelta(days=730):
        return value, LOW_CONFIDENCE  # parseable but implausible: keep it, flag it for human review
    return value, 0.9


def create_task(session, run, user_id, evidence: EvidenceSource, title, domain, due, start=None,
                explicit=True, duration_minutes=60) -> Task:
    """Atomically stage evidence + proposed task + TaskProposed event (caller commits)."""
    try:
        start, end = normalize_interval(due, start, duration_minutes)
    except ValueError:
        start, end = normalize_interval(due, None, duration_minutes)
    session.add(evidence)
    session.flush()
    task = Task(user_id=user_id, evidence_source_id=evidence.id, title=title, domain=domain, due_datetime_utc=end,
                scheduled_start_utc=start, scheduled_end_utc=end, is_deadline_explicit=explicit,
                estimated_duration_minutes=int((end - start).total_seconds() // 60) or duration_minutes)
    session.add(task)
    session.flush()
    emit(session, run, TaskProposed, user_id, natural_key=task.id, task_id=task.id, title=task.title,
         domain=domain, due_datetime_utc=end, scheduled_start_utc=start, scheduled_end_utc=end,
         is_explicit=explicit, confidence_score=evidence.confidence_score,
         evidence_snippet=evidence.raw_text_snippet)
    return task


def propose_from_extraction(session, run, user_id, item: ExtractedTask, anchor: datetime, source_type: str,
                            source_ref: str, source_text: str, domain: str | None = None,
                            title_prefix: str = "") -> Task | None:
    due, confidence = validate_candidate_date(item.due_iso, anchor)
    if due is None:
        session.add(AuditEvent(user_id=user_id, action="unparseable_date",
                               details_json={"source": source_ref, "candidate": item.due_iso}))
        return None
    start, _ = validate_candidate_date(item.start_iso, anchor)
    if item.evidence_quote not in source_text:
        confidence = min(confidence, 0.5)  # quote is not verbatim, so the grounding can't be trusted
    evidence = EvidenceSource(user_id=user_id, source_type=source_type, source_reference_id=source_ref,
                              raw_text_snippet=item.evidence_quote, confidence_score=confidence)
    return create_task(session, run, user_id, evidence, (title_prefix + item.title)[:255], domain or item.domain,
                       due, start, explicit=item.is_explicit and confidence > LOW_CONFIDENCE)
