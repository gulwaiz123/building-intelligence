"""Shared registry: the single source of truth for course modes and model IDs."""

from enum import Enum


class Pattern(str, Enum):
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    HYBRID_RERANKED = "hybrid-reranked"
    STRUCTURED = "structured"
    DECOMPOSITION = "decomposition"
    HYDE = "hyde"


# Registry: mode -> model ID. Later stories add behavior, never entries or renames.
PATTERN_MODEL_IDS: dict[Pattern, str] = {
    Pattern.SEMANTIC: "rag-semantic",
    Pattern.HYBRID: "rag-hybrid",
    Pattern.HYBRID_RERANKED: "rag-hybrid-reranked",
    Pattern.STRUCTURED: "rag-structured",
    Pattern.DECOMPOSITION: "rag-decomposition",
    Pattern.HYDE: "rag-hyde",
}

MODEL_ID_TO_PATTERN: dict[str, Pattern] = {mid: p for p, mid in PATTERN_MODEL_IDS.items()}


def run_pattern(pattern: Pattern, question: str, caller_id: str | None) -> dict:
    """Single shared dispatch path for every mode.

    Every mode currently returns an honest not_implemented placeholder;
    later stories add real behavior here, one pattern at a time.
    """
    return {
        "pattern": pattern.value,
        "status": "not_implemented",
        "message": (
            f"Pattern '{pattern.value}' is not implemented yet; "
            "this course story adds no retrieval behavior."
        ),
        "trace": {"caller_id": caller_id, "question_length": len(question)},
        "results": [],
        "omitted_candidates": [],
        "subquestions": [],
        "hyde_direct_candidates": [],
        "hyde_query_candidates": [],
        "hyde_hypothetical_text_debug": None,
    }
