"""
ContextBuilder — Context Fusion (Advanced RAG Stage 4)
======================================================
Converts a ranked list of Documents into a single context string that
the ReviewAgent can reason over.

Design goals:
- Clear chunk boundaries  : prevent the LLM from bleeding meaning across chunks
- Rich source attribution : file, line range, function/class name, sheet, section
- Truncation safety       : each chunk is capped at MAX_CHARS_PER_CHUNK to keep
                            total context within the model's context window
- Order signal            : chunks are numbered in reranked order (1 = most relevant)
"""
from typing import List

from langchain_core.documents import Document

from app.config import config

# Hard cap per chunk to avoid context blowout
# Total context ≈ RERANKER_TOP_K × MAX_CHARS_PER_CHUNK + overhead
MAX_CHARS_PER_CHUNK = getattr(config, "MAX_CHARS_PER_CHUNK", 1200)


def build_context(docs: List[Document]) -> str:
    """
    Format a ranked list of Documents into a structured context string.

    Each chunk is wrapped in a header block:
        ┌─ [1] app/auth.py | function: verify_token | lines 42–65 ─┐
        └─────────────────────────────────────────────────────────┘
        <code content>

    For doc chunks:
        ┌─ [2] architecture.pdf | page 3 | §"Authentication Flow" ─┐

    Returns a single string with chunks separated by "═══" dividers.
    Returns "(no context retrieved)" for empty input.
    """
    if not docs:
        return "(no context retrieved)"

    parts = []
    for rank, doc in enumerate(docs, start=1):
        meta = doc.metadata
        header = _format_header(rank, meta)
        content = doc.page_content[:MAX_CHARS_PER_CHUNK]
        if len(doc.page_content) > MAX_CHARS_PER_CHUNK:
            content += "\n… [truncated]"
        parts.append(f"{header}\n{content}")

    return "\n\n═══════════════════════════════════════\n\n".join(parts)


def _format_header(rank: int, meta: dict) -> str:
    """Build a human-readable header line for a single chunk."""
    if meta.get("source_type") == "doc":
        filename = meta.get("filename", "unknown")
        page     = meta.get("page", "?")
        section  = meta.get("section", "")
        section_str = f' | §"{section}"' if section else ""
        return f"[{rank}] 📄 {filename} | page {page}{section_str}"
    else:
        file_     = meta.get("file", "unknown")
        kind      = meta.get("kind", "block")
        name      = meta.get("name", "")
        start     = meta.get("start_line", "?")
        end       = meta.get("end_line", "?")
        name_str  = f": {name}" if name else ""
        return f"[{rank}] 💻 {file_} | {kind}{name_str} | lines {start}–{end}"
