from datetime import datetime, timezone
from types import SimpleNamespace

from app.agents.central_coordinator import arbitrate

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
exam = SimpleNamespace(title="Midterm", domain="academic", created_at=T0)
interview = SimpleNamespace(title="Interview", domain="career", created_at=T0)


def test_cross_domain_prioritization():
    keep, move, _ = arbitrate(interview, exam, academic_weight=0.6)
    assert (keep, move) == (exam, interview)


def test_low_academic_weight_favours_career():
    keep, move, _ = arbitrate(exam, interview, academic_weight=0.4)
    assert (keep, move) == (interview, exam)


def test_same_domain_is_first_come_first_served():
    later = SimpleNamespace(title="Quiz", domain="academic", created_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    keep, move, _ = arbitrate(later, exam, academic_weight=0.6)
    assert (keep, move) == (exam, later)
