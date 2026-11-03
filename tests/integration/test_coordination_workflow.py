"""End-to-end: the arms perceive, the guardian detects, the central brain arbitrates."""
from app.api import llm_provider
from app.llm import MockLLMProvider
from app.main import app
from conftest import drain_all

EXAM_MAIL = ("From: prof@uni.edu\nTo: student@uni.edu\nSubject: CS410 midterm\n"
             "Date: Wed, 04 Nov 2026 10:00:00 +0000\nMessage-ID: <exam-1@uni.edu>\n\n"
             "The CS410 midterm exam is on Nov 15, 2pm - 4pm in Hall B.\n")


def test_exam_vs_interview_conflict_cascade(client):
    assert client.post("/api/v1/mail/ingest", json={"raw": EXAM_MAIL}).json()["actionable"] is True
    interview = client.post("/api/v1/tasks", json={
        "title": "FinTech interview", "domain": "career",
        "scheduled_start_utc": "2026-11-15T15:00:00Z", "due_datetime_utc": "2026-11-15T16:30:00Z"}).json()
    drain_all()

    [alert] = [n for n in client.get("/api/v1/notifications").json() if n["type"] == "cross_domain_conflict"]
    assert alert["priority"] == "high"
    assert alert["task_id"] == interview["id"]  # the interview is the one to move
    assert "60 min" in alert["body_markdown"] and "favours coursework" in alert["body_markdown"]

    events = client.get("/api/v1/agents/events").json()
    conflict = next(e for e in events if e["type"] == "DeadlineConflictDetected")
    assert conflict["depth"] == 1 and conflict["parent"] is not None
    assert all(e["status"] == "processed" for e in events)

    # re-delivering the same facts must not duplicate the alert
    client.patch(f"/api/v1/tasks/{interview['id']}", json={"title": "FinTech interview (round 2)"})
    drain_all()
    assert len([n for n in client.get("/api/v1/notifications").json() if n["type"] == "cross_domain_conflict"]) == 1


def test_same_exam_from_email_and_syllabus_is_a_duplicate_not_a_conflict(client):
    client.post("/api/v1/mail/ingest", json={"raw": EXAM_MAIL})
    client.post("/api/v1/courses/upload", json={"filename": "s.md", "course_code": "CS410", "term": "Fall 2026",
                                                "text": "Midterm exam: Nov 15, 2026, 2pm - 4pm"})
    drain_all()
    [n] = client.get("/api/v1/notifications").json()
    assert (n["type"], n["priority"]) == ("possible_duplicate", "low")


def test_daily_briefing_llm_crash_fallback(client):
    class Down(MockLLMProvider):
        def synthesize_briefing(self, wrapped_context):
            raise RuntimeError("503 provider unavailable")

    client.post("/api/v1/tasks", json={"title": "DBMS assignment", "domain": "academic",
                                       "due_datetime_utc": "2099-01-01T10:00:00Z"})
    app.dependency_overrides[llm_provider] = Down
    try:
        res = client.get("/api/v1/dashboard/briefing")
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200
    assert "earliest deadline first" in res.json()["body_markdown"]


def test_task_api_revisions_and_confirm(client):
    task = client.post("/api/v1/tasks", json={"title": "OS lab", "domain": "academic",
                                              "due_datetime_utc": "2026-12-01T10:00:00+05:30"}).json()
    assert task["due_datetime_utc"].startswith("2026-12-01T04:30:00")  # stored in UTC
    moved = client.patch(f"/api/v1/tasks/{task['id']}", json={"due_datetime_utc": "2026-12-02T10:00:00Z",
                                                              "reason": "prof extended"}).json()
    assert moved["revision_count"] == 1
    assert client.post(f"/api/v1/tasks/{task['id']}/confirm").status_code == 409  # already confirmed
    assert client.post("/api/v1/tasks", json={"title": "x", "domain": "academic",
                                              "due_datetime_utc": "2026-12-01T10:00:00"}).status_code == 422  # naive
    assert len(client.get("/api/v1/tasks", params={"limit": 1}).json()) == 1
    health = client.get("/api/v1/health").json()
    assert (health["status"], health["database"]) == ("healthy", "connected")
