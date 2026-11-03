"""Agent 2: Internship Intelligence. Reads whitelisted feeds; never applies or submits anything."""
import hashlib
import json
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, HttpUrl, ValidationError
from sqlalchemy import select

from app.agents.base import create_task
from app.models import AuditEvent, EvidenceSource, Opportunity, UserPreferences, now
from app.orchestration.contracts import OpportunityDiscovered
from app.orchestration.outbox import emit, track_run

AGENT = "InternshipIntelligence"


class Listing(BaseModel):
    """Canonical listing every adapter is normalised into. Rows that don't fit are skipped, not guessed."""

    organization: str
    title: str
    url: HttpUrl
    cycle_season: str
    location: str = "Unspecified"
    role_category: str = "software"
    closing_date: AwareDatetime | None = None
    source_portal: str = "unknown"


class JsonFeedAdapter:
    """Local/static JSON feed: a list of Listing-shaped objects (the MVP source)."""

    def __init__(self, path: str):
        self.path = path

    def fetch(self) -> list[dict]:
        return json.loads(Path(self.path).read_text(encoding="utf-8"))

    def __repr__(self):
        return f"JsonFeedAdapter({self.path!r})"


def dedup_hash(organization: str, title: str, cycle_season: str) -> str:
    norm = [" ".join(part.lower().split()) for part in (organization, title, cycle_season)]
    # "|" separator so ("ab", "c") and ("a", "bc") can't hash the same
    return hashlib.sha256("|".join(norm).encode()).hexdigest()


def harvest(session, user: UserPreferences, adapters) -> list[Opportunity]:
    """Pull every adapter, dedupe across feeds and seasons, propose 'Apply' tasks for open closing dates."""
    discovered = []
    with track_run(session, AGENT, "harvest") as run:
        for adapter in adapters:
            try:
                items = adapter.fetch()
            except Exception as exc:  # RSK-05: one broken portal must not stop the batch
                session.add(AuditEvent(user_id=user.id, action="adapter_failed",
                                       details_json={"adapter": repr(adapter), "error": repr(exc)}))
                continue
            for item in items:
                try:
                    listing = Listing.model_validate(item)
                except ValidationError as exc:
                    session.add(AuditEvent(user_id=user.id, action="listing_rejected",
                                           details_json={"adapter": repr(adapter), "errors": exc.error_count()}))
                    continue
                key = dedup_hash(listing.organization, listing.title, listing.cycle_season)
                existing = session.scalar(select(Opportunity).where(Opportunity.user_id == user.id,
                                                                    Opportunity.deduplication_hash == key))
                if existing:
                    existing.updated_at = now()
                    continue
                opp = Opportunity(user_id=user.id, canonical_url=str(listing.url), title=listing.title,
                                  organization=listing.organization, location=listing.location,
                                  role_category=listing.role_category, cycle_season=listing.cycle_season,
                                  closing_date_utc=listing.closing_date, source_portal=listing.source_portal,
                                  deduplication_hash=key)
                session.add(opp)
                session.flush()
                if listing.closing_date and listing.closing_date > now():
                    evidence = EvidenceSource(user_id=user.id, source_type="job_posting",
                                              source_reference_id=str(listing.url),
                                              raw_text_snippet=f"{listing.title} at {listing.organization}, "
                                                               f"closes {listing.closing_date.isoformat()}",
                                              confidence_score=1.0)
                    create_task(session, run, user.id, evidence, f"Apply: {listing.title} @ {listing.organization}",
                                "career", listing.closing_date)
                emit(session, run, OpportunityDiscovered, user.id, natural_key=key, opportunity_id=opp.id,
                     company=opp.organization, title=opp.title, closing_date_utc=listing.closing_date,
                     canonical_url=listing.url)
                discovered.append(opp)
    session.commit()
    return discovered
