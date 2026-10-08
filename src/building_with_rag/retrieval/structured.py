"""Structured exact retrieval: rule-based classification, then one read-only sections lookup.

Only validated StructuredSignals (act, section_number, optional chapter) reach MongoDB; the raw
question never does. No LLM, no embedding, no vector/keyword index, no fallback to other modes.
"""

import logging
import re

from building_with_rag.contracts import QueryRequest, QueryResult, StructuredSignals
from building_with_rag.ingestion import mongodb_schema as schema
from building_with_rag.retrieval import semantic
from building_with_rag.retrieval.semantic import RetrievalError
from building_with_rag.settings import get_settings

log = logging.getLogger(__name__)

NOTE = (
    "Exact record from the supplied corpus; status and source_status_version describe the "
    "source document and do not claim current legal applicability."
)
_AGGREGATION = re.compile(r"\bhow many\b|\bcount\b|\btotal number\b", re.IGNORECASE)
_FILTER = re.compile(r"\blist\b|\ball sections\b|\bwhich sections\b|\bsections?\s+(?:in|under)\b", re.IGNORECASE)
_SECTION_REF = re.compile(r"(?<![A-Za-z])(?:sections?|sec\.?|s\.|§)\s*(\d+)([A-Za-z]?)(?![A-Za-z0-9])", re.IGNORECASE)
_ACTS = {
    "BNS_2023": re.compile(r"\bbns\b|bharatiya nyaya sanhita", re.IGNORECASE),
    "IPC_1860": re.compile(r"\bipc\b|indian penal code", re.IGNORECASE),
}
_CHAPTER = re.compile(r"[A-Za-z0-9 .\-]{1,40}")


def _signals(status: str, reason: str, **kw) -> StructuredSignals:
    return StructuredSignals(status=status, reason=reason, **kw)


def classify(question: str, chapter: str | None = None) -> StructuredSignals:
    """Pure rule-based classifier. `chapter` is a bound value, never parsed from the question."""
    if chapter is not None and not _CHAPTER.fullmatch(chapter):
        raise RetrievalError(422, "unsupported_option", "chapter must be 1-40 letters, digits, spaces, '.' or '-'.")
    chapter = chapter.strip() if chapter else None
    if _AGGREGATION.search(question):
        return _signals("recommendation", "Counting or aggregation is recognised but not executed in structured mode.",
                        intent="aggregation", chapter=chapter)
    if _FILTER.search(question):
        return _signals("recommendation", "Listing or filtering sections is recognised but not executed in structured mode.",
                        intent="filter", chapter=chapter)
    refs = _SECTION_REF.findall(question)
    acts = [a for a, rx in _ACTS.items() if rx.search(question)]
    if refs:
        if any(suffix for _, suffix in refs):
            return _signals("clarification_needed", "Only whole-number sections are supported (no '103A').",
                            intent="exact_lookup", chapter=chapter)
        numbers = {int(n) for n, _ in refs}
        if len(numbers) > 1:
            return _signals("clarification_needed", "Several section numbers were named; ask for one section at a time.",
                            intent="exact_lookup", chapter=chapter)
        number = numbers.pop()
        if not 1 <= number <= 999:
            return _signals("clarification_needed", "The section number must be between 1 and 999.",
                            intent="exact_lookup", chapter=chapter)
        if not acts:
            return _signals("clarification_needed", "No act was named; say whether you mean BNS or IPC.",
                            intent="exact_lookup", section_number=number, chapter=chapter)
        if len(acts) > 1:
            return _signals("clarification_needed", "Both BNS and IPC were named; ask for one act at a time.",
                            intent="exact_lookup", section_number=number, chapter=chapter)
        return _signals("ok", "Exact section reference with one act.", intent="exact_lookup",
                        act=acts[0], section_number=number, chapter=chapter)
    return _signals("recommendation", "No section reference found; use semantic or hybrid for open questions.",
                    chapter=chapter)


def _check_scope(request: QueryRequest) -> None:
    caller = get_settings().webui_demo_caller_id
    if request.caller_id is not None and request.caller_id != caller:
        raise RetrievalError(422, "invalid_request", "caller_id does not match the effective caller.")
    if request.required_acts is not None:
        raise RetrievalError(422, "invalid_request", "required_acts is not supported by structured mode.")


def run_structured(request: QueryRequest, *, db=None) -> QueryResult:
    _check_scope(request)
    signals = classify(request.question, request.chapter)
    filters = semantic.effective_filters(request)
    trace = {
        "mode": "structured", "signals": signals.model_dump(), "mongodb_called": False,
        "collection": schema.SECTIONS_COLLECTION, "filters": filters,
        "caller_id": get_settings().webui_demo_caller_id, "result_count": 0,
    }
    if (signals.status == "ok" and filters["act"] and signals.act not in filters["act"]):
        signals = _signals("clarification_needed",
                           f"The act filter excludes {signals.act}; the question names that act.",
                           intent="exact_lookup", act=signals.act,
                           section_number=signals.section_number, chapter=signals.chapter)
        trace["signals"] = signals.model_dump()
    if signals.status != "ok":
        return QueryResult(pattern="structured", status=signals.status, message=signals.reason, trace=trace)

    predicate = {
        "act": signals.act,
        "section_number": signals.section_number,
        "access_level": {"$in": filters["access_level"]},
    }
    if filters["status"]:
        predicate["status"] = {"$in": filters["status"]}
    if signals.chapter:
        predicate["chapter"] = signals.chapter
    if db is None:
        if not get_settings().mongodb_uri:
            raise RetrievalError(503, "retrieval_not_ready", "MONGODB_URI must be set.")
        db = semantic.default_db()
    trace["mongodb_called"] = True
    try:
        section = db[schema.SECTIONS_COLLECTION].find_one(predicate, {"_id": 0, "provenance": 0})
    except Exception as exc:  # noqa: BLE001 - upstream details must not leak
        log.warning("structured lookup failed: %s", type(exc).__name__)
        raise RetrievalError(502, "retrieval_upstream_error", "MongoDB section lookup failed.") from None
    if section is None:
        return QueryResult(
            pattern="structured", status="not_found", trace=trace,
            message="This corpus has no such record (this does not mean the law has no such section).",
        )
    chunk = semantic.chunk_result(
        {"chunk_id": section["section_id"], "text": section.get("text")}, section, 1.0
    )
    trace["result_count"] = 1
    trace["record"] = {
        "section_id": section["section_id"], "status": section.get("status"),
        "source_status_version": section.get("source_status_version"),
    }
    return QueryResult(pattern="structured", status="ok", trace=trace, results=[chunk], message=NOTE)
