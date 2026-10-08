"""Create (or reuse) the Atlas Search keyword index on chunks.text for hybrid retrieval.

Run with::

    uv run python -m building_with_rag.ingestion.keyword_index

Idempotent: absent -> create; matching -> reuse; different -> report and stop (never replaced).
Writes no data and makes no Voyage calls. Prints only the index name and status.
"""

import time

from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.operations import SearchIndexModel

from building_with_rag.ingestion import mongodb_schema as schema
from building_with_rag.settings import get_settings

WAIT_SECONDS = 120


def index_differences(latest: dict) -> list[str]:
    """Differences between a live index definition and KEYWORD_INDEX_DEFINITION."""
    expected = schema.KEYWORD_INDEX_DEFINITION["mappings"]
    mappings = latest.get("mappings") or {}
    diffs = []
    if bool(mappings.get("dynamic", False)) != expected["dynamic"]:
        diffs.append(f"mappings.dynamic is {mappings.get('dynamic')!r}, expected False")
    live_fields = mappings.get("fields") or {}
    for name, want in expected["fields"].items():
        got = live_fields.get(name)
        if not isinstance(got, dict):
            diffs.append(f"field '{name}' is missing")
            continue
        for key, value in want.items():
            if got.get(key) != value:
                diffs.append(f"field '{name}'.{key} is {got.get(key)!r}, expected {value!r}")
    for name in live_fields.keys() - expected["fields"].keys():
        diffs.append(f"unexpected field '{name}'")
    return diffs


def _find(coll) -> dict | None:
    return next(iter(coll.list_search_indexes(schema.KEYWORD_INDEX_NAME)), None)


def main() -> int:
    settings = get_settings()
    if not settings.mongodb_uri:
        print("MongoDB unavailable: MONGODB_URI is empty. Set it in .env and re-run.")
        return 1
    client: MongoClient = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except PyMongoError:
        print("MongoDB unavailable: could not connect. Check MONGODB_URI and that the cluster is running.")
        return 1
    db_name = settings.mongodb_test_db_name if settings.app_env == "testing" else settings.mongodb_db_name
    coll = client[db_name][schema.CHUNKS_COLLECTION]
    try:
        existing = _find(coll)
        if existing is None:
            coll.create_search_index(SearchIndexModel(
                name=schema.KEYWORD_INDEX_NAME, type="search",
                definition=schema.KEYWORD_INDEX_DEFINITION,
            ))
            print(f"{schema.KEYWORD_INDEX_NAME}: created")
        else:
            diffs = index_differences(existing.get("latestDefinition") or {})
            if diffs:
                print(f"{schema.KEYWORD_INDEX_NAME}: exists with a different definition:")
                for diff in diffs:
                    print(f"  - {diff}")
                print("Drop it manually in the Atlas UI, then re-run. It is never replaced automatically.")
                return 1
            print(f"{schema.KEYWORD_INDEX_NAME}: reusing existing index")
        deadline = time.monotonic() + WAIT_SECONDS
        status = "unknown"
        while time.monotonic() < deadline:
            current = _find(coll) or {}
            status = current.get("status", "unknown")
            if current.get("queryable") or status == "READY":
                print(f"{schema.KEYWORD_INDEX_NAME}: {status} (queryable)")
                return 0
            time.sleep(5)
        print(f"{schema.KEYWORD_INDEX_NAME}: last-known status {status}; not queryable within "
              f"{WAIT_SECONDS}s. Re-check with db.chunks.aggregate([{{$listSearchIndexes: {{}}}}]) in mongosh.")
        return 0
    except OperationFailure as exc:
        if exc.code == 13 or "not authorized" in str(exc).lower():
            print("Permission missing: the connected user cannot manage search indexes. "
                  "Grant a role that allows it, then re-run.")
        else:
            print(f"Atlas Search error ({type(exc).__name__}); Atlas Search is required.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())