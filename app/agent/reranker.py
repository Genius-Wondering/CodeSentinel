"""
Reranker — Post-Retrieval Scoring (Advanced RAG Stage 3)
=========================================================
Problem:
    Initial retrieval (BM25 + vector hybrid) optimises for recall — it casts
    a wide net and returns the Top-N most *likely* relevant chunks.  But recall
    and precision are different objectives.  A chunk that is statistically close
    in the embedding space is not always the most useful answer to the specific
    question.

Solution — Cross-Attention Re-ranking:
    Pass each (query, chunk) pair through a second model that attends to BOTH
    at once (cross-encoder architecture) and outputs a relevance score.
    This is far more accurate than embedding cosine similarity but too expensive
    to run over the entire corpus — which is why it runs only on the Top-N
    already returned by the fast retriever.

Implementation:
    sentence-transformers CrossEncoder is the standard open-source choice
    (ms-marco-MiniLM-L-6-v2).  However, because installing it requires ~500 MB
    and may not be available in all environments, this module provides TWO paths:

    Path A — CrossEncoder (preferred when sentence-transformers is installed):
        model.predict([(query, chunk_text), ...]) → float[]
        Latency: ~20–50 ms for 20 chunks on CPU.

    Path B — LLM Reranker (fallback, no extra deps):
        Ask GPT-4o-mini to score each chunk 0–10 in a single batched prompt.
        Latency: ~1–3 s but requires only the OpenAI key already in use.
        Accuracy: slightly lower than a dedicated cross-encoder but far better
        than cosine similarity alone.

    The module auto-detects which path is available and logs the choice.
"""
import logging
from typing import List, Tuple

from langchain_core.documents import Document
from langchain_openai import ChatOpenAI

from app.config import config

logger = logging.getLogger(__name__)

# ── Try to import sentence-transformers (Path A) ──────────────────────────────
try:
    from sentence_transformers import CrossEncoder as _CrossEncoder  # type: ignore
    _CROSS_ENCODER_AVAILABLE = True
except ImportError:
    _CROSS_ENCODER_AVAILABLE = False

CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_cross_encoder_instance = None  # lazy singleton


def _get_cross_encoder():
    global _cross_encoder_instance
    if _cross_encoder_instance is None:
        logger.info("Loading CrossEncoder model: %s", CROSS_ENCODER_MODEL)
        _cross_encoder_instance = _CrossEncoder(CROSS_ENCODER_MODEL, max_length=512)
    return _cross_encoder_instance


# ── LLM batch reranker prompt ─────────────────────────────────────────────────
_LLM_RERANK_PROMPT = """\
You are a relevance scoring engine for a code and document search system.

Query: {query}

Score each passage below for relevance to the query on a scale of 0–10:
  10 = directly answers the query
   7 = highly relevant, contains the answer or strong clues
   4 = somewhat relevant, tangentially related
   1 = not relevant
   0 = completely unrelated

Return ONLY a JSON array of numbers in the same order as the passages.
Example: [8, 3, 7, 1]

Passages:
{passages}
"""


def _llm_rerank(query: str, docs: List[Document]) -> List[Tuple[Document, float]]:
    """Rerank using GPT-4o-mini as a relevance scorer (no extra dependencies)."""
    if not docs:
        return []

    passages = "\n\n".join(
        f"[{i+1}] {doc.page_content[:600]}" for i, doc in enumerate(docs)
    )
    prompt = _LLM_RERANK_PROMPT.format(query=query, passages=passages)

    llm = ChatOpenAI(
        model=config.OPENAI_MODEL,
        temperature=0,
        openai_api_key=config.OPENAI_API_KEY,
        max_tokens=200,
        timeout=30,
    )
    try:
        import json, re
        response = llm.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)

        # Strip markdown fences if present
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        scores_raw = fence.group(1) if fence else text.strip()
        scores: List[float] = json.loads(scores_raw)

        if len(scores) != len(docs):
            logger.warning(
                "Reranker score count mismatch (%d scores for %d docs) — using order",
                len(scores), len(docs),
            )
            return [(doc, float(len(docs) - i)) for i, doc in enumerate(docs)]

        return list(zip(docs, [float(s) for s in scores]))

    except Exception as exc:
        logger.warning("LLM reranking failed (%s) — preserving retrieval order", exc)
        return [(doc, float(len(docs) - i)) for i, doc in enumerate(docs)]


def rerank(
    query: str,
    docs: List[Document],
    top_k: int = None,
) -> List[Document]:
    """
    Rerank `docs` by relevance to `query` and return the top_k most relevant.

    Automatically selects Path A (CrossEncoder) or Path B (LLM) based on
    what's installed.

    Args:
        query   : the user's (rewritten) query
        docs    : candidate documents from the retriever (Top-N)
        top_k   : how many to keep after reranking (default: RERANKER_TOP_K from config)

    Returns:
        Reranked list of Documents, most relevant first, length <= top_k.
    """
    if not docs:
        return []

    top_k = top_k or config.RERANKER_TOP_K

    if _CROSS_ENCODER_AVAILABLE:
        # Path A: CrossEncoder
        logger.debug("Reranking %d docs with CrossEncoder (Path A)", len(docs))
        try:
            ce = _get_cross_encoder()
            pairs = [(query, doc.page_content[:512]) for doc in docs]
            scores = ce.predict(pairs).tolist()
            scored = list(zip(docs, scores))
        except Exception as exc:
            logger.warning("CrossEncoder failed (%s) — falling back to LLM reranker", exc)
            scored = _llm_rerank(query, docs)
    else:
        # Path B: LLM reranker
        logger.debug("Reranking %d docs with LLM reranker (Path B)", len(docs))
        scored = _llm_rerank(query, docs)

    # Sort descending by score, keep top_k
    scored.sort(key=lambda x: x[1], reverse=True)
    result = [doc for doc, _ in scored[:top_k]]

    logger.debug(
        "Reranker: %d → %d docs  |  top score=%.2f",
        len(docs), len(result),
        scored[0][1] if scored else 0,
    )
    return result
