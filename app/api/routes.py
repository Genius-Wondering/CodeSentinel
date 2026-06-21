import json
import os
import re
import time

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from typing import Optional

from app.agent.graph import run_agent
from app.config import config
from app.rag.doc_chunker import SUPPORTED_EXTENSIONS
from app.services.indexing import index_code_repository, index_document
from app.services.metadata import log_query
from app.rag.vectordb import get_vector_store

router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    source_type: Optional[str] = Field(
        default=None,
        description="Filter results: 'code', 'doc', or omit for cross-search",
    )

class IndexLocalRequest(BaseModel):
    repo_path: str = Field(..., min_length=1)

class IndexDocumentRequest(BaseModel):
    file_path: str = Field(..., min_length=1)


# ── Utility endpoints ─────────────────────────────────────────────────────────

@router.get("/health")
def health():
    return {"status": "ok", "service": "CodeSentinel"}


@router.get("/stats")
def stats():
    store = get_vector_store()
    total = store.count()
    # FIX: read counts from Chroma metadata filter, not _corpus.
    # _corpus is in-memory only; it's empty after process restart
    # even though Chroma has persisted data on disk.
    try:
        code_count = store.db._collection.count(where={"source_type": "code"})
        doc_count  = store.db._collection.count(where={"source_type": "doc"})
    except Exception:
        code_count = len([d for d in store._corpus if d.metadata.get("source_type") == "code"])
        doc_count  = len([d for d in store._corpus if d.metadata.get("source_type") == "doc"])
    return {
        "total_chunks": total,
        "code_chunks": code_count,
        "doc_chunks": doc_count,
        "hybrid_search_active": bool(store._corpus),
        "chroma_dir": config.CHROMA_PERSIST_DIR,
        "vector_weight": config.VECTOR_WEIGHT,
        "bm25_weight": config.BM25_WEIGHT,
        "openai_configured": bool(config.OPENAI_API_KEY),
    }


# ── Persisted metadata endpoints ─────────────────────────────────────────────

@router.get("/sources")
def list_sources(limit: int = 50):
    """
    Version history of everything ever indexed (survives process restarts).
    Each row is one (source_type, name) version — re-indexing the same repo
    path or re-uploading a changed document creates a new row with version+1.
    """
    from app.db import get_session
    from app.db_models import IndexedSource

    with get_session() as session:
        rows = (
            session.query(IndexedSource)
            .order_by(IndexedSource.indexed_at.desc())
            .limit(min(limit, 200))
            .all()
        )
        return {
            "sources": [
                {
                    "source_type": r.source_type,
                    "name": r.name,
                    "identifier": r.identifier,
                    "version": r.version,
                    "chunk_count": r.chunk_count,
                    "indexed_at": r.indexed_at.isoformat() if r.indexed_at else None,
                }
                for r in rows
            ]
        }


