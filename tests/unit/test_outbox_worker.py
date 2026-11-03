import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import AgentEvent, AuditEvent, DeadLetterEvent, Task, now
from app.orchestration.contracts import BaseAgentEvent, TaskConfirmed
from app.orchestration.outbox import drain, emit, subscribe, track_run
from conftest import make_task


class PingOk(BaseAgentEvent):
    note: str = ""


class PingFail(BaseAgentEvent):
    note: str = ""


handled = []
subscribe("PingOk", agent="Test")(lambda s, run, ev: handled.append(ev.id))


@subscribe("PingFail", agent="Flaky")
def _always_fails(session, run, event):
    raise RuntimeError("subscriber exploded")


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_atomic_commit(db, user):
    with pytest.raises(RuntimeError):
        with track_run(db, "Test", "test") as run:
            task = make_task(db, user)
            emit(db, run, TaskConfirmed, user.id, task.id, task_id=task.id, confirmed_at_utc=now())
            raise RuntimeError("crash before commit")
    db.rollback()
    assert count(db, Task) == 0 and count(db, AgentEvent) == 0

    with track_run(db, "Test", "test") as run:
        task = make_task(db, user)
        emit(db, run, TaskConfirmed, user.id, task.id, task_id=task.id, confirmed_at_utc=now())
    db.commit()
    assert count(db, Task) == 1 and count(db, AgentEvent) == 1


def test_outbox_drain(db, user):
    with track_run(db, "Test", "test") as run:
        row = emit(db, run, PingOk, user.id, "drain")
    db.commit()
    assert drain(SessionLocal) == 1
    db.expire_all()
    assert db.get(AgentEvent, row.id).status == "processed"
    assert row.id in handled


def test_depth_budget_cutoff(db, user):
    with track_run(db, "Test", "test") as run:
        parent = emit(db, run, PingOk, user.id, "root")
        parent.causation_depth = 3
        assert emit(db, run, PingOk, user.id, "child", parent=parent) is None  # depth 4 never staged
        rogue = AgentEvent(run_id=run.id, event_type="PingOk", payload_json={"user_id": user.id},
                           causation_depth=4, idempotency_key="rogue")
        db.add(rogue)
    db.commit()
    drain(SessionLocal)
    db.expire_all()
    assert db.get(AgentEvent, rogue.id).status == "failed"
    assert rogue.id not in handled
    assert count(db, AuditEvent) == 2  # one from emit, one from the dispatcher


def test_dead_letter_escalation(db, user):
    with track_run(db, "Test", "test") as run:
        row = emit(db, run, PingFail, user.id, "boom")
    db.commit()
    for _ in range(5):
        drain(SessionLocal)
    db.expire_all()
    event = db.get(AgentEvent, row.id)
    assert (event.status, event.retry_count) == ("failed", 3)
    [dead] = db.scalars(select(DeadLetterEvent)).all()
    assert dead.failed_agent == "Flaky" and "subscriber exploded" in dead.error_message


def test_idempotent_event_deduplication(db, user):
    with track_run(db, "Test", "test") as run:
        assert emit(db, run, PingOk, user.id, "same") is not None
        assert emit(db, run, PingOk, user.id, "same") is None
        db.add(AgentEvent(run_id=run.id, event_type="PingOk", payload_json={}, idempotency_key="k"))
        db.add(AgentEvent(run_id=run.id, event_type="PingOk", payload_json={}, idempotency_key="k"))
        with pytest.raises(IntegrityError):
            db.flush()
