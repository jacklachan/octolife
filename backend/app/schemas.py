"""Request/response DTOs."""
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Domain = Literal["academic", "career", "personal"]


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    domain: Domain
    due_datetime_utc: AwareDatetime
    scheduled_start_utc: AwareDatetime | None = None
    estimated_duration_minutes: int = Field(60, ge=1, le=24 * 60)


class TaskUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    domain: Domain | None = None
    due_datetime_utc: AwareDatetime | None = None
    scheduled_start_utc: AwareDatetime | None = None
    estimated_duration_minutes: int | None = Field(None, ge=1, le=24 * 60)
    status: Literal["completed", "cancelled"] | None = None
    reason: str = Field("edited by user", max_length=255)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    description: str | None
    domain: str
    status: str
    due_datetime_utc: datetime
    scheduled_start_utc: datetime
    scheduled_end_utc: datetime
    is_deadline_explicit: bool
    estimated_duration_minutes: int
    revision_count: int
    evidence_source_id: str | None


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    priority: str
    title: str
    body_markdown: str
    task_id: str | None
    scheduled_for_utc: datetime
    dismissed_at: datetime | None


class OpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    organization: str
    location: str
    role_category: str
    cycle_season: str
    closing_date_utc: datetime | None
    canonical_url: str
    source_portal: str
    status: str
    applied_at_utc: datetime | None


class OpportunityStatusUpdate(BaseModel):
    status: Literal["discovered", "saved", "applied", "interviewing", "rejected", "offer"]


class RawEmail(BaseModel):
    raw: str = Field(min_length=1, max_length=2_000_000)


class DocumentUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=5_000_000)
    course_code: str = Field(min_length=1, max_length=32)
    term: str = Field(min_length=1, max_length=32)
    course_title: str | None = Field(None, max_length=255)


class AcademicQuery(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
