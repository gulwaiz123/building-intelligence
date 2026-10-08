"""Semantic retrieval: embed the question, run MongoDB $vectorSearch, resolve passages."""

from pymongo import MongoClient

from building_with_rag.contracts import QueryRequest, QueryResult, RetrievedChunk
from building_with_rag.ingestion import mongodb_schema as schema
from building_with_rag.settings import get_settings

INPUT_TYPE = "query"
MIN_CANDIDATES = 50
MAX_CANDIDATES = 200
TIMEOUT_MS = 5000
SCORE_NOTE = (
    "Scores rank similarity only; they do not prove a passage is correct or answers the question."
)

_mongo: MongoClient | None = None
_voyage = None
_ready = False


class RetrievalError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def num_candidates(limit: int) -> int:
    """limit*10 clamped to [max(limit, 50), 200]."""
    return min(MAX_CANDIDATES, max(limit, MIN_CANDIDATES, limit * 10))


def effective_filters(request: QueryRequest) -> dict[str, list[str]]:
    """Server fixes access_level=public; caller lists only narrow act/status."""
    f = request.filters
    return {
        "act": list(f.act) if f else [],
        "status": list(f.status) if f else [],
        "access_level": [schema.DEFAULT_ACCESS_LEVEL],
    }


def default_db():
    global _mongo
    s = get_settings()
    if _mongo is None:
        _mongo = MongoClient(
            s.mongodb_uri,
            serverSelectionTimeoutMS=TIMEOUT_MS,
            connectTimeoutMS=TIMEOUT_MS,
            socketTimeoutMS=15000,
        )
    return _mongo[s.mongodb_db_name]


def _voyage_client():
    global _voyage
    if _voyage is None:
        from voyageai import Client

        _voyage = Client(api_key=get_settings().voyage_api_key, timeout=15, max_retries=4)
    return _voyage


def _not_ready(message: str) -> RetrievalError:
    return RetrievalError(503, "retrieval_not_ready", message)


def _ensure_ready() -> None:
    global _ready
    if _ready:
        return
    s = get_settings()
    if not s.mongodb_uri or not s.voyage_api_key:
        raise _not_ready("MONGODB_URI and VOYAGE_API_KEY must be set.")
    try:
        coll = default_db()[schema.EMBEDDINGS_COLLECTION]
        idx = next(
            (i for i in coll.list_search_indexes() if i.get("name") == schema.VECTOR_INDEX_NAME),
            None,
        )
        sample = coll.find_one({}, {"model": 1, "dimensions": 1})
    except Exception:  # noqa: BLE001 - upstream details must not leak
        raise RetrievalError(
            502, "retrieval_upstream_error", "MongoDB readiness check failed."
        ) from None
    if idx is None:
        raise _not_ready("vector_index is missing; run Story 2.2 ingestion.")
    if not idx.get("queryable"):
        raise _not_ready("vector_index is not queryable yet; retry shortly.")
    if sample is None:
        raise _not_ready("embeddings collection is empty; run Story 2.2 ingestion.")
    if (
        sample.get("model") != schema.EMBEDDING_MODEL
        or sample.get("dimensions") != schema.EMBEDDING_DIMENSIONS
    ):
        raise _not_ready("Stored embeddings do not match voyage-3.5 / 1024 dimensions.")
    _ready = True


def _embed(question: str) -> list[float]:
    try:
        vec = (
            _voyage_client()
            .embed(texts=[question], model=schema.EMBEDDING_MODEL, input_type=INPUT_TYPE)
            .embeddings[0]
        )
    except Exception:  # noqa: BLE001 - upstream details must not leak
        raise RetrievalError(
            502, "retrieval_upstream_error", "Voyage embedding request failed."
        ) from None
    if len(vec) != schema.EMBEDDING_DIMENSIONS:
        raise _not_ready("Query embedding dimension mismatch.")
    return vec


def _search_filter(filters: dict[str, list[str]]) -> dict:
    return {"$and": [{field: {"$in": vals}} for field, vals in filters.items() if vals]}


def chunk_result(chunk: dict, section: dict, score: float, **extra) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk["chunk_id"],
        section_id=section["section_id"],
        act=section.get("act") or chunk.get("act") or "",
        text=chunk.get("text") or "",
        heading=section.get("heading") or "",
        score=float(score),
        chunk_index=chunk.get("chunk_index"),
        act_label=section.get("act_label"),
        status=section.get("status"),
        chapter=section.get("chapter"),
        chapter_title=section.get("chapter_title"),
        section_number=section.get("section_number"),
        source_pdf=section.get("source_pdf"),
        source_sha256=section.get("source_sha256"),
        needs_review=section.get("needs_review"),
        **extra,
    )


