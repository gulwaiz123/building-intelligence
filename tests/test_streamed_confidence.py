"""Story 3.2 offline tests: fake provider, no network."""

import json

import pytest
from fastapi.testclient import TestClient

from building_with_rag.app import app
from building_with_rag.contracts import QueryResult, RetrievedChunk
from building_with_rag.generation import answer

GOOD = "Under the BNS, theft is punishable. [E1]"
BAD_LABEL = "Under the BNS, theft is punishable. [E9]"
SUPPORTED = json.dumps({"results": [{"claim": 1, "supported": True}]})


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1", section_id="bns:303", act="BNS_2023", text="Whoever commits theft",
        heading="Theft", score=0.9, section_number=303,
    )


class Fake:
    """Scripted provider: one entry per streamed attempt; validator replies in order."""

    def __init__(self, attempts, validator=(SUPPORTED,)):
        self.attempts, self.validator, self.calls = list(attempts), list(validator), 0

    def stream(self, messages):
        self.calls += 1
        item = self.attempts.pop(0)
        for piece in item if isinstance(item, list) else [item]:
            if isinstance(piece, Exception):
                raise piece
            yield piece

    def chat(self, messages):
        return self.validator.pop(0)


@pytest.fixture
def fake(monkeypatch):
    def install(attempts, validator=(SUPPORTED,)):
        f = Fake(attempts, validator)
        monkeypatch.setattr(answer, "_stream_chat", f.stream)
        monkeypatch.setattr(answer, "_chat", f.chat)
        return f

    return install


def _run():
    events = list(answer.generate_events("q", [_chunk()]))
    final = events[-1][1]
    streamed = "".join(p for k, p in events if k != "final")
    return streamed, final


def test_pass_single_call_streamed_equals_text(fake) -> None:
    f = fake([[GOOD[:10], GOOD[10:]]])
    streamed, g = _run()
    assert f.calls == 1
    assert g.outcome == "answered" and g.confidence == "high"
    assert streamed == "DRAFT — checking evidence\n\n" + g.text
    assert g.citations[0].section_id == "bns:303"


def test_retry_then_pass(fake) -> None:
    fake([BAD_LABEL, GOOD])
    streamed, g = _run()
    assert streamed.count("DRAFT — checking evidence") == 2
    assert "Retrying (attempt 2 of 2)" in streamed
    assert [a["status"] for a in g.attempts] == ["failed", "passed"]
    assert g.issues[0]["check"] == "citation_labels" and g.confidence == "high"


def test_both_fail_low_confidence(fake) -> None:
    fake([BAD_LABEL, BAD_LABEL])
    streamed, g = _run()
    assert g.outcome == "malformed" and g.confidence == "low" and g.text == ""
    assert g.draft_answer == BAD_LABEL and g.low_confidence_reason
    assert len(g.issues) == 2 and "E9" in g.issues[1]["detail"]
    from building_with_rag.pipeline import render_footer

    footer = render_footer(QueryResult(pattern="semantic", status="ok", message="", trace={},
                                       generation=g))
    assert "DRAFT — low confidence, not the final answer." in footer


def test_provider_failure_mid_stream(fake, monkeypatch) -> None:
    fake([["Partial text ", answer.ProviderError("provider request failed: ReadTimeout")]])
    streamed, g = _run()
    assert g.outcome == "unavailable" and g.confidence is None
    assert streamed.endswith(answer.UNAVAILABLE_AFTER_TEXT + "\n")
    assert g.draft_answer == "Partial text "


def test_chat_stream_ends_with_stop_and_done(fake, monkeypatch) -> None:
    fake([GOOD])
    from building_with_rag import pipeline

    monkeypatch.setattr(
        pipeline, "semantic_retrieve",
        lambda r: QueryResult(pattern="semantic", status="ok", message="m", trace={},
                              results=[_chunk()]),
    )
    client = TestClient(app)
    with client.stream("POST", "/v1/chat/completions", json={
        "model": "rag-semantic", "stream": True,
        "messages": [{"role": "user", "content": "theft?"}],
    }) as r:
        raw = b"".join(r.iter_bytes()).decode()
    assert r.status_code == 200
    assert '"finish_reason": "stop"' in raw and "confidence: high" in raw
    assert raw.strip().endswith("data: [DONE]")
