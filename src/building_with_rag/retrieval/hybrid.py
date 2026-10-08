"""Hybrid retrieval: Atlas Search keyword route + semantic vector route, fused with RRF.

Both routes search the same chunks (keyword on `chunks.text`, vector on `embeddings.vector`).
Reciprocal Rank Fusion uses ranks only, since BM25 and cosine scores are not comparable.
The fused score ranks passages; it does not prove correctness.
"""

from collections.abc import Callable

from building_with_rag.contracts import QueryRequest, QueryResult, RetrievedChunk
from building_with_rag.ingestion import mongodb_schema as schema
from building_with_rag.retrieval import semantic
from building_with_rag.retrieval.semantic import RetrievalError
from building_with_rag.settings import get_settings

RRF_K = 60
WEIGHTS = {"semantic": 1, "keyword": 1}
KEYWORD_COMMAND = "uv run python -m building_with_rag.ingestion.keyword_index"

_ready = False  # positive readiness cached per process; re-checked after any failure


def route_depth(limit: int) -> int:
    return max(limit, min(50, max(20, 4 * limit)))


def fuse(semantic_hits: list[tuple[str, float]], keyword_hits: list[tuple[str, float]],
         limit: int) -> list[dict]:
    """Reciprocal Rank Fusion over two best-first (chunk_id, score) lists. Pure; no I/O.

    fused_score = sum(1 / (RRF_K + rank)) over routes that returned the chunk. Order: fused
    score desc, then semantic_rank (missing last), then chunk_id. Returns the top `limit`;
    fused_rank is 1-based over the full fused list.
    """
    rows: dict[str, dict] = {}
    for route, hits in (("semantic", semantic_hits), ("keyword", keyword_hits)):
        for rank, (chunk_id, score) in enumerate(hits, start=1):
            row = rows.setdefault(chunk_id, {
                "chunk_id": chunk_id, "semantic_score": None, "semantic_rank": None,
                "keyword_score": None, "keyword_rank": None, "fused_score": 0.0,
            })
            if row[f"{route}_rank"] is not None:  # a route lists a chunk once
                continue
            row[f"{route}_score"], row[f"{route}_rank"] = float(score), rank
            row["fused_score"] += WEIGHTS[route] / (RRF_K + rank)
    ordered = sorted(
        rows.values(),
        key=lambda r: (
            -r["fused_score"],
            r["semantic_rank"] if r["semantic_rank"] is not None else float("inf"),
            r["chunk_id"],
        ),
    )
    for position, row in enumerate(ordered, start=1):
        row["fused_rank"] = position
    return ordered[:limit]


def _ensure_ready(db, cache: bool) -> None:
    """Both search indexes queryable and embeddings non-empty; never degrade silently."""
    global _ready
    if cache and _ready:
        return
    _ready = False
    for coll, name, hint in (
        (schema.EMBEDDINGS_COLLECTION, schema.VECTOR_INDEX_NAME,
         "run `uv run python -m building_with_rag.ingestion.ingest`"),
        (schema.CHUNKS_COLLECTION, schema.KEYWORD_INDEX_NAME, f"run `{KEYWORD_COMMAND}`"),
    ):
        found = list(db[coll].list_search_indexes(name))
        if not found or not (found[0].get("queryable") or found[0].get("status") == "READY"):
            raise RetrievalError(
                503, "retrieval_not_ready", f"Search index '{name}' is missing or not ready; {hint}."
            )
    if db[schema.EMBEDDINGS_COLLECTION].find_one({}, {"_id": 1}) is None:
        raise RetrievalError(503, "retrieval_not_ready", "embeddings collection is empty; run ingestion.")
    _ready = cache


def keyword_hits(request: QueryRequest, filters: dict[str, list[str]], depth: int,
                 db) -> list[dict]:
    """Atlas Search `text` operator on chunks.text (BM25). The question is only the query value."""
    pipeline = [
        {"$search": {
            "index": schema.KEYWORD_INDEX_NAME,
            "compound": {
                "must": [{"text": {"query": request.question, "path": "text"}}],
                "filter": [{"in": {"path": path, "value": values}}
                           for path, values in filters.items() if values],
            },
        }},
        {"$limit": depth},
        {"$project": {"_id": 0, "chunk_id": 1, "section_id": 1, "score": {"$meta": "searchScore"}}},
    ]
    return list(db[schema.CHUNKS_COLLECTION].aggregate(pipeline))


