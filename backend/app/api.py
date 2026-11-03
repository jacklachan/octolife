"""REST API (ARCHITECTURE.md section 9.1)."""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.agents import academic_intel, internship_intel, mail_intel
from app.agents.central_coordinator import generate_briefing
from app.agents.deadline_guardian import normalize_interval
from app.config import settings
from app.database import get_db
from app.llm import LLMProvider, get_llm
from app.models import AgentEvent, AgentRun, Notification, Opportunity, Task, TaskRevision, UserPreferences, ensure_user, now
from app.orchestration.contracts import TaskConfirmed, TaskRescheduled
from app.orchestration.outbox import emit, track_run
from app.schemas import (AcademicQuery, DocumentUpload, NotificationOut, OpportunityOut, OpportunityStatusUpdate,
                         RawEmail, TaskCreate, TaskOut, TaskUpdate)

router = APIRouter(prefix="/api/v1")
Db = Annotated[Session, Depends(get_db)]


def current_user(db: Db) -> UserPreferences:
    # shortcut: single local user, no auth yet; add the JWT cookie guard (ARCHITECTURE 11.1.4) before exposing beyond localhost
    return ensure_user(db)


def llm_provider() -> LLMProvider:
    return get_llm()


User = Annotated[UserPreferences, Depends(current_user)]
LLM = Annotated[LLMProvider, Depends(llm_provider)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def _owned(db: Session, model, obj_id: str, user: UserPreferences):
    obj = db.get(model, obj_id)
    if obj is None or obj.user_id != user.id:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


@router.get("/health")
def health(db: Db):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, {"status": "unhealthy", "database": "unreachable"})
    return {"status": "healthy", "database": "connected", "time_utc": now()}


# --- tasks ---------------------------------------------------------------

@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(db: Db, user: User, domain: str | None = None, status: str | None = None,
               from_utc: datetime | None = None, to_utc: datetime | None = None, limit: Limit = 50, offset: Offset = 0):
    q = select(Task).where(Task.user_id == user.id)
    if domain:
        q = q.where(Task.domain == domain)
    if status:
        q = q.where(Task.status == status)
    if from_utc:
        q = q.where(Task.due_datetime_utc >= from_utc)
    if to_utc:
        q = q.where(Task.due_datetime_utc <= to_utc)
    return db.scalars(q.order_by(Task.due_datetime_utc).limit(limit).offset(offset)).all()


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(body: TaskCreate, db: Db, user: User):
    try:
        start, end = normalize_interval(body.due_datetime_utc, body.scheduled_start_utc, body.estimated_duration_minutes)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    task = Task(user_id=user.id, title=body.title, description=body.description, domain=body.domain,
                status="confirmed", due_datetime_utc=end, scheduled_start_utc=start, scheduled_end_utc=end,
                estimated_duration_minutes=body.estimated_duration_minutes, user_confirmed_at=now())
    db.add(task)
    db.flush()
    with track_run(db, "User", "api") as run:
        emit(db, run, TaskConfirmed, user.id, natural_key=task.id, task_id=task.id, confirmed_at_utc=now())
    db.commit()
    return task


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: str, body: TaskUpdate, db: Db, user: User):
    task = _owned(db, Task, task_id, user)
    changes = body.model_dump(exclude_unset=True, exclude_none=True, exclude={"reason"})
    old_due = task.due_datetime_utc
    for field in ("title", "description", "domain", "status", "estimated_duration_minutes"):
        if field in changes:
            setattr(task, field, changes[field])
    if changes.keys() & {"due_datetime_utc", "scheduled_start_utc", "estimated_duration_minutes"}:
        try:
            task.scheduled_start_utc, task.scheduled_end_utc = normalize_interval(
                changes.get("due_datetime_utc", task.due_datetime_utc), changes.get("scheduled_start_utc"),
                task.estimated_duration_minutes)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        task.due_datetime_utc = task.scheduled_end_utc
    if task.due_datetime_utc != old_due:
        task.revision_count += 1
        db.add(TaskRevision(task_id=task.id, revision_number=task.revision_count, previous_due_datetime_utc=old_due,
                            new_due_datetime_utc=task.due_datetime_utc, reason=body.reason, changed_by="User"))
    if task.due_datetime_utc != old_due or "scheduled_start_utc" in changes:
        with track_run(db, "User", "api") as run:
            emit(db, run, TaskRescheduled, user.id, natural_key=f"{task.id}|{task.scheduled_start_utc}|{task.due_datetime_utc}",
                 task_id=task.id, revision_number=task.revision_count)
    db.commit()
    return task


@router.post("/tasks/{task_id}/confirm", response_model=TaskOut)
def confirm_task(task_id: str, db: Db, user: User):
    task = _owned(db, Task, task_id, user)
    if task.status != "proposed":
        raise HTTPException(409, f"task is {task.status}, only proposed tasks can be confirmed")
    task.status, task.user_confirmed_at = "confirmed", now()
    with track_run(db, "User", "api") as run:
        emit(db, run, TaskConfirmed, user.id, natural_key=task.id, task_id=task.id, confirmed_at_utc=task.user_confirmed_at)
    db.commit()
    return task


