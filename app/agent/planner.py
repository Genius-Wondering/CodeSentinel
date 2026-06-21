"""
PlannerAgent — Pre-Retrieval Optimization (Advanced RAG Stage 1)
================================================================
Upgrades over basic RAG:

1. Query Rewriting
   Resolves pronouns, expands abbreviations, and makes the implicit intent explicit.
   "why is it slow" → "performance bottleneck in the hot path of the request handler"

2. Multi-Angle Sub-Query Decomposition
   Generates 2–4 sub-queries covering different retrieval angles:
   - Semantic angle (natural language description)
   - Syntactic/keyword angle (exact symbol names)
   - Context angle (what surrounds the answer)
   - (optional) Contrast angle

3. HyDE — Hypothetical Document Embedding
   The planner writes a short "hypothetical answer passage" describing what the
   ideal retrieved chunk would look like. This passage is embedded alongside the
   sub-queries during retrieval, covering the semantic gap between a short question
   and a long technical chunk.
   Paper: "Precise Zero-Shot Dense Retrieval without Relevance Labels" (Gao et al. 2022)
"""
import json
import re
from pathlib import Path
from typing import List

from langchain_openai import ChatOpenAI

from app.config import config

PROMPT_TEMPLATE = (
    Path(__file__).parent.parent / "prompts" / "planner.txt"
).read_text(encoding="utf-8")

VALID_INTENTS = {
    "find_definition",
    "trace_logic",
    "explain_usage",
    "identify_bug",
    "summarize_module",
    "compare_implementations",
    "find_config",
}


def _parse_plan_json(text: str) -> dict:
    """Extract and parse JSON from LLM output (handles markdown fences)."""
    if not text:
        raise json.JSONDecodeError("empty response", "", 0)

    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()

    return json.loads(cleaned)


class PlannerAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            model=config.OPENAI_MODEL,
            temperature=0,
            openai_api_key=config.OPENAI_API_KEY,
            max_tokens=config.PLANNER_MAX_TOKENS,
            timeout=30,
            max_retries=3,
        )

    def plan(self, query: str) -> dict:
        """
        Returns a dict with keys:
            original_query   : str  — verbatim user input
            rewritten_query  : str  — cleaned/expanded version
            sub_queries      : list[str] — multi-angle retrieval queries
            hyde_passage     : str  — hypothetical answer for embedding
            intent           : str  — classified intent

        Falls back gracefully if LLM output is not valid JSON.
        """
        prompt = PROMPT_TEMPLATE.format(query=query)
        response = self.llm.invoke(prompt)
        try:
            content = response.content if hasattr(response, "content") else str(response)
            result = _parse_plan_json(content)
        except (json.JSONDecodeError, AttributeError, TypeError):
            return self._fallback(query)

        # Validate and sanitize each field
        result.setdefault("original_query",  query)
        result.setdefault("rewritten_query", query)
        result.setdefault("hyde_passage",    "")
        result.setdefault("intent",          "find_definition")

        # Ensure sub_queries is a non-empty list
        sqs = result.get("sub_queries") or []
        if isinstance(sqs, str):
            sqs = [sqs]
        sqs = [q for q in sqs if isinstance(q, str) and q.strip()]

        # Always include the rewritten query and HyDE passage as retrieval candidates
        hyde = result["hyde_passage"].strip()
        rewritten = result["rewritten_query"].strip()
        for extra in (rewritten, hyde):
            if extra and extra not in sqs:
                sqs.insert(0, extra)

        result["sub_queries"] = sqs if sqs else [query]

        if result["intent"] not in VALID_INTENTS:
            result["intent"] = "find_definition"

        return result

    # ------------------------------------------------------------------

    @staticmethod
    def _fallback(query: str) -> dict:
        return {
            "original_query":  query,
            "rewritten_query": query,
            "sub_queries":     [query],
            "hyde_passage":    "",
            "intent":          "find_definition",
        }
