"""Agent 4: Academic Intelligence. Indexes course material in FTS5 and answers only from what it indexed."""
import hashlib
import re

from pydantic import ValidationError
from sqlalchemy import select, text

from app.agents.base import propose_from_extraction
from app.llm import Extraction, LLMProvider, wrap_untrusted
from app.models import AcademicDocument, AcademicDocumentChunk, AuditEvent, Course, UserPreferences, now
from app.orchestration.outbox import track_run

AGENT = "AcademicIntelligence"
NOT_FOUND = "Information not found in provided materials."
_STOPWORDS = set("a an and are as at be by can do does for from how i in is it me my of on or the this to was "
                 "what when where which who whom why will with you your".split())


def chunk_words(content: str, size: int = 500, overlap: int = 50) -> list[str]:
    # shortcut: whitespace words stand in for tokens; swap in a real tokenizer if chunk budgets get tight
    words = content.split()
    if not words:
        return []
    return [" ".join(words[i:i + size]) for i in range(0, max(len(words) - overlap, 1), size - overlap)]


def ingest_document(session, user: UserPreferences, filename: str, content: str, course_code: str, term: str,
                    llm: LLMProvider, course_title: str | None = None) -> AcademicDocument:
    """Markdown/plain text only. Re-uploading identical content returns the existing document."""
    digest = hashlib.sha256(content.encode()).hexdigest()
    existing = session.scalar(select(AcademicDocument).where(AcademicDocument.user_id == user.id,
                                                             AcademicDocument.file_hash_sha256 == digest))
    if existing:
        return existing
    course = session.scalar(select(Course).where(Course.user_id == user.id, Course.course_code == course_code,
                                                 Course.term == term))
    if course is None:
        course = Course(user_id=user.id, course_code=course_code, course_title=course_title or course_code, term=term)
        session.add(course)
        session.flush()

    with track_run(session, AGENT, "document_upload") as run:
        run.model_name = llm.name
        doc = AcademicDocument(user_id=user.id, course_id=course.id, filename=filename, file_hash_sha256=digest,
                               mime_type="text/markdown", parsing_status="parsed")
        session.add(doc)
        session.flush()
        chunks = []
        for i, chunk in enumerate(chunk_words(content)):
            row = AcademicDocumentChunk(document_id=doc.id, chunk_index=i, chunk_text=chunk,
                                        token_count=len(chunk.split()))
            session.add(row)
            session.flush()
            session.execute(text("INSERT INTO document_chunks_fts(chunk_text, chunk_id) VALUES (:t, :id)"),
                            {"t": chunk, "id": row.id})
            chunks.append(row)

        try:
            result = Extraction.model_validate(
                llm.extract_commitments(wrap_untrusted(content, "syllabus", filename), now()))
        except ValidationError as exc:
            session.add(AuditEvent(user_id=user.id, action="llm_output_rejected",
                                   details_json={"document": filename, "errors": exc.error_count()}))
            result = Extraction(classification="unrelated")
        for item in result.tasks:
            quote = " ".join(item.evidence_quote.split())
            source = next((c for c in chunks if quote in c.chunk_text), chunks[0] if chunks else None)
            propose_from_extraction(session, run, user.id, item, now(), "syllabus_file",
                                    source.id if source else doc.id, content, domain="academic",
                                    title_prefix=f"[{course_code}] ")
    session.commit()
    return doc


def query(session, user: UserPreferences, question: str, k: int = 3) -> dict:
    """BM25 retrieval. The answer is verbatim source text with citations, never generated prose."""
    terms = [t for t in re.findall(r"\w+", question.lower()) if t not in _STOPWORDS]
    if not terms:
        return {"answer": NOT_FOUND, "citations": []}
    match = " OR ".join(f'"{t}"' for t in terms)  # quoted terms: user text can't inject FTS5 syntax
    rows = session.execute(text(
        "SELECT c.chunk_text, c.chunk_index, d.filename FROM document_chunks_fts f "
        "JOIN academic_document_chunks c ON c.id = f.chunk_id "
        "JOIN academic_documents d ON d.id = c.document_id "
        "WHERE document_chunks_fts MATCH :q AND d.user_id = :u ORDER BY bm25(document_chunks_fts) LIMIT :k"),
        {"q": match, "u": user.id, "k": k}).all()
    if not rows:
        return {"answer": NOT_FOUND, "citations": []}
    citations = [{"filename": r.filename, "chunk_index": r.chunk_index, "text": r.chunk_text} for r in rows]
    return {"answer": rows[0].chunk_text, "citations": citations}