def resolve_chunks(db, ids: list[str]) -> tuple[dict, dict]:
    """Return ({chunk_id: chunk}, {section_id: section}) for the given chunk IDs."""
    chunks = {
        c["chunk_id"]: c for c in db[schema.CHUNKS_COLLECTION].find({"chunk_id": {"$in": ids}})
    }
    sec_ids = list({c["section_id"] for c in chunks.values()})
    sections = {
        s["section_id"]: s
        for s in db[schema.SECTIONS_COLLECTION].find({"section_id": {"$in": sec_ids}})
    }
    return chunks, sections


def vector_hits(request: QueryRequest, limit: int, db, embed_query=None) -> tuple[list[dict], dict]:
    """Embed the question and run $vectorSearch; returns (hits, {"num_candidates": n}).

    Raises RetrievalError 502 on Voyage or MongoDB failure.
    """
    n_cand = num_candidates(limit)
    vec = (embed_query or _embed)(request.question)
    try:
        hits = list(
            db[schema.EMBEDDINGS_COLLECTION].aggregate(
                [
                    {
                        "$vectorSearch": {
                            "index": schema.VECTOR_INDEX_NAME,
                            "path": "vector",
                            "queryVector": vec,
                            "numCandidates": n_cand,
                            "limit": limit,
                            "filter": _search_filter(effective_filters(request)),
                        }
                    },
                    {"$project": {"_id": 0, "chunk_id": 1, "score": {"$meta": "vectorSearchScore"}}},
                ]
            )
        )
    except Exception:  # noqa: BLE001 - upstream details must not leak
        raise RetrievalError(
            502, "retrieval_upstream_error", "MongoDB vector search failed."
        ) from None
    return hits, {"num_candidates": n_cand}


def _resolve(db, hits: list[dict]) -> tuple[list[RetrievedChunk], int]:
    chunks, sections = resolve_chunks(db, [h["chunk_id"] for h in hits])
    out: list[RetrievedChunk] = []
    unresolved = 0
    for h in hits:
        c = chunks.get(h["chunk_id"])
        s = sections.get(c["section_id"]) if c else None
        if c is None or s is None:
            unresolved += 1
            continue
        out.append(chunk_result(c, s, h["score"]))
    return out, unresolved


def semantic_retrieve(request: QueryRequest) -> QueryResult:
    global _ready
    limit = request.limit
    filters = effective_filters(request)
    n_cand = num_candidates(limit)
    ignored: list[str] = []
    trace = {
        "mode": "semantic",
        "query": request.question,
        "embedding": {
            "model": schema.EMBEDDING_MODEL,
            "input_type": INPUT_TYPE,
            "dimensions": schema.EMBEDDING_DIMENSIONS,
        },
        "index": schema.VECTOR_INDEX_NAME,
        "limit": limit,
        "num_candidates": n_cand,
        "filters": filters,
        "caller_id": get_settings().webui_demo_caller_id,
        "result_count": 0,
        "ignored": ignored,
        "unresolved_hits": 0,
    }
    try:
        _ensure_ready()
        vec = _embed(request.question)
        db = default_db()
        try:
            hits = list(
                db[schema.EMBEDDINGS_COLLECTION].aggregate(
                    [
                        {
                            "$vectorSearch": {
                                "index": schema.VECTOR_INDEX_NAME,
                                "path": "vector",
                                "queryVector": vec,
                                "numCandidates": n_cand,
                                "limit": limit,
                                "filter": _search_filter(filters),
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "chunk_id": 1,
                                "score": {"$meta": "vectorSearchScore"},
                            }
                        },
                    ]
                )
            )
            results, unresolved = _resolve(db, hits)
        except Exception:  # noqa: BLE001 - upstream details must not leak
            raise RetrievalError(
                502, "retrieval_upstream_error", "MongoDB vector search failed."
            ) from None
    except RetrievalError:
        _ready = False
        raise
    trace["result_count"] = len(results)
    trace["unresolved_hits"] = unresolved
    if not results:
        return QueryResult(
            pattern="semantic",
            status="no_results",
            message=f"No passages match the given filters. {SCORE_NOTE}",
            trace=trace,
        )
    return QueryResult(
        pattern="semantic",
        status="ok",
        message=f"Returned {len(results)} passage(s) by vector similarity. {SCORE_NOTE}",
        trace=trace,
        results=results,
    )
