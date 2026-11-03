"""Persistent transactional outbox (ADR-004) plus run telemetry.

Agents stage events with emit() inside their own transaction; drain() delivers them to subscribers.
Each event is handled in one transaction: all subscribers succeed and it is marked processed, or
everything rolls back and retry_count goes up. Handlers must therefore be idempotent.
"""
import hashlib
import logging
import time
import traceback
from collections import defaultdict
from contextlib import contextmanager

from sqlalchemy import select

from app.models import AgentEvent, AgentRun, AuditEvent, DeadLetterEvent, now, uid
from app.orchestration.contracts import MAX_EVENT_DEPTH

log = logging.getLogger(__name__)
MAX_RETRIES = 3
_subscribers = defaultdict(list)  # event_type -> [(agent_name, handler)]


def subscribe(event_type: str, agent: str):
    def register(handler):
        _subscribers[event_type].append((agent, handler))
        return handler
    return register


@contextmanager
def track_run(session, agent_name: str, trigger: str):
    """Record one agent execution in agent_runs (latency + outcome)."""
    run = AgentRun(agent_name=agent_name, trigger_source=trigger, status="started")
    session.add(run)
    session.flush()
    started = time.perf_counter()
    try:
        yield run
        run.status = "success"
    except Exception:
        run.status = "failed"
        raise
    finally:
        run.duration_ms = int((time.perf_counter() - started) * 1000)


def idempotency_key(user_id: str, event_type: str, natural_key: str) -> str:
    return hashlib.sha256(f"{user_id}|{event_type}|{natural_key}".encode()).hexdigest()


def emit(session, run, event_cls, user_id: str, natural_key: str, parent: AgentEvent | None = None, **fields):
    """Stage an event in the caller's transaction.

    Returns None when the depth budget is spent (logged to audit_events) or the same event was already emitted.
    """
    event_type = event_cls.__name__
    depth = parent.causation_depth + 1 if parent else 0
    if depth > MAX_EVENT_DEPTH:
        session.add(AuditEvent(user_id=user_id, action="event_depth_exceeded",
                               details_json={"event_type": event_type, "parent_event_id": parent.id, "depth": depth}))
        return None
    key = idempotency_key(user_id, event_type, natural_key)
    if session.scalar(select(AgentEvent.id).where(AgentEvent.idempotency_key == key)):
        return None
    event = event_cls(event_id=uid(), emitted_at_utc=now(), origin_agent=run.agent_name, user_id=user_id,
                      idempotency_key=key, causation_depth=depth,
                      parent_event_id=parent.id if parent else None, **fields)
    row = AgentEvent(id=event.event_id, run_id=run.id, event_type=event_type,
                     payload_json=event.model_dump(mode="json"), causation_depth=depth,
                     parent_event_id=event.parent_event_id, idempotency_key=key)
    session.add(row)
    return row


def drain(session_factory, limit: int = 20) -> int:
    """Deliver up to `limit` pending events, oldest first. Returns how many were attempted."""
    with session_factory() as session:
        ids = session.scalars(select(AgentEvent.id).where(AgentEvent.status == "pending")
                              .order_by(AgentEvent.created_at).limit(limit)).all()
    for event_id in ids:
        _dispatch(session_factory, event_id)
    return len(ids)


def _dispatch(session_factory, event_id: str) -> None:
    with session_factory() as session:
        event = session.get(AgentEvent, event_id)
        if event.causation_depth > MAX_EVENT_DEPTH:
            event.status = "failed"
            session.add(AuditEvent(user_id=event.payload_json["user_id"], action="event_depth_exceeded",
                                   details_json={"event_id": event.id, "depth": event.causation_depth}))
            session.commit()
            return

        agent = "unknown"
        try:
            for agent, handler in _subscribers[event.event_type]:
                with track_run(session, agent, f"event:{event.event_type}") as run:
                    handler(session, run, event)
            event.status = "processed"
            session.commit()
        except Exception as exc:
            session.rollback()
            log.warning("handler %s failed on %s: %s", agent, event_id, exc)
            event = session.get(AgentEvent, event_id)
            event.retry_count += 1
            session.add(AgentRun(agent_name=agent, trigger_source=f"event:{event.event_type}", status="failed"))
            if event.retry_count >= MAX_RETRIES:
                event.status = "failed"
                session.add(DeadLetterEvent(event_id=event.id, failed_agent=agent, error_message=str(exc),
                                            stack_trace=traceback.format_exc(), retry_count=event.retry_count))
            session.commit()
