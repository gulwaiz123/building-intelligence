"""One shared path for /v1/query and /v1/chat/completions.

retrieve -> answer_events (one generation/validation operation) -> final QueryResult.
"""

from building_with_rag.contracts import GenerationResult, QueryRequest, QueryResult
from building_with_rag.generation.answer import generate_events
from building_with_rag.registry import Pattern, run_pattern
from building_with_rag.retrieval.hybrid import run_hybrid
from building_with_rag.retrieval.rerank import run_hybrid_reranked
from building_with_rag.retrieval.semantic import RetrievalError, semantic_retrieve
from building_with_rag.settings import get_settings

REAL_PATTERNS = frozenset({Pattern.SEMANTIC, Pattern.HYBRID, Pattern.HYBRID_RERANKED})
LOW_CONFIDENCE_HEAD = "DRAFT — low confidence, not the final answer."
PASSED_HEAD = "Evidence check passed — confidence: high"


def retrieve(request: QueryRequest) -> QueryResult:
    """Semantic/hybrid/hybrid-reranked -> real retrieval; other modes -> placeholder. Raises RetrievalError."""
    if request.pattern not in REAL_PATTERNS:
        payload = run_pattern(request.pattern, request.question, request.caller_id)
        return QueryResult(**payload)
    mode = request.pattern.value
    caller = get_settings().webui_demo_caller_id
    if request.caller_id is not None and request.caller_id != caller:
        raise RetrievalError(422, "invalid_request", "caller_id does not match the effective caller.")
    if request.required_acts is not None:
        raise RetrievalError(422, "invalid_request", f"required_acts is not supported by {mode} mode.")
    if request.chapter is not None:
        raise RetrievalError(422, "invalid_request", f"chapter is not supported by {mode} mode.")
    if request.pattern is Pattern.HYBRID_RERANKED:
        return run_hybrid_reranked(request)
    return run_hybrid(request) if request.pattern is Pattern.HYBRID else semantic_retrieve(request)


def wants_generation(request: QueryRequest) -> bool:
    return request.generate_answer and request.pattern in REAL_PATTERNS


def answer_events(question: str, retrieval: QueryResult):
    """Yield ('text'|'notice', str) events, then ('final', GenerationResult)."""
    yield from generate_events(question, retrieval.results)


def run_generation(question: str, retrieval: QueryResult) -> GenerationResult:
    """Drain answer_events and attach the result (used by /v1/query)."""
    final = None
    for kind, payload in answer_events(question, retrieval):
        if kind == "final":
            final = payload
    retrieval.generation = final
    retrieval.message += f" Answer generation: {final.outcome}."
    if final.confidence == "low":
        retrieval.message += f" Low confidence: {final.low_confidence_reason}"
    return final


def _source_line(c) -> str:
    act = c.act.split("_")[0]
    sec = f"§{c.section_number}" if c.section_number is not None else c.section_id
    return f"{c.label} · {act} {sec} · {c.heading} · {c.section_id}"


def render_footer(result: QueryResult) -> str:
    """Readable text appended after the streamed answer, derived from the final result."""
    g = result.generation
    if g is None:
        return result.message
    if g.outcome == "answered":
        sources = "\n".join(_source_line(c) for c in g.citations)
        return f"\n\n{PASSED_HEAD}\n\nSources:\n{sources}\n"
    if g.outcome == "insufficient_evidence":
        reason = str(g.trace.get("reason") or "").strip()[:200]
        tail = f" Reason: {reason}" if reason else ""
        return f"The retrieved sources do not contain enough evidence to answer.{tail}\n"
    if g.confidence == "low":
        last = g.attempts[-1]["attempt"] if g.attempts else None
        details = "\n".join(
            f"- {i['check']}: {i['detail']}" for i in g.issues if i["attempt"] == last
        )
        return f"\n\n{LOW_CONFIDENCE_HEAD}\n{g.low_confidence_reason}\n{details}\n"
    return ""  # unavailable / unjudged: the event stream already carried the notice
