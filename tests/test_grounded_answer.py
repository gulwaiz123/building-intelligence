"""Story 3.1 parser/generation tests (no network)."""

import json

from building_with_rag.contracts import RetrievedChunk
from building_with_rag.generation import answer

LABELS = {"E1", "E2"}


def _payload(**over) -> str:
    base = {
        "outcome": "answered",
        "answer": "Theft is punished.",
        "claims": [{"text": "Punished.", "evidence": ["E1"]}],
        "reason": "",
    }
    return json.dumps({**base, **over})


def test_unknown_label_malformed() -> None:
    bad = _payload(claims=[{"text": "x", "evidence": ["E9"]}])
    assert answer.parse_output(bad, LABELS) is None


def test_non_json_malformed() -> None:
    assert answer.parse_output("not json", LABELS) is None


def test_answered_without_claims_malformed() -> None:
    assert answer.parse_output(_payload(claims=[]), LABELS) is None


def test_valid_answer_resolves_citations(monkeypatch) -> None:
    chunk = RetrievedChunk(
        chunk_id="c1", section_id="bns:303", act="BNS_2023", text="t", heading="Theft", score=0.9
    )

    class Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"model": "m", "choices": [{"message": {"content": _payload()}}]}

    monkeypatch.setattr(answer.httpx, "post", lambda *a, **k: Resp())
    monkeypatch.setattr(
        answer,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "generation_model_name": "m",
                "generation_api_base_url": "http://x/",
                "generation_api_key": "k",
            },
        )(),
    )
    out = answer.generate_answer("q", [chunk])
    assert out.outcome == "answered"
    assert out.citations[0].chunk_id == "c1"
    assert out.citations[0].section_id == "bns:303"
    assert out.supporting_passages[0].chunk_id == "c1"
