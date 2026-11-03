from sqlalchemy import select

from app.agents.academic_intel import NOT_FOUND, chunk_words, ingest_document, query
from app.llm import MockLLMProvider
from app.models import AcademicDocument, EvidenceSource, Task


def upload(db, user, name, text):
    return ingest_document(db, user, name, text, "CS410", "Fall 2026", MockLLMProvider())


def test_bm25_ranking(db, user):
    upload(db, user, "grading.md", "Grading policy: assignments and quizzes count toward the grade.")
    upload(db, user, "exam.md", "The final exam carries 40 percent weight. The final exam covers all units.")
    upload(db, user, "hours.md", "Office hours are Tuesday afternoons in room 204.")
    result = query(db, user, "grading weight final exam")
    assert result["citations"][0]["filename"] == "exam.md"
    assert result["answer"].startswith("The final exam carries 40 percent weight")


def test_unsupported_fact_rejection(db, user):
    upload(db, user, "syllabus.md", "Grading policy: assignments 30%, midterm 30%, final 40%.")
    assert query(db, user, "Who is the dean of engineering?") == {"answer": NOT_FOUND, "citations": []}


def test_fts_syntax_in_question_is_harmless(db, user):
    upload(db, user, "syllabus.md", "Late penalty is 10% per day.")
    assert query(db, user, 'late" OR penalty NEAR(')["citations"][0]["filename"] == "syllabus.md"


def test_syllabus_milestones_and_reupload(db, user):
    text = "# CS410\nMidterm exam: Nov 20, 2026, 10am - 12pm in Hall B\nGrading: final 40%"
    doc = upload(db, user, "syllabus.md", text)
    assert upload(db, user, "copy.md", text).id == doc.id  # same hash -> same document
    [task] = db.scalars(select(Task)).all()
    assert task.title.startswith("[CS410] Midterm exam") and task.domain == "academic"
    assert db.get(EvidenceSource, task.evidence_source_id).source_type == "syllabus_file"
    assert len(db.scalars(select(AcademicDocument)).all()) == 1


def test_chunk_overlap():
    chunks = chunk_words(" ".join(str(i) for i in range(600)))
    assert len(chunks) == 2
    assert chunks[1].split()[0] == "450" and chunks[1].split()[-1] == "599"
