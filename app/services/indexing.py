"""Shared indexing helpers for API routes and MCP tools."""
import logging
import os
import uuid
from typing import Tuple

from fastapi import HTTPException

from app.services.repo_loader import RepoLoader
from app.services.metadata import (
    find_existing_doc_by_hash,
    hash_file,
    hash_repo_fingerprint,
    record_indexed_source,
)
from app.rag.doc_chunker import DocChunker
from app.rag.vectordb import get_vector_store

logger = logging.getLogger(__name__)

_repo_loader = RepoLoader()
_doc_chunker = DocChunker()


def validate_path(path: str, must_be_dir: bool = False) -> str:
    abs_path = os.path.abspath(os.path.expanduser(path.strip()))
    if must_be_dir and not os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    if not must_be_dir and not os.path.isfile(abs_path):
        raise HTTPException(status_code=400, detail=f"File not found: {path}")
    return abs_path


def index_code_repository(repo_path: str, reset: bool = True) -> Tuple[int, str]:
    """Load and index a code repository. Returns (chunk_count, absolute_path)."""
    path = validate_path(repo_path, must_be_dir=True)
    docs = _repo_loader.load_repo(path)
    if not docs:
        raise HTTPException(
            status_code=400,
            detail="No Python chunks found. Check the path or .py files under the repo.",
        )
    # Stamp source_type so retriever can filter later.
    for doc in docs:
        doc.metadata["source_type"] = "code"

    store = get_vector_store()
    if reset:
        store.reset()
    store.add_documents(docs)

    # Persist a version row (path/size/mtime fingerprint, not full content
    # hash — see hash_repo_fingerprint docstring for why).
    files = [d.metadata.get("file") for d in docs if d.metadata.get("file")]
    abs_files = [os.path.join(path, f) if not os.path.isabs(f) else f for f in files]
    fingerprint = hash_repo_fingerprint(path, abs_files)
    version = record_indexed_source(
        source_type="code", name=path, identifier=path,
        content_hash=fingerprint, chunk_count=len(docs),
    )
    logger.info("Indexed code repo %s as v%d (%d chunks)", path, version, len(docs))

    return len(docs), path


def index_document(file_path: str) -> Tuple[int, str]:
    """
    Chunk and index a PDF or Markdown file.
    Returns (chunk_count, doc_id).
    Does NOT reset the store — documents accumulate alongside code chunks.

    Content-hash dedup: re-indexing a file whose bytes are byte-for-byte
    identical to a previously-indexed document is detected via
    find_existing_doc_by_hash() and short-circuited — no re-chunking, no
    re-embedding, no wasted OpenAI API calls. A file with the same name
    but different content is indexed as a new version.
    """
    path = validate_path(file_path, must_be_dir=False)
    ext = os.path.splitext(path)[1].lower()
    from app.rag.doc_chunker import SUPPORTED_EXTENSIONS
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type '{ext}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            ),
        )

    content_hash = hash_file(path)
    existing = find_existing_doc_by_hash(content_hash)
    if existing:
        logger.info(
            "Skipping re-index of %s — identical content already indexed as %s (v%d)",
            path, existing["identifier"], existing["version"],
        )
        return existing["chunk_count"], existing["identifier"]

    doc_id = str(uuid.uuid4())
    docs = _doc_chunker.chunk(path, doc_id=doc_id, version=1)
    if not docs:
        raise HTTPException(status_code=400, detail="Document produced no chunks after processing.")

    store = get_vector_store()
    store.add_documents(docs)

    name = os.path.basename(path)
    version = record_indexed_source(
        source_type="doc", name=name, identifier=doc_id,
        content_hash=content_hash, chunk_count=len(docs),
    )
    logger.info("Indexed document %s as v%d (%d chunks, doc_id=%s)", name, version, len(docs), doc_id)

    return len(docs), doc_id

