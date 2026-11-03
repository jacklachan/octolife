from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.agents  # noqa: F401  registers outbox subscribers
from app import database
from app.config import settings
from app.database import SessionLocal, init_engine
from app.main import app
from app.models import EvidenceSource, Task, ensure_user

UTC = timezone.utc


@pytest.fixture
def db(tmp_path):
    init_engine(f"sqlite:///{tmp_path / 'test.db'}")
    with SessionLocal() as session:
        yield session
    database.engine.dispose()


@pytest.fixture
def user(db):
    return ensure_user(db)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    with TestClient(app) as c:
        yield c
    database.engine.dispose()


def make_task(session, user, title="Task", domain="academic", start=None, end=None, status="confirmed"):
    end = end or datetime.now(UTC) + timedelta(days=1)
    start = start or end - timedelta(hours=1)
    task = Task(user_id=user.id, title=title, domain=domain, status=status, due_datetime_utc=end,
                scheduled_start_utc=start, scheduled_end_utc=end)
    session.add(task)
    session.flush()
    return task


def drain_all():
    from app.orchestration.outbox import drain
    while drain(SessionLocal):
        pass
