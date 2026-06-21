"""
Persistent metadata store — SQLAlchemy engine + session factory.
=================================================================
This is deliberately NOT the vector store. ChromaDB already owns embeddings
and chunk content. This database owns the things that were previously kept
in plain Python dicts/lists in memory and lost on every restart:

  - IndexedSource : one row per indexed repo/document (file hash for dedup,
                    version, chunk count, timestamps) — see TECH_SPEC.md
                    "已知局限": "文档版本 metadata 内存存储，重启丢失"
  - QueryLog      : one row per /ask call (query, rewritten query, intent,
                    confidence, iteration count, latency) — feeds the
                    evaluation harness (tests/eval_metrics.py) with real
                    production queries instead of only hand-written labels

Backend is controlled by a single connection string (config.DATABASE_URL).
SQLite is the default for local dev/demo — zero setup, a single file on
disk. Pointing DATABASE_URL at a MySQL/Postgres DSN is the only change
needed to move to a "real" relational database; the ORM models and all
calling code stay identical.
"""
import logging
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import config

logger = logging.getLogger(__name__)

Base = declarative_base()

# SQLite needs this flag for multi-threaded FastAPI request handling.
# It's a no-op for MySQL/Postgres connection strings.
_connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(config.DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Create all tables if they don't exist yet. Safe to call on every startup."""
    # Import models so they're registered on Base.metadata before create_all().
    from app import db_models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    logger.info("Persistent metadata store ready at %s", config.DATABASE_URL)


@contextmanager
def get_session():
    """
    Usage:
        with get_session() as session:
            session.add(obj)
            session.commit()

    Any failure rolls back rather than leaving a half-written row; metadata
    persistence is a "nice to have" for this project, so callers should
    swallow exceptions from this context rather than let a logging failure
    take down the actual RAG request.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
