"""
Metadata service — the only module that talks to app/db_models.py directly.

Kept deliberately separate from app/services/indexing.py so the indexing
logic doesn't need to know about SQLAlchemy sessions, and from app/api/routes.py
so the API layer doesn't need to know about hashing/versioning rules.

All public functions fail soft: a metadata-store outage should never break
the actual RAG request (the part the user is waiting on). Errors are logged
and swallowed.
"""
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.db import get_session
from app.db_models import IndexedSource, QueryLog

logger = logging.getLogger(__name__)


# ── Hashing ──────────────────────────────────────────────────────────────────

def hash_file(path: str) -> str:
    """SHA-256 of a file's bytes. Used for document content dedup."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def hash_repo_fingerprint(repo_path: str, files: Iterable[str]) -> str:
    """
    Lightweight content fingerprint for a whole repo: (relative_path, size,
    mtime) per file, sorted for determinism, hashed together.

    This is intentionally NOT a hash of every byte in every file — for a
    large repo that would mean re-reading the entire tree on every index
    call just to compute a fingerprint. (path, size, mtime) changes whenever
    a file is added, removed, or edited, which is enough to detect "this
    repo looks different since last time" for the version-history use case.
    """
    h = hashlib.sha256()
    for f in sorted(files):
        try:
            st = os.stat(f)
            rel = os.path.relpath(f, repo_path)
            h.update(f"{rel}:{st.st_size}:{int(st.st_mtime)}".encode("utf-8", "ignore"))
        except OSError:
            continue
    return h.hexdigest()


# ── IndexedSource ────────────────────────────────────────────────────────────

def find_existing_doc_by_hash(content_hash: str) -> Optional[dict]:
    """
    Look up a previously-indexed document with identical content.
    Returns {"identifier", "chunk_count", "version", "name"} or None.
    """
    try:
        with get_session() as session:
            row = (
                session.query(IndexedSource)
                .filter_by(source_type="doc", content_hash=content_hash)
                .order_by(IndexedSource.version.desc())
                .first()
            )
            if row:
                return {
                    "identifier": row.identifier,
                    "chunk_count": row.chunk_count,
                    "version": row.version,
                    "name": row.name,
                }
    except Exception:
        logger.exception("metadata lookup failed; continuing without dedup")
    return None


def record_indexed_source(
    source_type: str, name: str, identifier: str, content_hash: str, chunk_count: int
) -> int:
    """
    Insert a new version row for this (source_type, name) pair.
    Returns the assigned version number (1 if this name has never been
    indexed before, otherwise previous_version + 1).
    """
    try:
        with get_session() as session:
            prev = (
                session.query(IndexedSource)
                .filter_by(source_type=source_type, name=name)
                .order_by(IndexedSource.version.desc())
                .first()
            )
            version = (prev.version + 1) if prev else 1
            session.add(
                IndexedSource(
                    source_type=source_type,
                    name=name,
                    identifier=identifier,
                    content_hash=content_hash,
                    version=version,
                    chunk_count=chunk_count,
                    indexed_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
            return version
    except Exception:
        logger.exception("failed to persist indexed-source metadata")
        return 1


# ── QueryLog ─────────────────────────────────────────────────────────────────

def log_query(
    query: str,
    rewritten_query: Optional[str],
    intent: Optional[str],
    sub_queries_json: Optional[str],
    source_type: Optional[str],
    confidence: Optional[str],
    iterations: Optional[int],
    retrieved_candidates: Optional[int],
    latency_ms: Optional[int],
    answer_preview: Optional[str],
) -> None:
    try:
        with get_session() as session:
            session.add(
                QueryLog(
                    query=query,
                    rewritten_query=rewritten_query,
                    intent=intent,
                    sub_queries=sub_queries_json,
                    source_type=source_type or "cross",
                    confidence=confidence,
                    iterations=iterations,
                    retrieved_candidates=retrieved_candidates,
                    latency_ms=latency_ms,
                    answer_preview=(answer_preview or "")[:500],
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
    except Exception:
        logger.exception("failed to persist query log")
