"""ORM models (ARCHITECTURE.md section 6.3). Every proposed task points at an evidence_sources row."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.config import settings
from app.database import Base, UTCDateTime


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


def _pk():
    return mapped_column(String(36), primary_key=True, default=uid)


def _user_fk():
    return mapped_column(ForeignKey("user_preferences.id", ondelete="CASCADE"))


def _created():
    return mapped_column(UTCDateTime, default=now)


class UserPreferences(Base):
    __tablename__ = "user_preferences"
    id: Mapped[str] = _pk()
    email: Mapped[str] = mapped_column(String(255), unique=True)
    full_name: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    academic_priority_weight: Mapped[float] = mapped_column(default=0.6)
    max_daily_study_hours: Mapped[float] = mapped_column(default=4.0)
    daily_briefing_time: Mapped[str] = mapped_column(String(5), default="07:00")
    created_at: Mapped[datetime] = _created()


class EvidenceSource(Base):
    __tablename__ = "evidence_sources"
    __table_args__ = (Index("ix_evidence_source", "source_type", "source_reference_id"),)
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _user_fk()
    source_type: Mapped[str] = mapped_column(String(32))  # email | syllabus_file | job_posting | user_manual
    source_reference_id: Mapped[str] = mapped_column(String(1024))
    raw_text_snippet: Mapped[str] = mapped_column(Text)
    confidence_score: Mapped[float]
    created_at: Mapped[datetime] = _created()


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_interval", "scheduled_start_utc", "scheduled_end_utc"),
    )
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _user_fk()
    evidence_source_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_sources.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(32))  # academic | career | personal
    status: Mapped[str] = mapped_column(String(32), default="proposed")  # proposed|confirmed|completed|overdue|cancelled
    due_datetime_utc: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    scheduled_start_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    scheduled_end_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    is_deadline_explicit: Mapped[bool] = mapped_column(default=True)
    estimated_duration_minutes: Mapped[int] = mapped_column(default=60)
    user_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revision_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now, onupdate=now)


class TaskRevision(Base):
    __tablename__ = "task_revisions"
    __table_args__ = (UniqueConstraint("task_id", "revision_number"),)
    id: Mapped[str] = _pk()
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    revision_number: Mapped[int]
    previous_due_datetime_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    new_due_datetime_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    reason: Mapped[str] = mapped_column(String(255))
    changed_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


class EmailRecord(Base):
    __tablename__ = "email_records"
    __table_args__ = (UniqueConstraint("user_id", "provider_message_id"),)
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _user_fk()
    provider_message_id: Mapped[str] = mapped_column(String(255))
    sender: Mapped[str] = mapped_column(String(255))
    recipient: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    sanitized_body_text: Mapped[str] = mapped_column(Text)
    classification_label: Mapped[str] = mapped_column(String(32), index=True)
    is_actionable: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = _created()


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (UniqueConstraint("user_id", "deduplication_hash"),)
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _user_fk()
    canonical_url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(255))
    organization: Mapped[str] = mapped_column(String(255))
    location: Mapped[str] = mapped_column(String(255))
    role_category: Mapped[str] = mapped_column(String(64))
    cycle_season: Mapped[str] = mapped_column(String(32))
    closing_date_utc: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    source_portal: Mapped[str] = mapped_column(String(64))
    deduplication_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="discovered", index=True)
    applied_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now, onupdate=now)


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("user_id", "course_code", "term"),)
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _user_fk()
    course_code: Mapped[str] = mapped_column(String(32))
    course_title: Mapped[str] = mapped_column(String(255))
    term: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created()


class AcademicDocument(Base):
    __tablename__ = "academic_documents"
    __table_args__ = (UniqueConstraint("user_id", "file_hash_sha256"),)
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _user_fk()
    course_id: Mapped[str | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(255))
    file_hash_sha256: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(64))
    parsing_status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = _created()


class AcademicDocumentChunk(Base):
    __tablename__ = "academic_document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index"),)
    id: Mapped[str] = _pk()
    document_id: Mapped[str] = mapped_column(ForeignKey("academic_documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int]
    chunk_text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int]
    created_at: Mapped[datetime] = _created()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_inbox", "user_id", "dismissed_at", "scheduled_for_utc"),)
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _user_fk()
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32))  # deadline_reminder|cross_domain_conflict|schedule_conflict|daily_briefing
    priority: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255))
    body_markdown: Mapped[str] = mapped_column(Text)
    scheduled_for_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    dismissed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    idempotency_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _created()


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = _pk()
    agent_name: Mapped[str] = mapped_column(String(64))
    trigger_source: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))  # started | success | failed
    duration_ms: Mapped[int] = mapped_column(default=0)
    model_name: Mapped[str | None] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = _created()


class AgentEvent(Base):
    """The transactional outbox (ADR-004)."""

    __tablename__ = "agent_events"
    __table_args__ = (Index("ix_agent_events_status", "status", "created_at"),)
    id: Mapped[str] = _pk()
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending | processed | failed
    causation_depth: Mapped[int] = mapped_column(default=0)
    parent_event_id: Mapped[str | None] = mapped_column(ForeignKey("agent_events.id", ondelete="SET NULL"))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    retry_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = _created()


class DeadLetterEvent(Base):
    __tablename__ = "dead_letter_events"
    id: Mapped[str] = _pk()
    event_id: Mapped[str] = mapped_column(ForeignKey("agent_events.id", ondelete="CASCADE"))
    failed_agent: Mapped[str] = mapped_column(String(64))
    error_message: Mapped[str] = mapped_column(Text)
    stack_trace: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int]
    escalated_at: Mapped[datetime] = _created()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _user_fk()
    action: Mapped[str] = mapped_column(String(64))
    details_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()


def ensure_user(session: Session) -> UserPreferences:
    """Single-user, local-first: the configured student is the only tenant."""
    user = session.scalar(select(UserPreferences).where(UserPreferences.email == settings.user_email))
    if user is None:
        user = UserPreferences(email=settings.user_email, full_name=settings.user_name, timezone=settings.user_timezone)
        session.add(user)
        session.commit()
    return user