@router.get("/queries/recent")
def recent_queries(limit: int = 20):
    """
    Recent /ask traffic from the persistent QueryLog table. Useful for
    spotting low-confidence queries to add to tests/eval_labels.json, or
    for eyeballing whether intent-adaptive retrieval is actually changing
    behavior across different question types.
    """
    from app.db import get_session
    from app.db_models import QueryLog

    with get_session() as session:
        rows = (
            session.query(QueryLog)
            .order_by(QueryLog.created_at.desc())
            .limit(min(limit, 200))
            .all()
        )
        return {
            "queries": [
                {
                    "query": r.query,
                    "intent": r.intent,
                    "source_type": r.source_type,
                    "confidence": r.confidence,
                    "iterations": r.iterations,
                    "retrieved_candidates": r.retrieved_candidates,
                    "latency_ms": r.latency_ms,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        }


# ── Query endpoint ────────────────────────────────────────────────────────────

@router.post("/ask")
def ask(req: QueryRequest):
    """
    Query the indexed knowledge base.
    source_type='code'  → code index only
    source_type='doc'   → document index only
    source_type omitted → cross-search both
    """
    if not config.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")

    store = get_vector_store()
    if store.count() == 0:
        raise HTTPException(
            status_code=400,
            detail="Index is empty. Use /index/local, /index/document, or /index/upload first.",
        )

    if req.source_type and req.source_type not in ("code", "doc"):
        raise HTTPException(status_code=422, detail="source_type must be 'code', 'doc', or omitted")

    try:
        started = time.monotonic()
        state = run_agent(req.query.strip(), source_type=req.source_type)
        latency_ms = round((time.monotonic() - started) * 1000)

        plan = state.get("plan", {})

        # Persist a QueryLog row — see app/services/metadata.py. This never
        # raises into the request: a logging failure must not turn a
        # successful answer into a 500.
        log_query(
            query=req.query.strip(),
            rewritten_query=plan.get("rewritten_query"),
            intent=plan.get("intent"),
            sub_queries_json=json.dumps(state.get("sub_queries", [])),
            source_type=req.source_type or "cross",
            confidence=state.get("confidence", "low"),
            iterations=state.get("iteration", 1),
            retrieved_candidates=state.get("reranked_chunk_count", 0),
            latency_ms=latency_ms,
            answer_preview=state.get("answer", ""),
        )

        return {
            "answer":          state["answer"],
            "confidence":      state.get("confidence", "low"),
            "sub_queries":     state["sub_queries"],
            "rewritten_query": plan.get("rewritten_query", req.query),
            "hyde_passage":    plan.get("hyde_passage", ""),
            "intent":          plan.get("intent", ""),
            "iterations":      state.get("iteration", 1),
            "source_type":     req.source_type or "cross",
            "latency_ms":      latency_ms,
            # Raw per-chunk text behind the answer — not shown in the
            # Streamlit UI, but consumed by tests/swe_qa_ragas_eval.py as
            # the `retrieved_contexts` RAGAS needs for context_precision /
            # context_recall (those metrics need individual chunks, not the
            # single pre-formatted `retrieved_context` string with headers).
            "retrieved_chunks_text": state.get("retrieved_chunks_text", []),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent pipeline failed: {e}") from e


# ── Indexing endpoints ────────────────────────────────────────────────────────

@router.post("/index/local")
def index_local(req: IndexLocalRequest):
    """Index a local repository directory (code)."""
    if not config.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
    try:
        count, path = index_code_repository(req.repo_path, reset=True)
        return {"indexed_chunks": count, "repo_path": path, "source_type": "code"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {e}") from e


@router.post("/index/document")
def index_doc(req: IndexDocumentRequest):
    """Index a PDF or Markdown document."""
    if not config.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
    try:
        count, doc_id = index_document(req.file_path)
        return {"indexed_chunks": count, "doc_id": doc_id, "source_type": "doc"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Document indexing failed: {e}") from e

@router.post("/index/upload")
async def index_upload(file: UploadFile = File(...)):
    """
    Upload a document file and index it directly.

    Accepted formats: .pdf, .md, .txt, .docx, .xlsx, .xls
    Size limit     : MAX_UPLOAD_BYTES (default 50 MB)

    Returns: {indexed_chunks, doc_id, filename, source_type}
    """
    if not config.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")

    # Validate extension
    original_name = file.filename or "upload"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type '{ext}'. "
                f"Accepted: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            ),
        )

    # Read into memory and check size
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {mb} MB.",
        )

    # Sanitize filename: replace non-alphanumeric (except . and -) with _
    safe_name = re.sub(r"[^\w.\-]", "_", original_name)[:200]

    # Persist to upload dir so DocChunker can read it from disk
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    tmp_path = os.path.join(config.UPLOAD_DIR, safe_name)
    try:
        with open(tmp_path, "wb") as f_out:
            f_out.write(data)
        count, doc_id = index_document(tmp_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload indexing failed: {e}") from e
    finally:
        # Clean up the saved file regardless of success/failure
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {
        "indexed_chunks": count,
        "doc_id": doc_id,
        "filename": original_name,
        "source_type": "doc",
    }

