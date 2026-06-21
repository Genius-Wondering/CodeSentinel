import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma")
    CHUNK_SIZE_LINES: int = int(os.getenv("CHUNK_SIZE_LINES", "50"))
    RETRIEVER_TOP_K: int = int(os.getenv("RETRIEVER_TOP_K", "5"))
    PLANNER_MAX_TOKENS: int = int(os.getenv("PLANNER_MAX_TOKENS", "2048"))
    REVIEWER_MAX_TOKENS: int = int(os.getenv("REVIEWER_MAX_TOKENS", "4096"))

    # Document chunking
    DOC_CHUNK_SIZE: int = int(os.getenv("DOC_CHUNK_SIZE", "1000"))
    DOC_CHUNK_OVERLAP: int = int(os.getenv("DOC_CHUNK_OVERLAP", "100"))

    # Hybrid search weights (must sum to 1.0)
    # Higher VECTOR_WEIGHT → semantic similarity matters more
    # Higher BM25_WEIGHT   → exact keyword match matters more
    VECTOR_WEIGHT: float = float(os.getenv("VECTOR_WEIGHT", "0.6"))
    BM25_WEIGHT: float = float(os.getenv("BM25_WEIGHT", "0.4"))

    # File upload
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./data/uploads")
    # Max upload size in bytes (default 50 MB)
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))

    # ── Advanced RAG settings ─────────────────────────────────────────────────

    # Retrieval: fetch a larger pool for the reranker to re-order
    # RETRIEVER_FETCH_K >> RERANKER_TOP_K (reranker cuts it down)
    RETRIEVER_FETCH_K: int = int(os.getenv("RETRIEVER_FETCH_K", "20"))

    # Reranker: how many chunks survive after reranking (passed to reviewer)
    RERANKER_TOP_K: int = int(os.getenv("RERANKER_TOP_K", "5"))

    # Context fusion: max characters per chunk in the review prompt
    # Total context ≈ RERANKER_TOP_K × MAX_CHARS_PER_CHUNK + prompt overhead
    MAX_CHARS_PER_CHUNK: int = int(os.getenv("MAX_CHARS_PER_CHUNK", "1200"))

    # Feedback loop: max retrieval iterations before forcing END
    # 1 = no looping (basic RAG), 2 = one follow-up iteration (recommended)
    MAX_RAG_ITERATIONS: int = int(os.getenv("MAX_RAG_ITERATIONS", "2"))

    # ── Persistent metadata store ────────────────────────────────────────────
    # SQLite by default (zero ops, good for local dev / single-process demo).
    # Swap to Postgres/MySQL in production by changing this one value, e.g.:
    #   mysql+pymysql://user:password@host:3306/codesentinel
    #   postgresql+psycopg2://user:password@host:5432/codesentinel
    # The schema (app/db_models.py) is plain SQLAlchemy, so no other code
    # needs to change when swapping backends.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data/codesentinel.db")

    # Intent-adaptive retrieval: when False, every query uses the global
    # VECTOR_WEIGHT/BM25_WEIGHT/RETRIEVER_FETCH_K regardless of intent.
    INTENT_ROUTING_ENABLED: bool = os.getenv("INTENT_ROUTING_ENABLED", "true").lower() == "true"


config = Config()
