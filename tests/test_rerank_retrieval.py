"""Offline tests for hybrid re-ranking (no network)."""

import httpx
import pytest

from building_with_rag import pipeline
from building_with_rag.contracts import QueryRequest, QueryResult, RetrievedChunk
from building_with_rag.generation.context import assemble_context
from building_with_rag.retrieval import rerank
from building_with_rag.retrieval.semantic import RetrievalError
from building_with_rag.settings import Settings


def _cfg(**kw) -> Settings:
    base = {"rerank_api_key": "k", "rerank_api_base_url": "https://rerank.test/v1",
            "rerank_candidate_limit": 4, "rerank_send_limit": 3, "rerank_return_limit": 2}
    return Settings(_env_file=None, **{**base, **kw})


def _hybrid_result() -> QueryResult:
    chunks = [RetrievedChunk(chunk_id=f"c{i}", section_id=f"s{i}", act="BNS_2023", text=f"t{i}",
                             heading="H", score=1 / i, fused_score=1 / i, fused_rank=i)
              for i in range(1, 5)]
    return QueryResult(pattern="hybrid", status="ok", message="m", trace={"filters": {}},
                       results=chunks)


def _request() -> QueryRequest:
    return QueryRequest(question="q", pattern="hybrid-reranked", limit=5)


@pytest.fixture()
def wired(monkeypatch):
    calls = {"hybrid": 0}

    def fake_hybrid(request):
        calls["hybrid"] += 1
        assert request.limit == 4 and request.pattern.value == "hybrid"
        return _hybrid_result()

    monkeypatch.setattr(rerank, "get_settings", _cfg)
    monkeypatch.setattr(rerank, "run_hybrid", fake_hybrid)
    return calls, monkeypatch


def test_reorders_and_omits(wired) -> None:
    _, mp = wired
    mp.setattr(rerank, "call_reranker", lambda q, d, s: ({0: 0.1, 1: 0.9, 2: 0.5}, 42))
    result = rerank.run_hybrid_reranked(_request())
    assert [c.chunk_id for c in result.results] == ["c2", "c3"]
    assert [c.rerank_rank for c in result.results] == [1, 2]
    assert all(c.score == c.rerank_score for c in result.results)
    assert [c.fused_rank for c in result.results] == [2, 3]
    below, cut = result.omitted_candidates
    assert (below.chunk_id, below.omitted_reason, below.rerank_rank) == ("c1", "below_return_limit", 3)
    assert (cut.chunk_id, cut.omitted_reason, cut.rerank_score) == ("c4", "not_sent_to_reranker", None)
    t = result.trace["rerank"]
    assert (t["candidates"], t["sent"], t["returned"]) == (4, 3, 2)
    assert (t["omitted_before"], t["omitted_after"], t["usage_tokens"]) == (1, 1, 42)


def test_empty_key_is_503_without_calls(wired) -> None:
    calls, mp = wired
    mp.setattr(rerank, "get_settings", lambda: _cfg(rerank_api_key=""))
    with pytest.raises(RetrievalError) as exc:
        rerank.run_hybrid_reranked(_request())
    assert (exc.value.status_code, exc.value.code) == (503, "retrieval_not_ready")
    assert "RERANK_API_KEY" in exc.value.message and calls["hybrid"] == 0


def test_invalid_limits_503(wired) -> None:
    calls, mp = wired
    mp.setattr(rerank, "get_settings", lambda: _cfg(rerank_send_limit=5))
    with pytest.raises(RetrievalError) as exc:
        rerank.run_hybrid_reranked(_request())
    assert exc.value.status_code == 503 and calls["hybrid"] == 0


def _response(status: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {}, request=httpx.Request("POST", "https://x"))


@pytest.mark.parametrize("body", [
    {"data": []},
    {"data": [{"index": 5, "relevance_score": 1}]},
    {"data": [{"index": 0, "relevance_score": 1}, {"index": 0, "relevance_score": 2}]},
    {"data": [{"index": 0}, {"index": 1}, {"index": 2}]},
    {"data": [{"index": 0, "relevance_score": 1}]},  # not every sent candidate scored
])
def test_invalid_reply_is_502(wired, body) -> None:
    _, mp = wired
    mp.setattr(rerank.httpx, "post", lambda *a, **k: _response(200, body))
    with pytest.raises(RetrievalError) as exc:
        rerank.run_hybrid_reranked(_request())
    assert (exc.value.status_code, exc.value.code) == (502, "retrieval_upstream_error")


@pytest.mark.parametrize("failure", ["timeout", "non2xx"])
def test_timeout_and_non_2xx_are_502(wired, failure) -> None:
    _, mp = wired

    def post(*a, **k):
        if failure == "timeout":
            raise httpx.ReadTimeout("t")
        return _response(500)

    mp.setattr(rerank.httpx, "post", post)
    with pytest.raises(RetrievalError) as exc:
        rerank.run_hybrid_reranked(_request())
    assert exc.value.status_code == 502 and "http" not in exc.value.message


def test_routing_and_context_use_final_results_only(wired) -> None:
    _, mp = wired
    mp.setattr(rerank, "call_reranker", lambda q, d, s: ({0: 0.1, 1: 0.9, 2: 0.5}, None))
    mp.setattr(pipeline, "run_hybrid_reranked", rerank.run_hybrid_reranked)
    result = pipeline.retrieve(_request())
    assert result.pattern == "hybrid-reranked"
    ctx = assemble_context(result.results)
    assert "t2" in str(ctx) and "t3" in str(ctx)
    assert "t1" not in str(ctx) and "t4" not in str(ctx)
