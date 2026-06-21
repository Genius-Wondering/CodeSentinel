"""
CodeSentinel Agent Graph — Advanced RAG Pipeline (v0.4.0)
=========================================================
LangGraph StateGraph implementing the full 5-stage Advanced RAG flow:

  Stage 1 — Pre-Retrieval  : plan_node
      Query rewriting, multi-angle decomposition, HyDE passage generation

  Stage 2 — Retrieval      : retrieve_node
      Multi-hop retrieval across all sub-queries (expanded pool FETCH_K=20)

  Stage 3 — Re-ranking     : rerank_node
      Cross-encoder or LLM reranker cuts FETCH_K → RERANKER_TOP_K

  Stage 4 — Context Fusion : (inside review_node via ContextBuilder)
      Ranked chunks formatted with rich headers and clear boundaries

  Stage 5 — Feedback Loop  : review_node + conditional_edge
      Reviewer outputs confidence + follow-up queries.
      If confidence < high AND iteration < MAX_ITERATIONS:
          → loop back to retrieve with follow-up queries
      Else:
          → END

Graph topology:
    plan → retrieve → rerank → review
                ↑                  |  (confidence < high && iter < max)
                └──────────────────┘
                        ↓
                       END

source_type in state controls which index is searched:
  "code"   → code chunks only
  "doc"    → document chunks only
  None/""  → cross-search (default)
"""
import logging
from typing import List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.agent.context_builder import build_context
from app.agent.planner import PlannerAgent
from app.agent.reranker import rerank
from app.agent.retriever import RetrieverAgent
from app.agent.reviewer import ReviewAgent
from app.config import config

logger = logging.getLogger(__name__)


# ── State schema ──────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # Input
    query: str
    source_type: Optional[str]          # "code" | "doc" | None

    # Planning outputs
    plan: dict
    sub_queries: List[str]              # includes rewritten_query + HyDE

    # Retrieval / reranking
    retrieved_context: str              # formatted string for reviewer
    retrieved_chunks_text: List[str]    # raw page_content per ranked chunk (for RAGAS, etc.)
    reranked_chunk_count: int           # chunks surviving rerank (for QueryLog)

    # Review outputs
    answer: str
    confidence: str                     # "high" | "medium" | "low"
    followup_queries: List[str]         # queries for next iteration

    # Loop control
    iteration: int                      # current feedback loop iteration


EMPTY_INDEX_MESSAGE = (
    "**Answer:**\n"
    "No content was retrieved from the index. Please index a repository or "
    "upload documents first, or try rephrasing your question.\n\n"
    "**Relevant locations:**\nNone\n\n"
    "**Confidence:** low\n\n"
    "**Follow-up queries:**\n[]"
)

# ── Node implementations ──────────────────────────────────────────────────────

def plan_node(state: AgentState) -> AgentState:
    """
    Stage 1: Pre-Retrieval Optimization
    Rewrites query, decomposes into multi-angle sub-queries, generates HyDE passage.
    """
    result = PlannerAgent().plan(state["query"])
    sub_queries = result.get("sub_queries") or [state["query"]]
    if isinstance(sub_queries, str):
        sub_queries = [sub_queries]

    logger.debug(
        "plan_node: intent=%s sub_queries=%d",
        result.get("intent"), len(sub_queries),
    )
    return {
        **state,
        "plan": result,
        "sub_queries": sub_queries,
        "iteration": state.get("iteration", 0),
    }


def retrieve_node(state: AgentState) -> AgentState:
    """
    Stage 2: Multi-Hop Retrieval
    Issues all sub-queries (incl. HyDE + rewritten query) and merges with dedup.
    On feedback iterations, appends follow-up queries from the previous review.

    The Planner's classified intent (state["plan"]["intent"]) is passed through
    to RetrieverAgent so BM25/vector weighting and fetch-pool size adapt to the
    kind of question being asked — see app/agent/intent_routing.py.
    """
    source_type = state.get("source_type") or None
    intent = (state.get("plan") or {}).get("intent")

    # On the first pass use sub_queries from planner.
    # On subsequent passes, add follow-up queries from the reviewer.
    queries = list(state.get("sub_queries", [state["query"]]))
    for q in state.get("followup_queries", []):
        if q and q not in queries:
            queries.append(q)

    docs = RetrieverAgent().retrieve_multi(queries, source_type=source_type, intent=intent)

    logger.debug(
        "retrieve_node: iter=%d  intent=%s  queries=%d  raw_docs=%d",
        state.get("iteration", 0), intent, len(queries), len(docs),
    )

    return {**state, "_raw_docs": docs}  # pass raw docs to rerank_node


