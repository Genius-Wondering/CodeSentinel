"""
ReviewAgent — Context Fusion + Follow-up Extraction (Advanced RAG Stage 4 & 5)
===============================================================================
Upgrades over basic RAG:

1. Context Fusion (Stage 4)
   Rather than concatenating chunks verbatim, the reviewer receives:
   - Ranked context: chunks are presented in reranked order (most relevant first)
   - Source-tagged headers: each chunk is labelled with file/doc + position
   - Section separators: clear visual boundaries between chunks prevent the LLM
     from treating adjacent chunks as a single continuous passage

2. Follow-up Query Extraction (Stage 5 — feedback loop support)
   The reviewer prompt asks the LLM to output "follow-up queries" that would
   resolve any remaining uncertainty.  These are parsed out of the response and
   stored in the agent state so the graph's feedback loop can trigger another
   retrieval round if confidence is low or medium.

3. Confidence Parsing
   The confidence level (high / medium / low) is extracted from the structured
   response and stored separately in the agent state, allowing the graph to make
   routing decisions without re-parsing the full answer text.
"""
import re
from pathlib import Path
from typing import List, Tuple

from langchain_openai import ChatOpenAI

from app.config import config

PROMPT_TEMPLATE = (
    Path(__file__).parent.parent / "prompts" / "reviewer.txt"
).read_text(encoding="utf-8")


def _build_review_prompt(query: str, context: str) -> str:
    """Avoid str.format() on code context which may contain literal braces."""
    return (
        PROMPT_TEMPLATE
        .replace("{query}", query)
        .replace("{context}", context or "(no context retrieved)")
    )


def _parse_confidence(answer_text: str) -> str:
    """Extract 'high' | 'medium' | 'low' from the structured answer."""
    m = re.search(r"\*\*Confidence:\*\*\s*(high|medium|low)", answer_text, re.IGNORECASE)
    return m.group(1).lower() if m else "low"


def _parse_followup_queries(answer_text: str) -> List[str]:
    """
    Extract follow-up queries from the structured answer section.
    Returns [] when confidence is high or when no follow-ups are listed.
    """
    # Match the section between "**Follow-up queries:**" and the next "---" or end
    section_match = re.search(
        r"\*\*Follow-up queries:\*\*\s*(.*?)(?:---|\Z)",
        answer_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return []

    raw = section_match.group(1).strip()

    # Detect explicit empty list "[]"
    if raw in ("[]", "[ ]", ""):
        return []

    # Parse line-by-line, stripping bullet markers
    queries = []
    for line in raw.splitlines():
        line = re.sub(r"^[\s\-\*\d\.]+", "", line).strip()
        if line and line != "[]":
            queries.append(line)

    return queries[:2]  # cap at 2


class ReviewAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            model=config.OPENAI_MODEL,
            temperature=0,
            openai_api_key=config.OPENAI_API_KEY,
            max_tokens=config.REVIEWER_MAX_TOKENS,
            timeout=60,
            max_retries=3,
        )

    def review(self, context: str, query: str) -> Tuple[str, str, List[str]]:
        """
        Generate an answer from context.

        Returns:
            answer          : str        — full markdown answer
            confidence      : str        — "high" | "medium" | "low"
            followup_queries: list[str]  — queries for the feedback loop
        """
        prompt = _build_review_prompt(query, context)
        response = self.llm.invoke(prompt)
        answer = response.content if hasattr(response, "content") else str(response)

        confidence = _parse_confidence(answer)
        followup_queries = _parse_followup_queries(answer)

        return answer, confidence, followup_queries
