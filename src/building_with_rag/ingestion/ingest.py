"""Ingestion runner: corpus MongoDB, chunk, embed, index.

Run with::

    uv run python -m building_with_rag.ingestion.ingest
"""

import json
import time
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.operations import SearchIndexModel
from voyageai import Client as VoyageClient
from voyageai.error import APIConnectionError, RateLimitError, ServiceUnavailableError, VoyageError

from building_with_rag.ingestion import mongodb_schema as schema
from building_with_rag.settings import get_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # src/building_with_rag/ingestion *
BNS_CORPUS = _PROJECT_ROOT / "data" / "processed" / "bns_sections.jsonl"
IPC_CORPUS = _PROJECT_ROOT / "data" / "processed" / "ipc_sections.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, returning a list of dicts with a ``_record_index`` field."""
    if not path.is_file():
        raise SystemExit(f"Corpus file missing: {path.name}. Run Story 2.1 extraction first.")
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for idx, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            record["_record_index"] = idx
            records.append(record)
    if not records:
        raise SystemExit(f"Corpus file empty: {path.name}. Re-run Story 2.1 extraction.")
    return records

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------


def _check_mongodb(settings) -> MongoClient:
    # Prerequisite checks per story; stop on the first failure. Never print the URI.
    if not settings.mongodb_uri:
        raise SystemExit("MongoDB unavailable: MONGODB_URI is empty. Set it in .env and re-run.")
    if not settings.voyage_api_key:
        raise SystemExit("VOYAGE_API_KEY is empty. Set it in .env and re-run.")

    client: MongoClient = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except PyMongoError:
        raise SystemExit(
            "MongoDB unavailable: could not connect. Check MONGODB_URI and that the cluster is running."
        ) from None

    is_atlas = "mongodb.net" in settings.mongodb_uri
    if not is_atlas:
        try:
            is_atlas = "atlasVersion" in client.admin.command("serverStatus")
        except OperationFailure:
            pass
    if not is_atlas:
        raise SystemExit("Atlas Search is required for $vectorSearch. Use an Atlas cluster (M0 free tier or higher).")

    db_name = settings.mongodb_test_db_name if settings.app_env == "testing" else settings.mongodb_db_name
    try:
        list(client[db_name][schema.EMBEDDINGS_COLLECTION].list_search_indexes())
    except OperationFailure as exc:
        if exc.code == 13 or "not authorized" in str(exc).lower():
            raise SystemExit(
                "Permission missing: the connected user cannot manage search indexes. Grant a role that "
                "allows it (for example Atlas admin on the project), then re-run."
            ) from None
    return client

# ---------------------------------------------------------------------------
# Ingestion steps
# ---------------------------------------------------------------------------


def _ingest_sources(db, sources_coll: str, bns_records: list[dict], ipc_records: list[dict]) -> dict:
    """Upsert source documents. Returns {inserted, replaced, skipped}."""
    stats = {"inserted": 0, "replaced": 0, "skipped": 0}
    collection = db[sources_coll]

    datasets = [
        (bns_records, "BNS_2023", "bns_sections.jsonl"),
        (ipc_records, "IPC_1860", "ipc_sections.jsonl"),
    ]

    for records, act, filename in datasets:
        first = records[0]
        doc = schema.build_source_doc(
            act=act,
            act_label=first["act_label"],
            status=first["status"],
            source_pdf=first["source_pdf"],
            source_sha256=first["source_sha256"],
            parser=first["parser"],
            parser_version=first["parser_version"],
            corpus_path=f"data/processed/{filename}",
            section_count=len(records),
        )
        existing = collection.find_one({"_id": doc["_id"]})
        if existing is None:
            collection.insert_one(doc)
            stats["inserted"] += 1
        elif _docs_equal(existing, doc, ("updated_at",)):
            stats["skipped"] += 1
        else:
            doc["created_at"] = existing.get("created_at", doc["created_at"])
            doc["updated_at"] = schema.now_utc()
            collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
            stats["replaced"] += 1

    return stats


