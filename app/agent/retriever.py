"""
RetrieverAgent — Multi-Route Retrieval (Advanced RAG Stage 2)
=============================================================
Upgrades over basic RAG:

1. Iterative / Multi-hop Retrieval
   Each sub-query from the Planner (including the HyDE passage and the
   rewritten query) is issued independently. Results are deduplicated and
   merged before reranking.

2. Source-Adaptive Routing
   - source_type=None   → hybrid search across code + docs
   - source_type="code" → filter_search restricted to code chunks
   - source_type="doc"  → filter_search restricted to doc chunks
   The BM25 corpus is pre-filtered to match the Chroma filter, so both
   retrieval legs see the same universe of documents.

3. Intent-Adaptive Retrieval (app/agent/intent_routing.py)
   The Planner's classified intent selects BM25/vector weights and a
   fetch-pool size multiplier — narrow lookups (find_definition,
   find_config) lean on exact keyword match with a tight pool; broad
   reasoning intents (trace_logic, compare_implementations,
   summarize_module) lean on semantic similarity with a wider pool.

4. Expanded Retrieval Pool (RETRIEVER_FETCH_K >> RERANKER_TOP_K)
   The retriever deliberately fetches more candidates than the final answer
   needs (RETRIEVER_FETCH_K, default 20) so the reranker has a meaningful
   pool to re-order.  The reranker then cuts this down to RERANKER_TOP_K.

5. Deduplication
   Code chunks deduplicated on (file, start_line).
   Doc chunks deduplicated on (doc_id, page, section).
"""
from typing import List, Optional

from langchain_core.documents import Document

from app.agent.intent_routing import get_retrieval_params
from app.rag.vectordb import get_vector_store
from app.config import config


class RetrieverAgent:
    def __init__(self):
        self.vectordb = get_vector_store()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        source_type: Optional[str] = None,
        intent: Optional[str] = None,
    ) -> List[Document]:
        """
        Single-query retrieval with expanded fetch pool.
        Fetches RETRIEVER_FETCH_K (default 20), scaled by the intent's
        fetch_k_multiplier, for the reranker to re-order.
        """
        params = get_retrieval_params(intent)
        k = max(1, round(config.RETRIEVER_FETCH_K * params.fetch_k_multiplier))

        if source_type:
            return self.vectordb.filter_search(
                query, source_type=source_type, k=k,
                bm25_weight=params.bm25_weight, vector_weight=params.vector_weight,
            )
        return self.vectordb.search(
            query, k=k,
            bm25_weight=params.bm25_weight, vector_weight=params.vector_weight,
        )

    def retrieve_multi(
        self,
        sub_queries: List[str],
        source_type: Optional[str] = None,
        intent: Optional[str] = None,
    ) -> List[Document]:
        """
        Multi-hop retrieval: run every sub-query (including HyDE passage and
        rewritten query) and merge results with deduplication.

        The merge order is first-appearance: a chunk that appears in the
        results of sub-query #1 keeps its early position even if it also
        appears in sub-query #3. This preserves the signal from higher-ranked
        sub-queries while still surfacing diversity from later ones.
        """
        seen: set = set()
        results: List[Document] = []

        for q in sub_queries:
            if not q or not q.strip():
                continue
            docs = self.retrieve(q.strip(), source_type=source_type, intent=intent)
            for doc in docs:
                key = _dedup_key(doc)
                if key not in seen:
                    seen.add(key)
                    results.append(doc)

        return results


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _dedup_key(doc: Document) -> tuple:
    """
    Stable deduplication key that works for both code and doc chunks.
    Code chunks key on (file, start_line).
    Doc chunks key on (doc_id, page, section).
    """
    meta = doc.metadata
    if meta.get("source_type") == "doc":
        return ("doc", meta.get("doc_id", ""), meta.get("page", 0), meta.get("section", ""))
    return ("code", meta.get("file", ""), meta.get("start_line", 0))
