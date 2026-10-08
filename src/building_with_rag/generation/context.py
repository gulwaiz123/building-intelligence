"""Bounded, labelled evidence context built from semantic, hybrid or re-ranked retrieval results."""

from building_with_rag.contracts import RetrievedChunk

MAX_PASSAGES = 5
MAX_CHARS = 12000


def assemble_context(results: list[RetrievedChunk]) -> tuple[list[dict], dict]:
    """Take passages in score order until a budget is hit; never cut a passage."""
    entries: list[dict] = []
    chars = 0
    for chunk in results:
        if len(entries) >= MAX_PASSAGES or chars + len(chunk.text) > MAX_CHARS:
            break
        chars += len(chunk.text)
        entries.append(
            {
                "label": f"E{len(entries) + 1}",
                "chunk_id": chunk.chunk_id,
                "section_id": chunk.section_id,
                "act": chunk.act,
                "act_label": chunk.act_label,
                "heading": chunk.heading,
                "chapter": chunk.chapter,
                "section_number": chunk.section_number,
                "status": chunk.status,
                "source_pdf": chunk.source_pdf,
                "needs_review": chunk.needs_review,
                "text": chunk.text,
            }
        )
    trace = {
        "labels": {e["label"]: e["chunk_id"] for e in entries},
        "selected": len(entries),
        "omitted": len(results) - len(entries),
        "chars": chars,
    }
    return entries, trace
