from datetime import timezone

from sqlalchemy import DateTime, TypeDecorator, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """SQLite drops tzinfo, so store naive UTC and hand back aware UTC. Naive input is a bug: reject it."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: pass timezone-aware values")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)


SessionLocal = sessionmaker(expire_on_commit=False)
engine = None


def init_engine(url: str):
    global engine
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # audit issue 3: every connection, not just the first
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    SessionLocal.configure(bind=engine)
    from app import models  # noqa: F401  registers tables on Base

    # shortcut: create_all instead of Alembic migrations; add Alembic once the schema has to change under real data
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(chunk_text, chunk_id UNINDEXED)"
        ))
    return engine


def get_db():
    with SessionLocal() as session:
        yield session
