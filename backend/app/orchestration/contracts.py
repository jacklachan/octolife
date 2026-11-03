"""Typed inter-agent events (ARCHITECTURE.md section 7.2)."""
from enum import Enum

from pydantic import AwareDatetime, BaseModel, Field, HttpUrl

MAX_EVENT_DEPTH = 3  # ADR-007


class EventPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BaseAgentEvent(BaseModel):
    event_id: str
    emitted_at_utc: AwareDatetime
    origin_agent: str
    user_id: str
    idempotency_key: str
    causation_depth: int = Field(default=0, ge=0, le=MAX_EVENT_DEPTH)
    parent_event_id: str | None = None


class EmailProcessed(BaseAgentEvent):
    email_record_id: str
    provider_message_id: str
    classification: str
    tasks_extracted_count: int


class TaskProposed(BaseAgentEvent):
    task_id: str
    title: str
    domain: str
    due_datetime_utc: AwareDatetime
    scheduled_start_utc: AwareDatetime
    scheduled_end_utc: AwareDatetime
    is_explicit: bool
    confidence_score: float
    evidence_snippet: str


class TaskConfirmed(BaseAgentEvent):
    task_id: str
    confirmed_at_utc: AwareDatetime


class TaskRescheduled(BaseAgentEvent):
    task_id: str
    revision_number: int


class OpportunityDiscovered(BaseAgentEvent):
    opportunity_id: str
    company: str
    title: str
    closing_date_utc: AwareDatetime | None
    canonical_url: HttpUrl


class DeadlineConflictDetected(BaseAgentEvent):
    conflict_id: str
    task_id_a: str
    task_id_b: str
    overlap_start_utc: AwareDatetime
    overlap_end_utc: AwareDatetime
    conflict_severity: EventPriority
    description: str
