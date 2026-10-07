# Story 2.2 — MongoDB, Chunks, Embeddings, and Vector Index

Third story of the three-day "Building Intelligence with RAG" classroom course. Follows Story 2.1. Loads the section-level JSONL corpus into MongoDB, chunks section text, embeds every chunk with Voyage, and creates an Atlas vector-search index. Retrieval endpoints, answer generation, hybrid search, and re-ranking belong to later stories.

## Purpose

Turn the corpus into searchable evidence in one MongoDB database. Produce four collections — `sources`, `sections`, `chunks`, `embeddings` — and a `vectorSearch` index on `embeddings.vector`. Do not add retrieval endpoints, answer generation, hybrid search, re-ranking, or a separate vector store.

## Prerequisites

- Story 2.1 complete: `data/processed/bns_sections.jsonl` and `data/processed/ipc_sections.jsonl` exist.
- Story 1.1 complete: project seeded, `uv sync` works, `.env.example` exists, "Fixed embedding choices" recorded in `docs/architecture.md` (`voyage-3.5`, version `voyage-3.5`, 1,024 dimensions).
- `.env` (untracked) holds `MONGODB_URI` (Atlas free M0 cluster), `MONGODB_DB_NAME`, `MONGODB_TEST_DB_NAME`, `VOYAGE_API_KEY`. Use the existing `Settings`; do not rename, remove, or add variables. `APP_ENV=testing` selects `MONGODB_TEST_DB_NAME`; otherwise use `MONGODB_DB_NAME`.
- Never print or log credentials.

### Prerequisite check (runs first; stop on the first failure)

1. `MONGODB_URI` empty → stop: `MongoDB unavailable: MONGODB_URI is empty. Set it in .env and re-run.`
2. `VOYAGE_API_KEY` empty → stop: `VOYAGE_API_KEY is empty. Set it in .env and re-run.`
3. `ping` fails → stop: `MongoDB unavailable: could not connect. Check MONGODB_URI and that the cluster is running.` (do not print the URI).
4. Identify the deployment: record MongoDB version (`buildInfo`) and whether it is Atlas (`serverStatus` contains `atlasVersion`, or the host ends in `mongodb.net`). If not Atlas → stop: `Atlas Search is required for $vectorSearch. Use an Atlas cluster (M0 free tier or higher).`
5. Confirm the user can manage search indexes: `list_search_indexes()` on `embeddings` must not raise an authorization error. If it does → stop: `Permission missing: the connected user cannot manage search indexes. Grant a role that allows it (for example Atlas admin on the project), then re-run.`

Record the detected version and capability in the handover. Do not fall back to `$text`, `$geoNear`, or client-side similarity.

## Work to do

**Do not execute the ingestion runner, and do not call Voyage or write to MongoDB.** Write the code only. The developer runs it (see "Developer runs this"). Adding the dependencies with `uv add` is allowed; running `ingest` is not.

### 1. Dependencies

```bash
uv add voyageai langchain-text-splitters
```

Add `langchain-text-splitters` explicitly even if it arrives transitively. No other new dependencies unless needed. Record resolved versions in the handover.

### 2. Schema module — `src/building_with_rag/ingestion/mongodb_schema.py`

If `ingestion/` files already exist from an earlier attempt, reconcile them with this story instead of duplicating. Constants, validators, deterministic IDs, document builders, ordinary indexes, and the vector index definition live here. No Voyage or embedding calls.

Constants: `SOURCES_COLLECTION="sources"`, `SECTIONS_COLLECTION="sections"`, `CHUNKS_COLLECTION="chunks"`, `EMBEDDINGS_COLLECTION="embeddings"`, `VECTOR_INDEX_NAME="vector_index"`, `CHUNKING_VERSION="v1"`, `CHUNK_SIZE=2048`, `CHUNK_OVERLAP=256`, `EMBEDDING_MODEL="voyage-3.5"`, `EMBEDDING_MODEL_VERSION="voyage-3.5"`, `EMBEDDING_DIMENSIONS=1024`, `DEFAULT_ACCESS_LEVEL="public"`.

Validators (plain Python, raise `ValueError`): `validate_act` (`BNS_2023` / `IPC_1860`), `validate_status` (`in_force` / `repealed`), `validate_access_level` (`public`), `validate_section_id` (`bns:<int>` / `ipc:<int>`). Helpers: `ensure_deterministic_id(*parts)` (SHA-256 hex of parts joined with `:`) and `now_utc()` (timezone-aware UTC).

Deterministic IDs:
- `chunk_id = sha256(f"{section_id}:{chunk_index}:{chunking_version}")[:24]`
- `embedding_id = sha256(f"emb:{chunk_id}:{model}:{model_version}:{dimensions}")[:24]`

