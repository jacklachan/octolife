"""Agent 1: Mail Intelligence. Read-only: it can only store what it reads and propose tasks."""
import email
import email.policy
import html
import re

from pydantic import ValidationError
from sqlalchemy import select

from app.agents.base import propose_from_extraction
from app.llm import Extraction, LLMProvider, wrap_untrusted
from app.models import AuditEvent, EmailRecord, UserPreferences, now
from app.orchestration.contracts import EmailProcessed
from app.orchestration.outbox import emit, track_run

AGENT = "MailIntelligence"


def sanitize_html(markup: str) -> str:
    markup = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", markup, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p\s*>", "\n", markup, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def ingest_email(session, user: UserPreferences, raw: str, llm: LLMProvider) -> EmailRecord | None:
    """Ingest one RFC 2822 message. Returns None if this Message-ID was already ingested."""
    msg = email.message_from_string(raw, policy=email.policy.default)
    message_id = str(msg["Message-ID"] or "").strip()
    if not message_id:
        raise ValueError("email has no Message-ID header")
    if session.scalar(select(EmailRecord.id).where(EmailRecord.user_id == user.id,
                                                   EmailRecord.provider_message_id == message_id)):
        return None

    anchor = getattr(msg["Date"], "datetime", None) or now()  # the Date header anchors relative dates
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=now().tzinfo)
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part else ""
    if part is not None and part.get_content_type() == "text/html":
        body = sanitize_html(body)

    with track_run(session, AGENT, "mail_ingest") as run:
        run.model_name = llm.name
        try:
            result = Extraction.model_validate(llm.extract_commitments(wrap_untrusted(body, "email", message_id), anchor))
        except ValidationError as exc:
            session.add(AuditEvent(user_id=user.id, action="llm_output_rejected",
                                   details_json={"message_id": message_id, "errors": exc.error_count()}))
            result = Extraction(classification="unrelated")
        run.prompt_tokens, run.completion_tokens = llm.last_usage

        record =EmailRecord(user_id=user.id, provider_message_id=message_id, sender=str(msg["From"] or ""),
                             recipient=str(msg["To"] or ""), subject=str(msg["Subject"] or ""), sent_at=anchor,
                             sanitized_body_text=body, classification_label=result.classification)
        session.add(record)
        session.flush()
        tasks = []
        if result.classification in ("academic", "career"):
            for item in result.tasks:
                task = propose_from_extraction(session, run, user.id, item, anchor, "email", record.id, body)
                if task:
                    tasks.append(task)
        record.is_actionable = bool(tasks)
        emit(session, run, EmailProcessed, user.id, natural_key=message_id, email_record_id=record.id,
             provider_message_id=message_id, classification=result.classification,
             tasks_extracted_count=len(tasks))
    session.commit()
    return record