def _ingest_sections(db, sections_coll: str, records: list[dict]) -> dict:
    """Upsert section documents. Returns {inserted, replaced, skipped}."""
    stats = {"inserted": 0, "replaced": 0, "skipped": 0}
    collection = db[sections_coll]

    for record in records:
        doc = schema.build_section_doc(record)
        existing = collection.find_one({"_id": doc["_id"]})
        if existing is None:
            collection.insert_one(doc)
            stats["inserted"] += 1
        elif _docs_equal(existing, doc, ("updated_at",)):
            stats["skipped"] += 1
        else:
            doc["created_at"] = existing.get("created_at", doc["created_at"])
            doc["updated_at"] = schema.now_utc()
            collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
            stats["replaced"] += 1

    return stats


def _delete_removed_sections(db, records: list[dict]) -> int:
    # Remove sections no longer in the corpus, with their chunks and embeddings.
    keep = [r["section_id"] for r in records]
    gone = [s["_id"] for s in db[schema.SECTIONS_COLLECTION].find({"_id": {"$nin": keep}}, {"_id": 1})]
    if gone:
        cids = [c["_id"] for c in db[schema.CHUNKS_COLLECTION].find({"section_id": {"$in": gone}}, {"_id": 1})]
        db[schema.EMBEDDINGS_COLLECTION].delete_many({"chunk_id": {"$in": cids}})
        db[schema.CHUNKS_COLLECTION].delete_many({"section_id": {"$in": gone}})
        db[schema.SECTIONS_COLLECTION].delete_many({"_id": {"$in": gone}})
    return len(gone)


