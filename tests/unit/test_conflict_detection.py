from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.agents.deadline_guardian import (calculate_time_remaining, detect_interval_collisions,
                                          evaluate_due_reminders, normalize_interval)
from app.models import Notification
from conftest import make_task

UTC = timezone.utc


def at(hour, minute=0):
    return datetime(2026, 11, 15, hour, minute, tzinfo=UTC)


def iv(start, end):
    return SimpleNamespace(scheduled_start_utc=start, scheduled_end_utc=end)


def test_interval_algebra_adjacent():
    assert detect_interval_collisions([iv(at(14), at(15)), iv(at(15), at(16))]) == []


def test_interval_algebra_partial_overlap():
    [(_, _, minutes)] = detect_interval_collisions([iv(at(14), at(15, 30)), iv(at(15), at(16, 30))])
    assert minutes == 30


def test_interval_algebra_enclosure():
    [(_, _, minutes)] = detect_interval_collisions([iv(at(14), at(17)), iv(at(15), at(16))])
    assert minutes == 60


def test_instantaneous_deadline_mapping():
    assert normalize_interval(at(23, 59), None, 90) == (at(22, 29), at(23, 59))


def test_leap_year_rollover():
    remaining = calculate_time_remaining(datetime(2028, 3, 1, tzinfo=UTC), datetime(2028, 2, 28, tzinfo=UTC))
    assert remaining == timedelta(days=2)  # 2028 has Feb 29


def test_daylight_saving_transitions():
    ny = ZoneInfo("America/New_York")
    # 2027-03-14 02:00 local doesn't exist; 01:00 EST -> 03:00 EDT is one real hour
    start, end = normalize_interval(datetime(2027, 3, 14, 3, tzinfo=ny), datetime(2027, 3, 14, 1, tzinfo=ny), 60)
    assert end - start == timedelta(hours=1)


def test_mixed_offsets_collide_in_utc():
    ist = ZoneInfo("Asia/Kolkata")
    exam = iv(*normalize_interval(datetime(2026, 11, 15, 20, 30, tzinfo=ist), datetime(2026, 11, 15, 19, 30, tzinfo=ist), 0))
    call = iv(at(14, 30), at(15, 30))  # 20:00-21:00 IST
    [(_, _, minutes)] = detect_interval_collisions([exam, call])
    assert minutes == 30


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        normalize_interval(datetime(2026, 11, 15, 14), None, 60)


def test_reminders_are_exactly_once_and_overdue_marked(db, user):
    now = datetime.now(UTC)
    soon = make_task(db, user, "Soon", end=now + timedelta(hours=1))
    late = make_task(db, user, "Late", end=now - timedelta(minutes=1))
    db.commit()
    assert evaluate_due_reminders(db, now) == 1  # only the 2h reminder, not a stale 48h one too
    assert evaluate_due_reminders(db, now) == 0
    assert late.status == "overdue"
    [n] = db.query(Notification).all()
    assert n.task_id == soon.id and n.priority == "high"