def rerank_node(state: AgentState) -> AgentState:
    """
    Stage 3: Re-ranking
    Applies cross-encoder or LLM reranker to cut the retrieval pool down to
    RERANKER_TOP_K, then formats the ranked docs into the context string.
    """
    raw_docs = state.get("_raw_docs", [])

    # Use rewritten_query for reranking if available (more precise than original)
    rerank_query = (
        state["plan"].get("rewritten_query") or state["query"]
        if state.get("plan") else state["query"]
    )

    ranked_docs = rerank(rerank_query, raw_docs, top_k=config.RERANKER_TOP_K)

    logger.debug(
        "rerank_node: iter=%d  raw=%d → ranked=%d",
        state.get("iteration", 0), len(raw_docs), len(ranked_docs),
    )

    # Stage 4: Context Fusion — build structured context string
    context = build_context(ranked_docs)
    chunks_text = [d.page_content for d in ranked_docs]

    return {
        **state,
        "retrieved_context": context,
        "retrieved_chunks_text": chunks_text,
        "reranked_chunk_count": len(ranked_docs),
    }


def review_node(state: AgentState) -> AgentState:
    """
    Stage 4+5: Context Fusion + Feedback Loop
    Generates answer; parses confidence and follow-up queries for the loop.
    """
    if not state.get("retrieved_context", "").strip() or \
       state.get("retrieved_context") == "(no context retrieved)":
        return {
            **state,
            "answer":          EMPTY_INDEX_MESSAGE,
            "confidence":      "low",
            "followup_queries": [],
        }

    answer, confidence, followup_queries = ReviewAgent().review(
        context=state["retrieved_context"],
        query=state["query"],
    )

    logger.debug(
        "review_node: iter=%d  confidence=%s  followups=%d",
        state.get("iteration", 0), confidence, len(followup_queries),
    )

    return {
        **state,
        "answer":           answer,
        "confidence":       confidence,
        "followup_queries": followup_queries,
        "iteration":        state.get("iteration", 0) + 1,
    }


# ── Routing logic for feedback loop ──────────────────────────────────────────

def _should_loop(state: AgentState) -> str:
    """
    Stage 5: Feedback Loop router.

    Loop back for another retrieval round if:
    - Confidence is NOT high (answer may be incomplete)
    - There are follow-up queries to pursue
    - We haven't exceeded MAX_ITERATIONS

    This prevents infinite loops while allowing the agent to self-correct
    when the first retrieval pass returns insufficient context.
    """
    iteration  = state.get("iteration", 1)
    confidence = state.get("confidence", "low")
    followups  = state.get("followup_queries", [])

    should_iterate = (
        confidence != "high"
        and bool(followups)
        and iteration < config.MAX_RAG_ITERATIONS
    )

    logger.debug(
        "_should_loop: iter=%d conf=%s followups=%d → %s",
        iteration, confidence, len(followups),
        "loop" if should_iterate else "end",
    )

    return "retrieve" if should_iterate else "end"


# ── Graph construction ────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(AgentState)

    g.add_node("plan",     plan_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("rerank",   rerank_node)
    g.add_node("review",   review_node)

    g.set_entry_point("plan")
    g.add_edge("plan",     "retrieve")
    g.add_edge("retrieve", "rerank")
    g.add_edge("rerank",   "review")

    # Feedback loop: review → retrieve (if low/medium confidence + followups)
    #                review → END       (if high confidence or max iterations reached)
    g.add_conditional_edges(
        "review",
        _should_loop,
        {"retrieve": "retrieve", "end": END},
    )

    return g.compile()


_graph = build_graph()


def run_agent(query: str, source_type: Optional[str] = None) -> dict:
    initial_state: AgentState = {
        "query":           query,
        "source_type":     source_type,
        "plan":            {},
        "sub_queries":     [],
        "retrieved_context": "",
        "retrieved_chunks_text": [],
        "reranked_chunk_count": 0,
        "answer":          "",
        "confidence":      "low",
        "followup_queries": [],
        "iteration":       0,
    }
    return _graph.invoke(initial_state)