@router.delete("/tasks/{task_id}", status_code=204)
def cancel_task(task_id: str, db: Db, user: User):
    _owned(db, Task, task_id, user).status = "cancelled"  # soft delete keeps the evidence trail
    db.commit()
    return Response(status_code=204)


# --- notifications -------------------------------------------------------

@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(db: Db, user: User, limit: Limit = 20, offset: Offset = 0):
    return db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.dismissed_at.is_(None))
                      .order_by(Notification.created_at.desc()).limit(limit).offset(offset)).all()


@router.post("/notifications/{notification_id}/dismiss", response_model=NotificationOut)
def dismiss_notification(notification_id: str, db: Db, user: User):
    n = _owned(db, Notification, notification_id, user)
    n.dismissed_at = n.dismissed_at or now()
    db.commit()
    return n


# --- agents' inputs ------------------------------------------------------

@router.post("/mail/ingest")
def ingest_mail(body: RawEmail, db: Db, user: User, llm: LLM):
    """Feed one raw RFC 2822 email (stand-in for Gmail sync until OAuth lands)."""
    try:
        record = mail_intel.ingest_email(db, user, body.raw, llm)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if record is None:
        return {"status": "duplicate"}
    return {"status": "ingested", "email_id": record.id, "classification": record.classification_label,
            "actionable": record.is_actionable}


@router.post("/opportunities/harvest")
def harvest_opportunities(db: Db, user: User):
    found = internship_intel.harvest(db, user, [internship_intel.JsonFeedAdapter(p) for p in settings.job_feed_paths])
    return {"discovered": len(found)}


@router.get("/opportunities", response_model=list[OpportunityOut])
def list_opportunities(db: Db, user: User, role_category: str | None = None, status: str | None = None,
                       limit: Limit = 50, offset: Offset = 0):
    q = select(Opportunity).where(Opportunity.user_id == user.id)
    if role_category:
        q = q.where(Opportunity.role_category == role_category)
    if status:
        q = q.where(Opportunity.status == status)
    return db.scalars(q.order_by(Opportunity.closing_date_utc).limit(limit).offset(offset)).all()


@router.patch("/opportunities/{opportunity_id}/status", response_model=OpportunityOut)
def update_opportunity_status(opportunity_id: str, body: OpportunityStatusUpdate, db: Db, user: User):
    opp = _owned(db, Opportunity, opportunity_id, user)
    opp.status = body.status
    if body.status == "applied" and opp.applied_at_utc is None:
        opp.applied_at_utc = now()
    db.commit()
    return opp


@router.post("/courses/upload", status_code=201)
def upload_course_document(body: DocumentUpload, db: Db, user: User, llm: LLM):
    # shortcut: JSON text upload (Markdown/plain). PDF needs pypdf/pdfplumber + multipart; add with Milestone 5 PDFs
    doc = academic_intel.ingest_document(db, user, body.filename, body.text, body.course_code, body.term, llm,
                                         body.course_title)
    return {"document_id": doc.id, "parsing_status": doc.parsing_status}


@router.post("/academic/query")
def academic_query(body: AcademicQuery, db: Db, user: User):
    return academic_intel.query(db, user, body.question)


# --- dashboard & observability ------------------------------------------

@router.get("/dashboard/briefing", response_model=NotificationOut)
def dashboard_briefing(db: Db, user: User, llm: LLM):
    return generate_briefing(db, user, llm)


@router.get("/dashboard/summary")
def dashboard_summary(db: Db, user: User):
    counts = dict(db.execute(select(Task.status, func.count()).where(Task.user_id == user.id).group_by(Task.status)).all())
    unread = db.scalar(select(func.count()).select_from(Notification)
                       .where(Notification.user_id == user.id, Notification.dismissed_at.is_(None)))
    return {"tasks_by_status": counts, "unread_notifications": unread}


@router.get("/agents/runs")
def agent_runs(db: Db, agent_name: str | None = None, limit: Limit = 50, offset: Offset = 0):
    q = select(AgentRun)
    if agent_name:
        q = q.where(AgentRun.agent_name == agent_name)
    rows = db.scalars(q.order_by(AgentRun.created_at.desc()).limit(limit).offset(offset)).all()
    return [{"id": r.id, "agent_name": r.agent_name, "trigger": r.trigger_source, "status": r.status,
             "duration_ms": r.duration_ms, "model": r.model_name, "created_at": r.created_at} for r in rows]


@router.get("/agents/events")
def agent_events(db: Db, status: str | None = None, limit: Limit = 50, offset: Offset = 0):
    q = select(AgentEvent)
    if status:
        q = q.where(AgentEvent.status == status)
    rows = db.scalars(q.order_by(AgentEvent.created_at.desc()).limit(limit).offset(offset)).all()
    return [{"id": e.id, "type": e.event_type, "status": e.status, "depth": e.causation_depth,
             "parent": e.parent_event_id, "retries": e.retry_count, "payload": e.payload_json} for e in rows]
