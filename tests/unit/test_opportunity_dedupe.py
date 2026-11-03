from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.agents.internship_intel import dedup_hash, harvest
from app.models import AuditEvent, Opportunity, Task


class Static:
    def __init__(self, items):
        self.items = items

    def fetch(self):
        return self.items


class Broken:
    def fetch(self):
        raise httpx.ConnectTimeout("portal timed out")


def listing(**overrides):
    closes = datetime.now(timezone.utc) + timedelta(days=30)
    item = {"organization": "Google", "title": "Software Engineering Intern",
            "url": "https://careers.google.com/jobs/1", "cycle_season": "Summer 2027",
            "closing_date": closes.isoformat(), "source_portal": "rss"}
    item.update(overrides)
    return item


def test_hash_generation():
    h = dedup_hash("Google", "Software  Engineering Intern", "Summer 2027")
    assert len(h) == 64 and int(h, 16) >= 0
    assert h == dedup_hash("google", "software engineering intern", "summer 2027")


def test_multi_source_deduplication(db, user):
    harvest(db, user, [Static([listing()]),
                       Static([listing(source_portal="json", url="https://example.com/g/1")])])
    assert len(db.scalars(select(Opportunity)).all()) == 1
    assert len(db.scalars(select(Task)).all()) == 1  # one "Apply" task, not two


def test_cycle_preservation(db, user):
    harvest(db, user, [Static([listing(cycle_season="Summer 2026"), listing(cycle_season="Summer 2027")])])
    assert len(db.scalars(select(Opportunity)).all()) == 2


def test_adapter_fault_isolation(db, user):
    harvest(db, user, [Broken(), Static([listing()])])
    assert len(db.scalars(select(Opportunity)).all()) == 1
    assert db.scalar(select(AuditEvent.action)) == "adapter_failed"


def test_apply_task_and_rolling_listings(db, user):
    harvest(db, user, [Static([listing(), listing(organization="Acme", closing_date=None)])])
    [task] = db.scalars(select(Task)).all()
    assert (task.domain, task.title) == ("career", "Apply: Software Engineering Intern @ Google")
    assert len(db.scalars(select(Opportunity)).all()) == 2


def test_bad_rows_skipped(db, user):
    harvest(db, user, [Static([{"organization": "NoUrl"}, listing()])])
    assert len(db.scalars(select(Opportunity)).all()) == 1
