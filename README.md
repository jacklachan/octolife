# OctoLife

Octopus-inspired multi-agent assistant for a student's academic and career life.
Like an octopus, most of the thinking happens in the arms: four specialist agents work on their own,
and the central coordinator only steps in when two arms collide.

| Agent | Octopus part | What it does | LLM? |
|---|---|---|---|
| Mail Intelligence | arm | Reads emails, extracts dated commitments, proposes tasks | yes (sandboxed) |
| Internship Intelligence | arm | Reads job feeds, dedupes across feeds/seasons, proposes "Apply" tasks | no |
| Academic Intelligence | arm | Indexes syllabi in SQLite FTS5, answers only with verbatim citations | yes (sandboxed) |
| Deadline Guardian | reflex arc | Interval-overlap conflicts, reminders, overdue marking | **never** |
| Central Coordinator | central brain | Arbitrates cross-domain conflicts, writes the daily briefing | yes, with rule-based fallback |

Agents talk only through typed events in a database outbox (`agent_events`), drained every second.
Every event carries a `causation_depth`; anything past depth 3 is dropped and audited (no runaway loops).

```
email ──> Mail agent ──TaskProposed──> Deadline Guardian ──DeadlineConflictDetected──> Coordinator ──> notification
                                         (depth 0)                  (depth 1)
```

## Run it

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```

```bash
cp .env.example .env
.venv/Scripts/uvicorn app.main:app --app-dir backend --workers 1
```

Then open http://127.0.0.1:8000/docs. Try in order:
1. `POST /api/v1/mail/ingest` with the contents of `samples/midterm.eml` as `{"raw": "..."}`
2. `POST /api/v1/tasks` with a career task overlapping Nov 15 14:00-16:00 UTC
3. wait a second, then `GET /api/v1/notifications` for the conflict and its recommendation
4. `POST /api/v1/opportunities/harvest`, `POST /api/v1/courses/upload`, `POST /api/v1/academic/query`, `GET /api/v1/dashboard/briefing`

## Layout

```
backend/app/
  agents/          one file per agent + base.py (shared "evidence -> proposed task" step)
  orchestration/   contracts.py (event types), outbox.py (emit/drain/telemetry), scheduler.py
  llm.py           provider interface, untrusted-content sandbox, output schema, offline mock
  models.py        SQLAlchemy models (ARCHITECTURE.md 6.3)
  api.py           REST endpoints (ARCHITECTURE.md 9.1)
tests/unit, tests/integration
samples/           an email, a job feed, a syllabus
```

## Not built yet (checklist IDs)

- Real LLM provider: only `LLM_PROVIDER=mock` (keyword + regex) is wired. Add a class implementing `LLMProvider` in `llm.py`.
- Gmail OAuth + sync (TSK-026..028), AES token encryption (TSK-051): emails come in through `/mail/ingest` for now.
- Alembic migrations (TSK-009): tables are created with `create_all`.
- Auth (single local user), log secret-masking filter (TSK-005), LLM budget cap (TSK-053).
- PDF upload (Markdown/plain text only), eligibility filtering of listings, RSS adapter.
- React frontend (Phase 7) and the research harness (Phase 9).