#### Collections

All four collections live in the same database. `_id` equals the named ID field.

**`sources`** — one per act PDF. `_id` (`bns_source` / `ipc_source`), `source_pdf`, `source_sha256`, `act`, `act_label`, `status`, `parser`, `parser_version`, `corpus_path`, `section_count`, `created_at`, `updated_at`. Index: unique `act`.

**`sections`** — one per corpus record. `_id`/`section_id` (e.g. `bns:1`), `act`, `act_label`, `status`, `chapter`, `chapter_title`, `section_number`, `heading`, `text`, `source_id` (→ `sources._id`), `source_pdf`, `source_sha256`, `parser`, `parser_version`, `source_status_version`, `needs_review`, `access_level`, `provenance` (`corpus_file`, `record_index`), `created_at`, `updated_at`. Indexes: unique `(act, section_number)`, `access_level`, `source_id`.

**`chunks`** — `_id`/`chunk_id`, `section_id`, `chunk_index` (zero-based), `chunking_version`, `text` (raw chunk), `embedding_input`, `act`, `chapter`, `status`, `access_level`, `created_at`. Indexes: `section_id`, `(act, chunk_index)` (non-unique), `access_level`.

**`embeddings`** — `_id`/`embedding_id`, `chunk_id`, `model`, `model_version`, `dimensions`, `vector` (list of 1,024 floats), `act`, `chapter`, `status`, `access_level`, `created_at`. Indexes: unique `chunk_id`, `(act, access_level)`. Vectors are never stored on `chunks`.

`act`, `chapter`, `status`, and `access_level` are copied onto `embeddings` so the vector index can filter without a join. Section number and source identifiers are not copied; later retrieval applies them after resolving `chunk_id` to `chunks` and `sections`.

#### Chunking rules

- Split each section's `text` with `langchain_text_splitters.RecursiveCharacterTextSplitter`, `chunk_size=2048` (~512 tokens), `chunk_overlap=256`, separators `["\n\n", "\n", ". ", " ", ""]`.
- A chunk never crosses a section boundary, so it never mixes acts or sections.
- Skip sections with empty text and report how many.
- `embedding_input` = `f"[{act_label}] {heading}\n{chunk_text}"`; `text` keeps the raw chunk.
- Bump `CHUNKING_VERSION` whenever splitter settings or the `embedding_input` format change.

#### Vector index — `vector_index` on `embeddings`

Create an Atlas `vectorSearch`-type index with a `fields` array. Do **not** use the legacy `mappings` / `knnVector` syntax (that belongs to `search`-type indexes). Use PyMongo's `SearchIndexModel(name=..., type="vectorSearch", definition=...)` with `collection.create_search_index`:

```json
{
  "fields": [
    {"type": "vector", "path": "vector", "numDimensions": 1024, "similarity": "cosine"},
    {"type": "filter", "path": "act"},
    {"type": "filter", "path": "chapter"},
    {"type": "filter", "path": "status"},
    {"type": "filter", "path": "access_level"}
  ]
}
```

Field names must match the schema exactly. The `embeddings` collection must exist before the index is created (the ordinary indexes in step 3 guarantee this).

### 3. Ingestion runner — `src/building_with_rag/ingestion/ingest.py`

Run with `uv run python -m building_with_rag.ingestion.ingest`. Steps, with compact progress output (never print vectors or full text):

