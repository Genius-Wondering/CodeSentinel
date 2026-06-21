# 🔍 CodeSentinel

**CodeSentinel** is an AI-powered multi-agent system that indexes a Python codebase (and optionally PDF/Markdown/Word/Excel docs alongside it) and answers natural-language questions — with exact file and line citations. Instead of grepping through an unfamiliar repo, just ask something like *"Where is JWT authentication implemented?"* and get back a cited answer such as `auth/middleware.py, lines 42–67`.

---

## ✨ Features

| Feature | Details |
|---|---|
| **4-Node Agent Pipeline + Feedback Loop** | LangGraph `StateGraph`: Plan → Retrieve → Rerank → Review, with a conditional edge that routes back to Retrieve when confidence is below "high" and follow-up queries exist (capped by `MAX_RAG_ITERATIONS`) |
| **Query Rewriting + Decomposition + HyDE** | Planner rewrites the raw question, splits it into multi-angle sub-queries, and drafts a HyDE passage to close the gap between short questions and long code/doc chunks |
| **Hybrid Retrieval (BM25 + Vector, RRF)** | Each sub-query is run through an `EnsembleRetriever` combining BM25 keyword search with Chroma vector search, fused via Reciprocal Rank Fusion — fixes pure-vector's weak recall on exact identifiers |
| **Cross-Encoder Reranking** | A second-stage reranker (CrossEncoder, with an LLM-scoring fallback) re-orders the expanded retrieval pool down to the final top-K before the answer is written |
| **Intent-Adaptive Retrieval** | The Planner's classified intent (`find_definition`, `trace_logic`, ...) sets per-query BM25/vector weighting and fetch-pool size — narrow lookups lean keyword-heavy with a tight pool, broad reasoning leans semantic with a wider pool (`app/agent/intent_routing.py`) |
| **AST-based Chunking** | tree-sitter parses Python into function/class-level semantic units (not naive line splits), with a line-block fallback for files that fail to parse |
| **Cross-Source Search** | `source_type=code/doc/None` lets a single query search code chunks, document chunks, or both at once |
| **Persistent Metadata + Query Log** | SQLAlchemy-backed store (SQLite by default, one connection string away from MySQL/Postgres) tracks document/repo versions with content-hash dedup, and logs every `/ask` call for analysis — see `/api/v1/sources` and `/api/v1/queries/recent` |
| **Cited Answers** | Reviewer outputs file path + line range (or doc/page/section) for every claim, plus a confidence label |
| **REST API** | FastAPI with `/ask`, `/index/local`, `/index/document`, `/index/upload`, `/sources`, `/queries/recent` endpoints |
| **Web UI** | Streamlit frontend with indexing sidebar, confidence/iteration badges, and an agent-plan inspector (sub-queries, rewritten query, HyDE passage) |
| **Benchmark Evaluation** | Three evaluation scripts: hand-labelled Recall@K/MRR (`tests/eval_metrics.py`), end-to-end RAGAS scoring against the published SWE-QA benchmark (`tests/swe_qa_ragas_eval.py`), and a BEIR-based generalization check of the hybrid retriever in isolation (`tests/beir_retriever_eval.py`) |

---

## 🏗 Architecture

```
User Query
    │
    ▼
┌─────────────┐   plan_node
│   Planner   │   → query rewriting + multi-angle sub-queries + HyDE passage
└──────┬──────┘
       │
       ▼
┌──────────────────┐   retrieve_node
│    Retriever     │   → hybrid (BM25 + vector / RRF) search per sub-query
│                  │     merged + deduplicated into an expanded candidate pool
└──────┬───────────┘
       │
       ▼
┌──────────────────┐   rerank_node
│    Reranker      │   → CrossEncoder (or LLM fallback) cuts the pool down
│                  │     to the final top-K, builds the structured context
└──────┬───────────┘
       │
       ▼
┌──────────────────┐   review_node
│    Reviewer      │   → synthesizes the answer with file:line citations,
│                  │     outputs confidence + follow-up queries
└──────┬───────────┘
       │
       │  confidence != "high"  AND  follow-up queries exist
       │  AND  iteration < MAX_RAG_ITERATIONS
       ▼
   loop back to Retriever ───┐
       │                     │
       │  otherwise          │
       ▼                     │
  Final Answer  ◄────────────┘
```

**Indexing pipeline:**
```
Local Path                          PDF / Markdown / TXT / DOCX / XLSX
    │                                            │
    ▼                                            ▼
RepoLoader (walks .py files)          DocChunker (structure-aware:
    │                                  headers / pages / sheets)
    ▼                                            │
CodeParser (tree-sitter AST                      │
  → functions & classes)                         │
    │                                            │
    ▼                                            │
CodeChunker (LangChain Documents,                │
  source_type="code")                            │
    │                                            │
    └──────────────────┬─────────────────────────┘
                        ▼
              ChromaDB (OpenAI embeddings)
              + in-memory BM25 corpus
```

---

## 🚀 Quick Start

### 1. Install

```bash
git clone https://github.com/your-username/codesentinel
cd codesentinel
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Set OPENAI_API_KEY in .env
```

### 3. Start the API

```bash
uvicorn app.main:app --reload
```

### 4. Start the frontend

```bash
streamlit run frontend/app.py
```

### 5. Index a repo and ask questions

**Via Streamlit UI:** open `http://localhost:8501`, paste a local repo path in the sidebar, then ask questions.