def hybrid_search(
    request: QueryRequest,
    *,
    db=None,
    embed_query: Callable[[str], list[float]] | None = None,
) -> tuple[list[RetrievedChunk], dict]:
    """Return (fused results, trace). Raises RetrievalError."""
    filters = semantic.effective_filters(request)  # same filters feed both routes
    depth = route_depth(request.limit)
    injected = db is not None
    if not injected:
        cfg = get_settings()
        if not cfg.mongodb_uri or not cfg.voyage_api_key:
            raise RetrievalError(503, "retrieval_not_ready", "MONGODB_URI and VOYAGE_API_KEY must be set.")
        db = semantic.default_db()
    global _ready
    try:
        try:
            _ensure_ready(db, cache=not injected)
            v_hits, v_part = semantic.vector_hits(request, limit=depth, db=db, embed_query=embed_query)
            k_hits = keyword_hits(request, filters, depth, db)
            chunks, sections = semantic.resolve_chunks(
                db, list({h["chunk_id"] for h in v_hits} | {h["chunk_id"] for h in k_hits})
            )
        except RetrievalError:
            raise
        except Exception:  # noqa: BLE001 - upstream details must not leak
            raise RetrievalError(
                502, "retrieval_upstream_error", "MongoDB keyword search failed."
            ) from None
    except RetrievalError:
        _ready = False
        raise

    def resolved(hits: list[dict]) -> list[tuple[str, float]]:
        return [
            (h["chunk_id"], h["score"]) for h in hits
            if h["chunk_id"] in chunks and chunks[h["chunk_id"]]["section_id"] in sections
        ]

    sem, kw = resolved(v_hits), resolved(k_hits)
    unresolved = len({h["chunk_id"] for h in v_hits + k_hits}) - len({c for c, _ in sem + kw})
    fused = fuse(sem, kw, request.limit)

    results = []
    for row in fused:
        chunk = chunks[row["chunk_id"]]
        extra = {k: row[k] for k in (
            "semantic_score", "semantic_rank", "keyword_score", "keyword_rank", "fused_rank"
        )}
        results.append(semantic.chunk_result(
            chunk, sections[chunk["section_id"]], row["fused_score"],
            fused_score=row["fused_score"], **extra,
        ))
    both = sum(1 for r in results if r.semantic_rank and r.keyword_rank)
    sem_only = sum(1 for r in results if r.semantic_rank and not r.keyword_rank)
    trace = {
        "mode": "hybrid",
        "query": request.question,
        "embedding": {
            "model": schema.EMBEDDING_MODEL, "input_type": "query",
            "dimensions": schema.EMBEDDING_DIMENSIONS,
        },
        "filters": filters,
        "result_count": len(results),
        "unresolved_hits": unresolved,
        "semantic": {
            "index": schema.VECTOR_INDEX_NAME, "limit": depth,
            "num_candidates": v_part["num_candidates"], "hit_count": len(sem),
        },
        "keyword": {
            "index": schema.KEYWORD_INDEX_NAME, "path": "text", "operator": "text",
            "limit": depth, "hit_count": len(kw),
        },
        "fusion": {"method": "rrf", "k": RRF_K, "weights": WEIGHTS, "route_depth": depth},
        "contribution": {
            "both": both, "semantic_only": sem_only, "keyword_only": len(results) - both - sem_only,
        },
    }
    return results, trace

def run_hybrid(request: QueryRequest) -> QueryResult:
    results, trace = hybrid_search(request)
    trace["caller_id"] = get_settings().webui_demo_caller_id
    note = "The fused score ranks passages only; it does not prove a passage is correct."
    if not results:
        return QueryResult(
            pattern="hybrid", status="no_results", trace=trace,
            message=f"No passages match the given filters. {note}",
        )
    return QueryResult(
        pattern="hybrid", status="ok", trace=trace, results=results,
        message=f"Returned {len(results)} passage(s) by hybrid (keyword + semantic) retrieval. {note}",
    )