1. Load settings; run the prerequisite check.
2. Read both JSONL files; stop with a clear message if a file is missing or empty.
3. Create the ordinary indexes (idempotent).
4. **Sources:** upsert one per act. Identical (ignoring `updated_at`) → skip; different (for example new SHA-256) → replace, keeping `created_at`.
5. **Sections:** same upsert rule per `section_id`. Sections in the database but absent from the corpus are deleted along with their chunks and embeddings; report the count.
6. **Chunks:** build chunks for every section. Insert new chunks; replace chunks whose `text`, `embedding_input`, or `chunking_version` differ; skip identical ones. When a chunk is inserted or replaced, delete its existing embedding so it is re-embedded. Then delete chunks whose ID is not in the current set (old `chunking_version`, or a section that now yields fewer chunks). Report inserted / replaced / skipped / deleted.
7. **Embeddings:** embed chunks that have no embedding with the current `embedding_id`, using `voyageai.Client.embed(texts, model="voyage-3.5", input_type="document")`.
   - Voyage keys are rate limited. Send small batches (start with 10 chunks) with a pause between batches (start with ~25 s; tune to the key's limits) and retry on rate-limit errors (HTTP 429) with backoff and a bounded number of attempts.
   - Print progress per batch and write each batch to MongoDB as soon as it returns, so an interrupted run resumes by skipping existing embeddings.
   - Expected duration on a free key: roughly 35–40 minutes for ~900–1,000 chunks. Say so in the console before starting.
   - After embedding, delete embeddings whose `chunk_id` no longer exists or whose `model`, `model_version`, or `dimensions` differ from the current values. Check each returned vector has length 1,024; stop if not.
8. **Vector index:**
   - Absent → create.
   - Present and compatible (type `vector` on `vector` with `numDimensions` 1024 and `cosine`, and `filter` entries for all four fields; read `latestDefinition`) → reuse.
   - Present but different → report the exact differences and stop. Never delete or replace it; tell the user to drop it manually in the Atlas UI.
   - Wait for `READY`/`queryable` for at most 120 s. On timeout, report the index name and last-known status and how to re-check (`db.embeddings.aggregate([{$listSearchIndexes: {}}])` in mongosh), then continue.
9. **Sample query:** embed `"punishment for theft"` with `input_type="query"` and run one `$vectorSearch` on `vector_index` (`path: "vector"`, `numCandidates: 50`, `limit: 1`, `filter: {"access_level": "public"}`), projecting `chunk_id` and `{$meta: "vectorSearchScore"}`. Resolve `chunk_id` through `chunks` and `sections`, and print `chunk_id`, `section_id`, heading, and score. If the index is not ready, print `vector query pending — index not ready`. A Voyage or MongoDB error here is reported, not raised.
10. **Verification summary:** collection counts; chunks of `bns:1` all carry `section_id == "bns:1"`; one embedding has `len(vector) == 1024`; `embeddings` count equals `chunks` count.

### 4. Safe re-run

A second run with unchanged inputs must report everything skipped, make no Voyage calls, and create no duplicates. Changed text, chunking version, or model settings are replaced and reported (never mixed silently). If model or dimensions change, the index check in step 8 must detect the mismatch and stop.

### 5. Update `docs/architecture.md`

Add or rewrite a section after "Corpus" (replace any earlier MongoDB section so it matches what is implemented) covering: the four collections and key fields; chunking (library, size, overlap, version); embedding decision and ID formulas; `vector_index` definition (`vectorSearch`, `fields` array, cosine, filter fields); the run command; the sample-query result; limitations — Atlas required, single `access_level`, re-ingest is the only update path, the index must be dropped manually to change dimensions or similarity, no hybrid index fields.

### 6. Contracts preserved

Unchanged: `.env.example`, `settings.py`, `contracts.py` (`RetrievedChunk`, `QueryRequest`, `QueryResult`), `registry.py`, `routes/`. No new endpoints. Later stories query `embeddings` with `$vectorSearch`, resolve each `chunk_id` to `chunks` and `sections`, and produce `RetrievedChunk` results.

## Completion checks (code only — no database or Voyage calls)

1. `uv run python -c "import building_with_rag.ingestion.ingest"` succeeds (module imports cleanly).
2. Constants in `mongodb_schema.py` match this story exactly (collection names, `vector_index`, `v1`, 2048/256, `voyage-3.5`, 1024, `public`).
3. The vector index definition is `type="vectorSearch"` with the `fields` array above — no `mappings`/`knnVector`.
4. Deterministic ID helpers produce the documented 24-character formulas.
5. The runner's prerequisite check, re-run (skip/replace) logic, batching with pauses, and 429 retry are all present in the code.
6. `uv run ruff check src/building_with_rag/ingestion` passes.

Run only checks specific to this story; do not run the full test suite.

## Developer runs this (not the implementer)

The developer executes these and confirms the results. Expect ~35–40 minutes on a free Voyage key.

1. `uv run python -m building_with_rag.ingestion.ingest` completes.
2. Collections: `sources` = 2, `sections` = corpus total (858 with the supplied PDFs), `chunks` > 0, `embeddings` = `chunks`.
3. `bns:1` has one or more chunks with `section_id == "bns:1"`; a sampled vector has length 1,024.
4. `vector_index` is `READY`, or its last-known status is reported.
5. Sample `$vectorSearch` prints `chunk_id`, `section_id`, heading, and score — or reports pending.
6. A second run skips everything and embeds nothing.

## Handover

Record: files created or changed; dependency versions resolved by `uv add`; results of the code-only completion checks; and the updated architecture section. Leave the counts, MongoDB version, index status, and sample-query result blank for the developer to fill after they run the ingestion. State plainly that the ingestion was not executed.

Story report (per project instructions): `Completed.`, changed paths, test results — at most 7 lines, ending with the full-suite command (`uv run pytest`) for the developer to run if wanted.