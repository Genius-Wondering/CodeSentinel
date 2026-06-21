"""
Intent-Adaptive Retrieval Routing
==================================
PlannerAgent (app/agent/planner.py) already classifies every query into one
of VALID_INTENTS. Until now that label was only ever shown in the Streamlit
debug panel — it didn't change anything about how retrieval ran. This module
is what makes the classification actually do something.

Two knobs are adjusted per intent:

1. BM25 vs vector weight
   "Narrow lookup" intents (find_definition, find_config) are usually
   exact-identifier searches — a variable name, a config key, a class name.
   BM25 (keyword match) is the stronger signal there, so it gets more weight.
   "Broad reasoning" intents (trace_logic, compare_implementations,
   summarize_module) need semantic understanding across possibly-unrelated
   wording, so vector similarity gets more weight.

2. Fetch-pool size multiplier
   Broad reasoning intents benefit from a wider candidate pool before
   reranking (more chunks for the reranker to choose from); narrow lookup
   intents don't — a bigger pool just adds noise and reranker latency for
   a question that has one right answer.

This is a deliberately simple, explainable lookup table rather than a
learned router — it's easy to reason about, easy to tune by hand after
looking at tests/eval_metrics.py results, and easy to explain in an
interview ("why these weights"): each row says directly which intents are
exact-match-shaped vs which are recall-shaped.
"""
from typing import NamedTuple

from app.config import config


class RetrievalParams(NamedTuple):
    bm25_weight: float
    vector_weight: float
    fetch_k_multiplier: float


# Falls back to this when intent is missing/unrecognized, or when
# INTENT_ROUTING_ENABLED=false — identical to the global config defaults,
# so disabling intent routing reproduces the pre-existing fixed-weight behavior.
_DEFAULT = RetrievalParams(
    bm25_weight=config.BM25_WEIGHT,
    vector_weight=config.VECTOR_WEIGHT,
    fetch_k_multiplier=1.0,
)

# (bm25_weight, vector_weight) must sum to 1.0, matching the config.py convention.
_INTENT_TABLE = {
    # Narrow / exact-match lookups → BM25-heavy, default-sized pool
    "find_definition": RetrievalParams(0.65, 0.35, 1.0),
    "find_config":     RetrievalParams(0.70, 0.30, 1.0),

    # Mixed intents → mild vector lean, slightly wider pool
    "explain_usage":  RetrievalParams(0.40, 0.60, 1.2),
    "identify_bug":   RetrievalParams(0.45, 0.55, 1.3),

    # Broad / cross-file reasoning → vector-heavy, widest pool
    "trace_logic":             RetrievalParams(0.30, 0.70, 1.5),
    "compare_implementations": RetrievalParams(0.30, 0.70, 1.5),
    "summarize_module":        RetrievalParams(0.35, 0.65, 1.5),
}


def get_retrieval_params(intent: str) -> RetrievalParams:
    """Look up retrieval parameters for a classified intent."""
    if not config.INTENT_ROUTING_ENABLED:
        return _DEFAULT
    return _INTENT_TABLE.get(intent, _DEFAULT)