def _chunk_sections(db, sections_coll: str, chunks_coll: str) -> tuple[int, list[str]]:
    # Chunk all sections; returns (inserted+replaced count, all current chunk ids).
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=schema.CHUNK_SIZE,
        chunk_overlap=schema.CHUNK_OVERLAP,
        separators=schema.CHUNK_SEPARATORS,
    )
    chunks_c, emb_c = db[chunks_coll], db[schema.EMBEDDINGS_COLLECTION]
    current: list[str] = []
    stats = {"inserted": 0, "replaced": 0, "skipped": 0, "deleted": 0, "empty_sections": 0}

    for section in db[sections_coll].find().sort("section_id", 1):
        if not (section.get("text") or "").strip():
            stats["empty_sections"] += 1
            continue
        for idx, chunk_text in enumerate(splitter.split_text(section["text"])):
            embedding_input = f"[{section.get('act_label', '')}] {section.get('heading', '')}\n{chunk_text}"
            doc = schema.build_chunk_doc(section=section, chunk_index=idx, text=chunk_text,
                                         embedding_input=embedding_input)
            current.append(doc["_id"])
            existing = chunks_c.find_one({"_id": doc["_id"]})
            if existing is None:
                chunks_c.insert_one(doc)
                stats["inserted"] += 1
            elif any(existing.get(k) != doc[k] for k in ("text", "embedding_input", "chunking_version")):
                doc["created_at"] = existing.get("created_at", doc["created_at"])
                chunks_c.replace_one({"_id": doc["_id"]}, doc)
                emb_c.delete_many({"chunk_id": doc["_id"]})  # re-embed changed chunk
                stats["replaced"] += 1
            else:
                stats["skipped"] += 1

    stale = [c["_id"] for c in chunks_c.find({"_id": {"$nin": current}}, {"_id": 1})]
    if stale:
        emb_c.delete_many({"chunk_id": {"$in": stale}})
        chunks_c.delete_many({"_id": {"$in": stale}})
    stats["deleted"] = len(stale)

    print("  Chunks: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in stats.items()))
    return stats["inserted"] + stats["replaced"], current


VOYAGE_BATCH_SIZE = 10
VOYAGE_BATCH_DELAY = 25  # seconds; free-tier rate limit


_RETRYABLE = (RateLimitError, ServiceUnavailableError, APIConnectionError)


def _embed_with_retry(vc, texts: list[str], input_type: str = "document", attempts: int = 5):
    # Retry rate limits (HTTP 429) and transient errors with growing backoff, bounded attempts.
    for attempt in range(1, attempts + 1):
        try:
            return vc.embed(texts=texts, model=schema.EMBEDDING_MODEL, input_type=input_type).embeddings
        except _RETRYABLE as exc:
            if attempt == attempts:
                raise
            wait = 30 * attempt
            print(f"  Voyage error ({type(exc).__name__}); retry {attempt}/{attempts - 1} in {wait}s")
            time.sleep(wait)


def _embed_chunks(db, embeddings_coll: str, chunk_ids: list[str], settings) -> dict:
    stats = {"inserted": 0, "skipped": 0, "deleted": 0}
    chunks_c, emb_c = db[schema.CHUNKS_COLLECTION], db[embeddings_coll]

    # Drop embeddings of removed chunks or other model settings (before inserting new ones)
    stats["deleted"] = emb_c.delete_many({"$or": [
        {"chunk_id": {"$nin": chunk_ids}},
        {"model": {"$ne": schema.EMBEDDING_MODEL}},
        {"model_version": {"$ne": schema.EMBEDDING_MODEL_VERSION}},
        {"dimensions": {"$ne": schema.EMBEDDING_DIMENSIONS}},
    ]}).deleted_count

    have = {e["chunk_id"] for e in emb_c.find({}, {"chunk_id": 1})}
    to_embed = [c for c in chunks_c.find({"_id": {"$in": chunk_ids}}) if c["_id"] not in have]
    stats["skipped"] = len(chunk_ids) - len(to_embed)
    if not to_embed:
        print(f"  Embeddings: 0 inserted, {stats['skipped']} skipped, {stats['deleted']} deleted")
        return stats

    vc = VoyageClient(api_key=settings.voyage_api_key)
    batches = [to_embed[i:i + VOYAGE_BATCH_SIZE] for i in range(0, len(to_embed), VOYAGE_BATCH_SIZE)]
    print(f"  {len(to_embed)} chunks, {len(batches)} batches of {VOYAGE_BATCH_SIZE}, {VOYAGE_BATCH_DELAY}s pause.")
    print("  Expect roughly 35-40 minutes for ~900-1,000 chunks on a free Voyage key. "
          "Safe to interrupt and re-run.")
    for n, batch in enumerate(batches, 1):
        vectors = _embed_with_retry(vc, [c["embedding_input"] for c in batch])
        bad = [len(v) for v in vectors if len(v) != schema.EMBEDDING_DIMENSIONS]
        if bad or len(vectors) != len(batch):
            raise SystemExit(f"Voyage returned unexpected vectors (dims {bad[:1]}, count {len(vectors)}/"
                             f"{len(batch)}); expected {schema.EMBEDDING_DIMENSIONS} dimensions.")
        # Write each batch immediately so an interrupted run resumes from here.
        emb_c.insert_many([schema.build_embedding_doc(chunk=c, vector=v) for c, v in zip(batch, vectors)])
        stats["inserted"] += len(batch)
        print(f"  Embedded batch {n}/{len(batches)} ({stats['inserted']}/{len(to_embed)} total)")
        if n < len(batches):
            time.sleep(VOYAGE_BATCH_DELAY)

    print(f"  Embeddings: {stats['inserted']} inserted, {stats['skipped']} skipped, {stats['deleted']} deleted")
    return stats

# ---------------------------------------------------------------------------
# Vector search index
# ---------------------------------------------------------------------------


_REQUIRED_FILTERS = ("act", "chapter", "status", "access_level")


def _find_index(coll):
    return next((i for i in coll.list_search_indexes() if i.get("name") == schema.VECTOR_INDEX_NAME), None)


def _index_diffs(definition: dict) -> list[str]:
    fields = definition.get("fields", [])
    vec = next((f for f in fields if f.get("type") == "vector" and f.get("path") == "vector"), None)
    diffs = []
    if vec is None:
        diffs.append("no vector field on path 'vector'")
    else:
        if vec.get("numDimensions") != schema.EMBEDDING_DIMENSIONS:
            diffs.append(f"numDimensions={vec.get('numDimensions')} (expected {schema.EMBEDDING_DIMENSIONS})")
        if vec.get("similarity") != "cosine":
            diffs.append(f"similarity={vec.get('similarity')} (expected cosine)")
    have = {f.get("path") for f in fields if f.get("type") == "filter"}
    missing = [p for p in _REQUIRED_FILTERS if p not in have]
    if missing:
        diffs.append(f"missing filter fields: {missing}")
    return diffs


def _ensure_vector_index(db, embeddings_coll: str) -> str:
    # Create, reuse if compatible, or stop on a mismatch (never drop).
    coll = db[embeddings_coll]
    idx = _find_index(coll)
    if idx is not None:
        diffs = _index_diffs(idx.get("latestDefinition") or idx.get("definition") or {})
        if diffs:
            raise SystemExit(f"Vector index '{schema.VECTOR_INDEX_NAME}' differs: {'; '.join(diffs)}. "
                             "Drop it manually in the Atlas UI, then re-run.")
        if idx.get("queryable") or idx.get("status") == "READY":
            return "READY"
    else:
        coll.create_search_index(SearchIndexModel(
            name=schema.VECTOR_INDEX_NAME, type="vectorSearch",
            definition=schema.VECTOR_INDEX_DEFINITION))

    status = None
    for waited in range(5, 125, 5):
        time.sleep(5)
        idx = _find_index(coll)
        status = idx.get("status") if idx else "UNKNOWN"
        if idx and (idx.get("queryable") or status == "READY"):
            return "READY"
        print(f"  Waiting for index... ({waited}s, status: {status})")
    print(f"  Index '{schema.VECTOR_INDEX_NAME}' not READY after 120s (last status: {status}). "
          "Re-check: db.embeddings.aggregate([{$listSearchIndexes: {}}])")
    return f"TIMEOUT (last status: {status})"

# ---------------------------------------------------------------------------
# Sample query
# ---------------------------------------------------------------------------


def _sample_vector_query(db, settings, index_status: str) -> str:
    # Report errors instead of raising.
    pending = "vector query pending — index not ready"
    if index_status != "READY":
        return pending
    try:
        vc = VoyageClient(api_key=settings.voyage_api_key)
        qv = vc.embed(texts=["punishment for theft"], model=schema.EMBEDDING_MODEL,
                      input_type="query").embeddings[0]
        rows = list(db[schema.EMBEDDINGS_COLLECTION].aggregate([
            {"$vectorSearch": {
                "index": schema.VECTOR_INDEX_NAME, "path": "vector", "queryVector": qv,
                "numCandidates": 50, "limit": 1, "filter": {"access_level": "public"}}},
            {"$project": {"_id": 0, "chunk_id": 1, "score": {"$meta": "vectorSearchScore"}}},
        ]))
    except VoyageError as exc:
        return f"error: Voyage query embedding failed: {exc}"
    except PyMongoError as exc:
        return pending if "not ready" in str(exc).lower() else f"error: {exc}"
    if not rows:
        return pending
    chunk = db[schema.CHUNKS_COLLECTION].find_one({"_id": rows[0]["chunk_id"]}) or {}
    section = db[schema.SECTIONS_COLLECTION].find_one({"_id": chunk.get("section_id")}) or {}
    return (f"chunk_id={rows[0]['chunk_id']} section_id={chunk.get('section_id')} "
            f"heading={section.get('heading')!r} score={rows[0]['score']:.4f}")

# ---------------------------------------------------------------------------
# Doc comparison
# ---------------------------------------------------------------------------

_EXCLUDE_KEYS = frozenset({"updated_at", "created_at"})


def _docs_equal(existing: dict, new_doc: dict, extra_skip: tuple[str, ...] = ()) -> bool:
    """Return True if *existing* and *new_doc* are equivalent for upsert purposes."""
    skip = _EXCLUDE_KEYS | frozenset(extra_skip)
    for key, value in new_doc.items():
        if key in skip:
            continue
        if existing.get(key) != value:
            return False
    return True

# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run(settings=None):
    """Execute the full ingestion pipeline."""
    if settings is None:
        settings = get_settings()

    print("=== MongoDB RAG Ingestion ===")
    print()

    # 1. Check MongoDB
    print("1. Checking MongoDB prerequisites...")
    client = _check_mongodb(settings)
    db_name = settings.mongodb_db_name if settings.app_env != "testing" else settings.mongodb_test_db_name
    db = client[db_name]
    print(f"   Connected to {db_name}; MongoDB {client.server_info().get('version', 'unknown')}; Atlas: yes")
    print()

    # 2. Read corpus
    print("2. Loading corpus...")
    bns_records = _load_jsonl(BNS_CORPUS)
    ipc_records = _load_jsonl(IPC_CORPUS)
    all_records = bns_records + ipc_records
    print(f"   BNS: {len(bns_records)} sections, IPC: {len(ipc_records)} sections")
    print()

    # 3. Ordinary indexes
    print("3. Ensuring ordinary indexes...")
    schema.ensure_ordinary_indexes(db)
    print("   Done.")
    print()

    # 4. Ingest sources
    print("4. Ingesting sources...")
    source_stats = _ingest_sources(db, schema.SOURCES_COLLECTION, bns_records, ipc_records)
    print(f"   {source_stats}")
    print()

    # 5. Ingest sections
    print("5. Ingesting sections...")
    section_stats = _ingest_sections(db, schema.SECTIONS_COLLECTION, all_records)
    print(f"   {section_stats}")
    removed = _delete_removed_sections(db, all_records)
    if removed:
        print(f"   Removed {removed} sections no longer in corpus")
    print()

    # 6. Chunk sections
    print("6. Chunking sections...")
    chunk_count, all_chunk_ids = _chunk_sections(db, schema.SECTIONS_COLLECTION, schema.CHUNKS_COLLECTION)
    print()

    # 7. Embed chunks
    print("7. Embedding chunks...")
    embed_stats = _embed_chunks(db, schema.EMBEDDINGS_COLLECTION, all_chunk_ids, settings)
    print()

    # 8. Vector index
    print("8. Vector search index...")
    index_status = _ensure_vector_index(db, schema.EMBEDDINGS_COLLECTION)
    print(f"   Index status: {index_status}")
    print()

    # 9. Sample query
    print("9. Sample vector query...")
    query_result = _sample_vector_query(db, settings, index_status)
    print(f"   {query_result}")
    print()

    # 10. Verification
    print("10. Lightweight verification...")
    _print_verification(db, chunk_count, embed_stats)
    print()

    print("=== Ingestion complete ===")

    return {
        "source_stats": source_stats,
        "section_stats": section_stats,
        "chunk_count": chunk_count,
        "embed_stats": embed_stats,
        "index_status": index_status,
        "query_result": query_result,
    }


def _print_verification(db, chunk_count: int, embed_stats: dict):
    # Classroom-sized checks: counts, section link, vector length.
    counts = {n: db[n].count_documents({}) for n in (
        schema.SOURCES_COLLECTION, schema.SECTIONS_COLLECTION,
        schema.CHUNKS_COLLECTION, schema.EMBEDDINGS_COLLECTION)}
    print("   Collections: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"   embeddings == chunks: {counts['embeddings'] == counts['chunks']}")
    bns1 = list(db[schema.CHUNKS_COLLECTION].find({"section_id": "bns:1"}, {"section_id": 1}))
    print(f"   bns:1 chunks: {len(bns1)} (all linked: {all(c['section_id'] == 'bns:1' for c in bns1)})")
    sample = db[schema.EMBEDDINGS_COLLECTION].find_one({}, {"vector": 1})
    print(f"   Sample vector length: {len(sample['vector']) if sample else 'none'} (expected 1024)")


if __name__ == "__main__":
    run()