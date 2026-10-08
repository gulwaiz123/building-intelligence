"""Offline tests for hybrid fusion and readiness (no network)."""

import pytest

from building_with_rag.contracts import QueryRequest
from building_with_rag.retrieval import hybrid
from building_with_rag.retrieval.semantic import RetrievalError


def test_fuse_both_routes_rank_first() -> None:
    rows = hybrid.fuse([("a", 0.9), ("b", 0.8)], [("c", 5.0), ("b", 4.0)], limit=5)
    assert rows[0]["chunk_id"] == "b"
    assert (rows[0]["semantic_rank"], rows[0]["keyword_rank"]) == (2, 2)
    assert rows[0]["fused_score"] == pytest.approx(2 / 62)
    assert [r["fused_rank"] for r in rows] == [1, 2, 3]


def test_fuse_single_route_leaves_other_none() -> None:
    rows = {r["chunk_id"]: r for r in hybrid.fuse([("a", 0.9)], [("c", 5.0)], limit=5)}
    assert rows["a"]["keyword_score"] is None and rows["a"]["keyword_rank"] is None
    assert rows["c"]["semantic_score"] is None and rows["c"]["semantic_rank"] is None


def test_fuse_tie_order_deterministic() -> None:
    first = hybrid.fuse([("z", 1.0)], [("a", 1.0)], limit=5)
    assert [r["chunk_id"] for r in first] == ["z", "a"]  # semantic_rank present beats missing
    assert first == hybrid.fuse([("z", 1.0)], [("a", 1.0)], limit=5)


class _Coll:
    def __init__(self, data=None, indexes=()):
        self.data, self.indexes = data or [], list(indexes)

    def list_search_indexes(self, name=None):
        return [i for i in self.indexes if name in (None, i["name"])]

    def find_one(self, *a, **k):
        return {"_id": 1}

    def find(self, query):
        (key, cond), = query.items()
        return [d for d in self.data if d.get(key) in cond["$in"]]

    def aggregate(self, pipeline):
        stage = pipeline[0]
        return self.data if "$search" in stage else self.hits


class _Db(dict):
    pass


def _db(with_keyword: bool = True):
    ready = {"name": "vector_index", "queryable": True}
    chunks = _Coll([{"chunk_id": "c1", "section_id": "s1", "text": "t"}],
                   [{"name": "chunk_text_index", "queryable": True}] if with_keyword else [])
    emb = _Coll(indexes=[ready])
    emb.hits = [{"chunk_id": "c1", "score": 0.9}]
    secs = _Coll([{"section_id": "s1", "act": "BNS_2023", "heading": "H"}])
    return _Db(chunks=chunks, embeddings=emb, sections=secs)


def test_run_hybrid_sets_fused_fields(monkeypatch) -> None:
    db = _db()
    db["chunks"].aggregate = lambda p: [{"chunk_id": "c1", "section_id": "s1", "score": 3.0}]
    request = QueryRequest(question="theft", pattern="hybrid", limit=3)
    results, trace = hybrid.hybrid_search(request, db=db, embed_query=lambda q: [0.0])
    r = results[0]
    assert r.score == r.fused_score and r.fused_rank == 1
    assert (r.semantic_rank, r.keyword_rank) == (1, 1)
    assert trace["contribution"] == {"both": 1, "semantic_only": 0, "keyword_only": 0}


def test_missing_keyword_index_is_not_ready() -> None:
    request = QueryRequest(question="theft", pattern="hybrid")
    with pytest.raises(RetrievalError) as exc:
        hybrid.hybrid_search(request, db=_db(with_keyword=False), embed_query=lambda q: [0.0])
    assert (exc.value.status_code, exc.value.code) == (503, "retrieval_not_ready")
