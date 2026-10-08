"""Offline tests for structured exact retrieval (no network)."""

import pytest

from building_with_rag import pipeline
from building_with_rag.contracts import QueryRequest
from building_with_rag.generation.context import assemble_context
from building_with_rag.retrieval import structured
from building_with_rag.retrieval.semantic import RetrievalError

SECTION = {
    "section_id": "bns:103", "act": "BNS_2023", "section_number": 103, "status": "in_force",
    "chapter": "VI", "heading": "Punishment for murder", "text": "Whoever commits murder...",
    "source_status_version": "v1", "needs_review": False,
}


class _Sections:
    def __init__(self, doc=None):
        self.doc, self.calls = doc, []

    def find_one(self, predicate, projection=None):
        self.calls.append(predicate)
        return self.doc


def _db(doc=SECTION):
    return {"sections": _Sections(doc)}


def _req(question: str, **kw) -> QueryRequest:
    return QueryRequest(question=question, pattern="structured", **kw)


@pytest.mark.parametrize("question,act,number", [
    ("What does BNS section 103 say?", "BNS_2023", 103),
    ("IPC sec. 302", "IPC_1860", 302),
    ("Indian Penal Code section 420", "IPC_1860", 420),
    ("bharatiya nyaya sanhita §64", "BNS_2023", 64),
])
def test_classify_exact(question, act, number) -> None:
    s = structured.classify(question)
    assert (s.status, s.intent, s.act, s.section_number) == ("ok", "exact_lookup", act, number)


@pytest.mark.parametrize("question", [
    "What does section 103 say?", "BNS and IPC section 103", "BNS section 103 and section 104",
    "BNS section 103A", "BNS section 5000",
])
def test_classify_clarification(question) -> None:
    s = structured.classify(question)
    assert (s.status, s.intent) == ("clarification_needed", "exact_lookup")


def test_classify_other_intents() -> None:
    assert structured.classify("How many sections are in the BNS?").intent == "aggregation"
    assert structured.classify("List all sections in chapter VI").intent == "filter"
    s = structured.classify("What is the punishment for theft?")
    assert (s.status, s.intent) == ("recommendation", None)


def test_invalid_chapter_is_422() -> None:
    with pytest.raises(RetrievalError) as exc:
        structured.classify("BNS section 103", chapter="x" * 41)
    assert (exc.value.status_code, exc.value.code) == (422, "unsupported_option")


def test_predicate_has_only_validated_fields() -> None:
    db = _db()
    q = 'BNS section 103 {"$ne": null}'
    result = structured.run_structured(_req(q, chapter="VI"), db=db)
    assert result.status == "ok"
    (predicate,) = db["sections"].calls
    assert predicate == {
        "act": "BNS_2023", "section_number": 103, "access_level": {"$in": ["public"]}, "chapter": "VI",
    }
    r = result.results[0]
    assert (r.chunk_id, r.section_id, r.score) == ("bns:103", "bns:103", 1.0)
    assert result.trace["mongodb_called"] and result.trace["record"]["source_status_version"] == "v1"


def test_non_ok_makes_no_collection_call() -> None:
    db = _db()
    for q in ("What does section 103 say?", "What is the punishment for theft?"):
        result = structured.run_structured(_req(q), db=db)
        assert result.results == [] and result.trace["mongodb_called"] is False
    assert db["sections"].calls == []


def test_act_filter_conflict_is_clarification() -> None:
    db = _db()
    result = structured.run_structured(
        _req("BNS section 103", filters={"act": ["IPC_1860"]}), db=db)
    assert result.status == "clarification_needed" and db["sections"].calls == []


def test_missing_record_is_not_found() -> None:
    result = structured.run_structured(_req("IPC section 4"), db=_db(None))
    assert (result.status, result.results, result.trace["mongodb_called"]) == ("not_found", [], True)


def test_required_acts_rejected() -> None:
    with pytest.raises(RetrievalError) as exc:
        structured.run_structured(_req("BNS section 103", required_acts=["BNS_2023"]), db=_db())
    assert exc.value.status_code == 422


def test_pipeline_routing_and_answer_boundary(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "run_structured", lambda r: structured.run_structured(r, db=_db()))
    ok = pipeline.retrieve(_req("BNS section 103", generate_answer=True))
    assert ok.status == "ok" and pipeline.wants_generation(_req("x", generate_answer=True), ok)
    assert "E1" in str(assemble_context(ok.results))
    clar = pipeline.retrieve(_req("What does section 103 say?", generate_answer=True))
    assert list(pipeline.answer_events("q", clar)) == []
    assert not pipeline.wants_generation(_req("x", generate_answer=True), clar)