**Via API:**

```bash
# Index a local repo
curl -X POST http://localhost:8000/api/v1/index/local \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/your/project"}'

# Ask a question
curl -X POST http://localhost:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "Where is dependency injection implemented?"}'
```

---

## 🗂 Project Structure

```
codesentinel/
├── app/
│   ├── agent/
│   │   ├── graph.py             # LangGraph StateGraph (plan → retrieve → rerank → review, + feedback loop)
│   │   ├── planner.py           # Query rewriting + decomposition + HyDE
│   │   ├── intent_routing.py    # Maps classified intent → BM25/vector weights + fetch-pool size
│   │   ├── retriever.py         # Multi-query hybrid retrieval (merge + dedup)
│   │   ├── reranker.py          # CrossEncoder / LLM re-ranking
│   │   ├── context_builder.py   # Formats ranked chunks into the review prompt's context
│   │   └── reviewer.py          # Answer synthesis + confidence + follow-up queries
│   ├── rag/
│   │   ├── parser.py            # tree-sitter AST parser (+ line-block fallback)
│   │   ├── chunker.py           # AST units → LangChain Documents (code)
│   │   ├── doc_chunker.py       # Structure-aware chunking for PDF/MD/TXT/DOCX/XLSX
│   │   ├── embedding.py         # OpenAI embeddings
│   │   └── vectordb.py          # ChromaDB + BM25 hybrid search wrapper
│   ├── services/
│   │   ├── repo_loader.py       # Walk local repo → Documents
│   │   ├── indexing.py          # Shared indexing helpers for the API routes
│   │   └── metadata.py          # File hashing, dedup lookup, version numbering, query logging
│   ├── api/
│   │   └── routes.py            # FastAPI endpoints
│   ├── prompts/
│   │   ├── planner.txt          # Prompt template for PlannerAgent
│   │   └── reviewer.txt         # Prompt template for ReviewAgent
│   ├── config.py                # Centralised config from .env
│   ├── db.py                    # SQLAlchemy engine/session (DATABASE_URL-driven)
│   ├── db_models.py             # IndexedSource + QueryLog ORM models
│   └── main.py                  # FastAPI app entry point
├── frontend/
│   └── app.py                   # Streamlit UI
├── tests/
│   ├── eval_metrics.py          # Recall@K / MRR / latency / throughput (hand-labelled)
│   ├── eval_labels.json         # Labelled queries for retrieval-quality metrics
│   ├── swe_qa_ragas_eval.py     # End-to-end answer quality vs published SWE-QA benchmark
│   ├── beir_retriever_eval.py   # Hybrid retriever generalization vs published BEIR benchmark
│   └── data/swe_qa/             # Bundled SWE-QA project subsets (Apache-2.0, see its README)
├── .env.example
├── requirements.txt
├── requirements-eval.txt        # ragas + beir, only needed to run the eval scripts above
└── docker-compose.yml
```

---

## 🛠 Tech Stack

- **Python 3.11+**
- **LangGraph** — agent orchestration with a stateful, conditionally-looping graph
- **LangChain** — LLM abstraction, document model, BM25 + Chroma ensemble retriever
- **ChromaDB** — local vector store
- **rank-bm25** — keyword retrieval leg of the hybrid search
- **tree-sitter** — AST parsing for semantic code chunking
- **sentence-transformers** *(optional)* — CrossEncoder reranking; falls back to an LLM-scored reranker if absent
- **SQLAlchemy** — persistent metadata store (document/repo versions, query logs); SQLite by default
- **FastAPI** — REST API
- **Streamlit** — web UI
- **OpenAI API** — embeddings + chat completions

---

## 📊 Evaluation

Three complementary scripts, each answering a different question:

| Script | Question it answers | Ground truth |
|---|---|---|
| `tests/eval_metrics.py` | Did the retriever find the *right chunk*? (Recall@K, MRR) + indexing throughput/latency | Hand-labelled `tests/eval_labels.json` |
| `tests/swe_qa_ragas_eval.py` | Is the final *answer* any good? (faithfulness, answer relevancy, context precision/recall) | Published [SWE-QA](tests/data/swe_qa/README.md) benchmark (real repos, human reference answers) |
| `tests/beir_retriever_eval.py` | Does the hybrid retriever *generalize* outside the code domain? (nDCG@10, Recall@100, MAP) | Published [BEIR](https://github.com/beir-cellar/beir) benchmark |

```bash
pip install -r requirements-eval.txt

# Retrieval-only, code domain, hand-labelled
python tests/eval_metrics.py --all --repo-path /path/to/repo

# Full agent, code domain, published benchmark (needs: uvicorn app.main:app --reload)
python tests/swe_qa_ragas_eval.py --repo flask

# Retriever only, non-code domain, published benchmark
python tests/beir_retriever_eval.py --dataset scifact
```

See `METRICS.md` for the full metric definitions and how to read the results.

---

## 🔭 Roadmap

- [ ] PR diff review: analyze only changed lines in a pull request
- [ ] Dependency graph visualization (import relationships)
- [ ] Multi-language support (JS/TS, Java via tree-sitter grammars)
- [ ] Streaming responses in the UI
- [ ] Multi-turn conversation memory (currently every `/ask` is a stateless single turn)
- [ ] Multi-repo indexing without a full reset (`index/local` currently wipes the whole store each time)

---

## 📄 License

MIT