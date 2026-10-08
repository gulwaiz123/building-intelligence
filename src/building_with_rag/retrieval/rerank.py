"""Hybrid re-ranking: bounded hybrid candidates -> one provider re-ranking call -> final evidence.

Re-ranking scores rank only; they do not prove correctness. No fallback to hybrid order.
"""

import logging
import math
import time

import httpx

from building_with_rag.contracts import QueryRequest, QueryResult, RetrievedChunk
from building_with_rag.registry import Pattern
from building_with_rag.retrieval.hybrid import run_hybrid
from building_with_rag.retrieval.semantic import RetrievalError
from building_with_rag.settings import Settings, get_settings

log = logging.getLogger(__name__)
MODE = "hybrid-reranked"
UPSTREAM_MESSAGE = "Re-ranking request failed; no re-ranked result returned."


def _not_ready(message: str) -> RetrievalError:
    return RetrievalError(503, "retrieval_not_ready", message)


def validate_settings(s: Settings) -> None:
    """Raise 503 retrieval_not_ready naming the offending setting (never its value)."""
    for name, value in (
        ("RERANK_API_KEY", s.rerank_api_key),
        ("RERANK_API_BASE_URL", s.rerank_api_base_url),
        ("RERANK_MODEL_NAME", s.rerank_model_name),
    ):
        if not value.strip():
            raise _not_ready(f"{name} is not set; {MODE} requires it.")
    if s.rerank_request_timeout_seconds < 1:
        raise _not_ready("RERANK_REQUEST_TIMEOUT_SECONDS must be at least 1.")
    if not 1 <= s.rerank_return_limit <= s.rerank_send_limit <= s.rerank_candidate_limit <= 20:
        raise _not_ready(
            "RERANK_RETURN_LIMIT, RERANK_SEND_LIMIT and RERANK_CANDIDATE_LIMIT must satisfy "
            "1 <= RETURN <= SEND <= CANDIDATE <= 20."
        )


def parse_reply(body: object, sent: int) -> tuple[dict[int, float], int | None]:
    """Validate the provider reply; return ({index: relevance_score}, usage_tokens)."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or not data:
        raise ValueError("data is not a non-empty list")
    scores: dict[int, float] = {}
    for item in data:
        index = item.get("index") if isinstance(item, dict) else None
        score = item.get("relevance_score") if isinstance(item, dict) else None
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < sent:
            raise ValueError("index out of range")
        if index in scores:
            raise ValueError("duplicate index")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError("invalid relevance_score")
        scores[index] = float(score)
    if len(scores) != sent:
        raise ValueError("not every sent candidate was scored")
    usage = body.get("usage")
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    return scores, tokens if isinstance(tokens, int) else None


def call_reranker(query: str, documents: list[str], s: Settings) -> tuple[dict[int, float], int | None]:
    """One POST {base}/rerank, no top_k, no retries. Raises RetrievalError 502 on any failure."""
    try:
        response = httpx.post(
            s.rerank_api_base_url.rstrip("/") + "/rerank",
            headers={"Authorization": f"Bearer {s.rerank_api_key}"},
            json={"model": s.rerank_model_name, "query": query, "documents": documents},
            timeout=s.rerank_request_timeout_seconds,
        )
        response.raise_for_status()
        return parse_reply(response.json(), len(documents))
    except Exception as exc:  # noqa: BLE001 - provider details must not leak
        log.warning("rerank call failed: %s", type(exc).__name__)
        raise RetrievalError(502, "retrieval_upstream_error", UPSTREAM_MESSAGE) from None


def select(candidates: list[RetrievedChunk], scores: dict[int, float], send_limit: int,
           final_limit: int) -> tuple[list[RetrievedChunk], list[RetrievedChunk]]:
    """Pure selection. `candidates` are in fused_rank order; `scores` index the first send_limit.

    Returns (final results by rerank_rank, omitted candidates in fused_rank order).
    """
    sent = candidates[:send_limit]
    order = sorted(range(len(sent)), key=lambda i: (-scores[i], sent[i].fused_rank or 0))
    rank_of = {i: r for r, i in enumerate(order, start=1)}
    final: list[RetrievedChunk] = []
    omitted: list[RetrievedChunk] = []
    for i, chunk in enumerate(sent):
        update = {"rerank_score": scores[i], "rerank_rank": rank_of[i]}
        if rank_of[i] <= final_limit:
            final.append(chunk.model_copy(update={**update, "score": scores[i]}))
        else:
            omitted.append(chunk.model_copy(update={**update, "omitted_reason": "below_return_limit"}))
    omitted += [c.model_copy(update={"omitted_reason": "not_sent_to_reranker"})
                for c in candidates[send_limit:]]
    final.sort(key=lambda c: c.rerank_rank)
    omitted.sort(key=lambda c: c.fused_rank or 0)
    return final, omitted


def run_hybrid_reranked(request: QueryRequest) -> QueryResult:
    s = get_settings()
    validate_settings(s)  # before any Voyage/MongoDB call
    hybrid = run_hybrid(request.model_copy(
        update={"pattern": Pattern.HYBRID, "limit": s.rerank_candidate_limit}
    ))
    note = "Re-ranking scores rank only; they do not prove a passage is correct."
    if hybrid.status != "ok":
        return QueryResult(
            pattern=MODE, status=hybrid.status, message=f"{hybrid.message} {note}",
            trace={"mode": MODE, "query": request.question, "result_count": 0},
        )
    candidates = hybrid.results
    sent = candidates[: s.rerank_send_limit]
    started = time.monotonic()
    scores, tokens = call_reranker(
        request.question, [f"{c.heading}\n{c.text}" for c in sent], s
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    final, omitted = select(candidates, scores, s.rerank_send_limit,
                            min(request.limit, s.rerank_return_limit))
    h = hybrid.trace
    rerank = {
        "model": s.rerank_model_name, "candidate_limit": s.rerank_candidate_limit,
        "send_limit": s.rerank_send_limit, "return_limit": s.rerank_return_limit,
        "candidates": len(candidates), "sent": len(sent), "returned": len(final),
        "omitted_before": len(candidates) - len(sent),
        "omitted_after": len(sent) - len(final), "latency_ms": latency_ms,
    }
    if tokens is not None:
        rerank["usage_tokens"] = tokens
    trace = {
        "mode": MODE, "query": request.question, "filters": h.get("filters"),
        "caller_id": h.get("caller_id"), "result_count": len(final),
        "hybrid": {k: h.get(k) for k in (
            "embedding", "semantic", "keyword", "fusion", "contribution", "unresolved_hits")},
        "rerank": rerank,
    }
    return QueryResult(
        pattern=MODE, status="ok", trace=trace, results=final, omitted_candidates=omitted,
        message=f"Returned {len(final)} passage(s) re-ranked from {len(candidates)} hybrid "
                f"candidate(s). {note}",
    )
