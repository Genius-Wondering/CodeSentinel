"""
ORM models for the persistent metadata store (app/db.py).

IndexedSource
-------------
One row per *version* of an indexed repo or document. Fixes the known
limitation that doc/repo metadata previously lived only in an in-memory
dict and disappeared on every process restart. `content_hash` enables
real dedup: re-uploading an unchanged document is detected and skipped
instead of silently re-embedding (and re-paying for) identical content.

QueryLog
--------
One row per /ask call: the rewritten query, classified intent, confidence,
iteration count and latency. This turns production traffic into a growing
evaluation corpus that complements the hand-labelled tests/eval_labels.json
used by tests/eval_metrics.py.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IndexedSource(Base):
    __tablename__ = "indexed_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(16), nullable=False)     # "code" | "doc"
    name = Column(String(512), nullable=False)            # repo path or original filename
    identifier = Column(String(64), nullable=False, index=True)  # doc_id (doc) or abs repo path (code)
    content_hash = Column(String(64), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    chunk_count = Column(Integer, nullable=False, default=0)
    indexed_at = Column(DateTime, nullable=False, default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<IndexedSource {self.source_type} name={self.name!r} "
            f"v{self.version} chunks={self.chunk_count}>"
        )


class QueryLog(Base):
    __tablename__ = "query_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    query = Column(Text, nullable=False)
    rewritten_query = Column(Text)
    intent = Column(String(64))
    sub_queries = Column(Text)            # JSON-encoded list[str]
    source_type = Column(String(16))      # "code" | "doc" | "cross"
    confidence = Column(String(16))       # "high" | "medium" | "low"
    iterations = Column(Integer)
    retrieved_candidates = Column(Integer)  # chunks surviving rerank, pre-answer
    latency_ms = Column(Integer)
    answer_preview = Column(Text)         # first ~500 chars, for quick scanning
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<QueryLog q={self.query[:40]!r} confidence={self.confidence}>"
