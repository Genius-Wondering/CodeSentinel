"""
VectorStore: ChromaDB-backed storage with pluggable retriever.

Two retrieval modes (set via config.VECTOR_WEIGHT / BM25_WEIGHT):
  - Pure vector search: similarity_search over ChromaDB (HNSW index)
  - Hybrid search: EnsembleRetriever combining ChromaDB vector search
    with BM25 keyword search, fused via Reciprocal Rank Fusion (RRF).

Why hybrid?
  Vector search alone fails on exact tokens: variable names, error codes,
  package names, and other low-frequency identifiers that the embedding
  model maps to generic "programming" regions of the vector space.
  BM25 handles these precisely. RRF merges the ranked lists without
  needing to calibrate score scales between the two retrievers.
"""
from typing import List, Optional

import chromadb
from langchain_chroma import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever
from langchain_core.documents import Document

from app.rag.embedding import get_embedding_model
from app.config import config

COLLECTION_NAME = "codebase"    # Hardcoded knowledge base name

_store: Optional["VectorStore"] = None


class VectorStore:
    def __init__(self):
        self._corpus: List[Document] = []  # kept in memory for BM25 index rebuilds
        self._build_chroma()

    def _build_chroma(self):
        self.db = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=get_embedding_model(),
            persist_directory=config.CHROMA_PERSIST_DIR,
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add_documents(self, docs: List[Document]) -> None:
        if not docs:
            return
        self._corpus.extend(docs)
        # Batch to stay under Chroma's per-call limit (~5461 items).
        batch_size = 500
        for i in range(0, len(docs), batch_size):
            self.db.add_documents(docs[i:i + batch_size])

    def reset(self) -> None:
        """Delete and recreate the collection; clear the BM25 corpus."""
        # reset 方法提供了彻底清空知识库的能力。
        # 它首先尝试删除底层的 ChromaDB 集合，随后清空内存中的 _corpus 列表，最后重新初始化 ChromaDB 连接，适用于需要完全重建索引的场景
        client = chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        self._corpus = []
        self._build_chroma()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        k: int = None,
        bm25_weight: float = None,
        vector_weight: float = None,
    ) -> List[Document]:
        """
        Hybrid search: BM25 (keyword) + vector (semantic) fused via RRF.
        Falls back to pure vector search if corpus is empty (e.g. cold start
        before any documents have been added in this process lifetime).

        bm25_weight/vector_weight default to config.BM25_WEIGHT/VECTOR_WEIGHT
        but can be overridden per-call — see app/agent/intent_routing.py,
        which derives these from the Planner's classified query intent.
        """
        k = k or config.RETRIEVER_TOP_K
        if self.count() == 0:
            return []

        if not self._corpus:
            # Corpus not in memory (e.g. process restarted after indexing).
            # Fall back to pure vector search — BM25 needs the raw documents
            # in memory and we don't persist them separately.
            return self.db.similarity_search(query, k=k)

        return self._hybrid_search(query, k, bm25_weight, vector_weight)

    def _hybrid_search(
        self, query: str, k: int, bm25_weight: float = None, vector_weight: float = None
    ) -> List[Document]:
        """
        EnsembleRetriever: BM25 + Chroma vector search fused with RRF.

        weights=[BM25_WEIGHT, VECTOR_WEIGHT] control how much each ranked
        list contributes to the final score. RRF formula:
            score(d) = Σ  weight_i / (rank_i(d) + 60)
        The constant 60 dampens the impact of very high ranks.
        """
        bm25_weight = config.BM25_WEIGHT if bm25_weight is None else bm25_weight
        vector_weight = config.VECTOR_WEIGHT if vector_weight is None else vector_weight

        bm25 = BM25Retriever.from_documents(self._corpus, k=k)
        vector = self.db.as_retriever(search_kwargs={"k": k})

        ensemble = EnsembleRetriever(
            retrievers=[bm25, vector],
            weights=[bm25_weight, vector_weight],
        )
        return ensemble.invoke(query)

    def count(self) -> int:
        try:
            return self.db._collection.count()
        except Exception:
            return 0

    def filter_search(
        self,
        query: str,
        source_type: str,  # "code" or "doc"
        k: int = None,
        bm25_weight: float = None,
        vector_weight: float = None,
    ) -> List[Document]:
        """
        Hybrid search restricted to one source type via Chroma metadata filter.
        BM25 leg is pre-filtered to the same subset for a fair comparison.
        """
        k = k or config.RETRIEVER_TOP_K
        bm25_weight = config.BM25_WEIGHT if bm25_weight is None else bm25_weight
        vector_weight = config.VECTOR_WEIGHT if vector_weight is None else vector_weight

        if self.count() == 0:
            return []

        filtered_corpus = [
            d for d in self._corpus
            if d.metadata.get("source_type") == source_type
        ]

        if not filtered_corpus:
            return self.db.similarity_search(
                query, k=k, filter={"source_type": source_type}
            )

        bm25 = BM25Retriever.from_documents(filtered_corpus, k=k)
        vector = self.db.as_retriever(
            search_kwargs={"k": k, "filter": {"source_type": source_type}}
        )
        ensemble = EnsembleRetriever(
            retrievers=[bm25, vector],
            weights=[bm25_weight, vector_weight],
        )
        return ensemble.invoke(query)


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
