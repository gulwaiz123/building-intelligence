# Story 4.1 — Hybrid Search

Seventh story of the "Building Intelligence with RAG" course. Follows Story 3.2. Adds the explicitly selectable `hybrid` mode: MongoDB keyword candidates on the stored chunk text are fused with the existing semantic candidates, so exact legal terms and section language complement semantic similarity. Re-ranking, structured, decomposition, and HyDE belong to later stories.

## Purpose

`pattern: "hybrid"` (model `rag-hybrid`) on `POST /v1/query` and `/v1/chat/completions` returns `QueryResult.results` built from two routes over the **same** corpus, MongoDB database, chunks, and embeddings as semantic: a vector route and an Atlas Search keyword route, merged by one documented fusion rule. Context assembly, citations, confidence, and streaming are the existing shared path (`pipeline.retrieve` + `answer_events`), unchanged. The selected mode decides the route; there is no automatic routing. Semantic mode stays exactly as it is.

Reuse, do not rename or replace: `QueryRequest`, `QueryResult`, `RetrievedChunk`, `GenerationResult`, `Pattern`/`rag-<pattern>` registry, `/v1/models`, the four collections, `vector_index`, `voyage-3.5`/1,024 dims, and every `.env.example` name. Add no environment variables, endpoints, top-level `QueryResult` fields, dependencies, or second application. Do not change the embedding model or re-run ingestion/embedding. Scores rank only; they do not prove correctness.

## Prerequisites

- Stories 1.1, 2.1–2.3, 3.1, 3.2 complete. Read `docs/architecture.md` first.
- `.env` (untracked) as in Story 3.2. Never print or log credentials or URLs.

### Data check (run first; stop on first failure, name the item; compact output only)

1. Yes/no only: `MONGODB_URI`, `VOYAGE_API_KEY`, `GENERATION_API_BASE_URL`, `GENERATION_API_KEY`.
2. Counts only: `chunks` > 0 and equals `embeddings`; `vector_index` is queryable; one semantic `/v1/query` (`limit: 3`) returns `status: "ok"`.
3. **Keyword-search capability (decides whether this story can proceed):** the configured deployment is Atlas (Story 2.2 recorded it; M0 or higher supports Atlas Search). Confirm `chunks.list_search_indexes()` runs without an authorization error (name/status only). Record the MongoDB version and "Atlas Search available: yes/no".

If item 3 is "no": report the prerequisite (Atlas Search on the configured cluster, plus permission to manage search indexes), make no code changes, and leave semantic mode and the `rag-hybrid` placeholder unchanged. Do not fall back to `$text`, regex, or client-side keyword scoring.

## Fixed keyword-search choice

- Mechanism: Atlas Search — a `search`-type index and the `$search` aggregation stage with the `text` operator (BM25 scoring via `{$meta: "searchScore"}`). This is not the `vectorSearch` index and not `$text`.
- Searched field: `chunks.text` (the raw stored chunk text). `embedding_input`/`heading` are not searched.
- Index: name `chunk_text_index` on collection `chunks`, defined with PyMongo `SearchIndexModel(name=..., type="search", definition=...)`:

```json
{"mappings": {"dynamic": false, "fields": {
  "text": {"type": "string", "analyzer": "lucene.standard"},
  "act": {"type": "token"},
  "status": {"type": "token"},
  "access_level": {"type": "token"}
}}}
```

- Filters (`act`, `status`, `access_level`) are applied inside `$search.compound.filter` with the `in` operator on the token fields, using the same effective filters as semantic (server-fixed `access_level=["public"]`; caller lists only narrow). These fields already exist on `chunks`; no schema or data change.
- The question is passed only as the `text.query` value, never as an operator or pipeline fragment.

## Fusion rule (fixed, documented in architecture)

Reciprocal Rank Fusion over ranks only (raw BM25 and cosine scores are not comparable):

- Each route returns its top `ROUTE_DEPTH = max(limit, min(50, max(20, 4 * limit)))` chunks, ranked from 1. Semantic route reuses the Story 2.3 embedding/`$vectorSearch` code with `limit = ROUTE_DEPTH` and its `numCandidates` rule.
- `fused_score = Σ 1 / (RRF_K + rank)` over the routes that returned the chunk; `RRF_K = 60`; equal weights.
- Order: `fused_score` desc, ties by `semantic_rank` (missing last), then `chunk_id`. Return the top `limit`; `fused_rank` is 1-based over the full fused list, so it equals the position in `results`.
- Chunks (not sections) are fused by `chunk_id`; a chunk found by both routes appears once.

Limitation to record: rank-only fusion ignores score magnitude; the `text` operator matches any query term (OR), so long natural-language questions can pull in common words; section numbers match only when they appear inside chunk `text`; no stemming/synonyms beyond the standard analyzer.

## Work to do

### 1. Keyword index (additive)

- `ingestion/mongodb_schema.py`: add `KEYWORD_INDEX_NAME = "chunk_text_index"` and `KEYWORD_INDEX_DEFINITION` (above). Do not change existing constants or ingestion behavior.
- New `src/building_with_rag/ingestion/keyword_index.py`, run with `uv run python -m building_with_rag.ingestion.keyword_index`: idempotent — absent → create; present with matching definition → reuse; present but different → report differences and stop (drop manually in Atlas, never auto-replace); wait up to 120 s for `READY`/queryable; print name and status only. No Voyage calls, no data writes. Existing ingestion is untouched, so `ingest` still ignores this index.

### 2. Hybrid retrieval (new `src/building_with_rag/retrieval/hybrid.py`)

- `run_hybrid(request) -> QueryResult`, reusing from `semantic.py`: `QueryError`, client helpers, `effective_filters`, `_check_scope` (`required_acts`/`chapter`/foreign `caller_id` rejected as 422; adjust message text only if it names "semantic"), `_chunk_result`, the query-embedding/vector-search step, and the `$in`-filter construction. Do not change semantic behavior or its readiness cache; hybrid keeps its own.
- Keyword route: `chunks.aggregate([{$search: {index, compound: {must: [{text: {query: question, path: "text"}}], filter: [{in: {path, value}} ...]}}}, {$limit: ROUTE_DEPTH}, {$project: {_id: 0, chunk_id: 1, section_id: 1, score: {$meta: "searchScore"}}}])`. Resolve sections as semantic does; unresolved hits are omitted and counted.
- Pure function `fuse(semantic_hits, keyword_hits, limit)` holding the rule above (importable for tests, no I/O).
- Readiness: credentials set, `vector_index` and `chunk_text_index` both queryable, embeddings non-empty. Missing keyword index or not ready → HTTP 503 `retrieval_not_ready` with a one-line message naming the keyword-index command. Never silently degrade to semantic-only. Voyage/MongoDB failure → 502 `retrieval_upstream_error`. No URIs or keys in messages.
- Outcomes: `ok` (fused passages; one route returning nothing is fine), `no_results` (both routes empty), failures as above.

### 3. Contract (additive, `contracts.py`)

`QueryResult` unchanged. Add to `RetrievedChunk` only, all optional, default `None`: `semantic_score`, `semantic_rank`, `keyword_score`, `keyword_rank`, `fused_score`, `fused_rank`. In hybrid, `score` = `fused_score`. A route that did not return the chunk leaves its score and rank `None` — that is how the contributing route is read per passage. Semantic mode leaves all six `None`. No other new fields.

### 4. Evidence visibility (`QueryResult.trace` + `message`)

Hybrid `trace` (no vectors, no secrets): `mode: "hybrid"`, `query`, `embedding` (as semantic), `filters`, `caller_id`, `result_count`, `unresolved_hits`, `semantic` (`index`, `limit`, `num_candidates`, `hit_count`), `keyword` (`index`, `path: "text"`, `operator: "text"`, `limit`, `hit_count`), `fusion` (`method: "rrf"`, `k`, `weights`, `route_depth`), `contribution` (`both`, `semantic_only`, `keyword_only` counts among returned results). `message` states that the fused score ranks only and does not prove correctness. The compact diagnostic below must be enough to see which route found each passage before any generation.

### 5. Routing and shared path

- `pipeline.retrieve`: `HYBRID` → `run_hybrid`; `SEMANTIC` unchanged; other modes still `run_pattern` placeholders.
- `pipeline.answer_events`/`attach_generation` and `routes/chat.py::_pieces` currently special-case `semantic`; make them treat `hybrid` identically via one small shared set of real patterns (no duplicated flow). Update the `context.py` docstring if it says semantic-only. No change to context constants, prompts, validation, confidence, footer labels, `MAX_ATTEMPTS`, or streaming framing.
- `registry.py` entries are unchanged; `/v1/models` still lists six IDs; `rag-hybrid` is chosen explicitly in Open WebUI or `/v1/query`. No automatic routing and no fallback between modes.

### 6. Docs

- `docs/architecture.md`: add "Hybrid retrieval (Story 4.1)" — keyword mechanism, field `chunks.text`, `chunk_text_index` definition and creation command, RRF rule and constants, new `RetrievedChunk` fields, trace fields, outcomes, limitation (above), diagnostic command. Fix: the line saying only semantic is real, the registry/endpoint/chat statements, and Limitations ("Atlas required; no `$text`…" stays true; replace "No keyword/hybrid index fields yet" with the Atlas Search index on `chunks`).
- `docs/manual-tests.md`: replace the Story 1.1 `/v1/query` hybrid placeholder entry with real hybrid; change the Story 1.1 chat placeholder checks (`rag-hybrid`) to `rag-hybrid-reranked`; add a compact Story 4.1 section. Update `tests/test_smoke.py` placeholder cases that use `rag-hybrid`/`hybrid` to `hybrid-reranked`.

## Completion checks (compact; never print passages, vectors, or whole responses)

Diagnostic form (text truncated):

```bash
curl -s http://127.0.0.1:8000/v1/query -H "Content-Type: application/json" \
  -d '{"question":"<q>","pattern":"hybrid","limit":5}' \
  | jq '{status, t: (.trace | {semantic, keyword, fusion, contribution}), r: [.results[] | {section_id, score, sr: .semantic_rank, kr: .keyword_rank, fr: .fused_rank, text: .text[:60]}]}'
```

1. **Index:** `uv run python -m building_with_rag.ingestion.keyword_index` ends with `chunk_text_index` queryable; a second run reuses it. Report name/status only.
2. **Hybrid works:** a question returns `status: "ok"`, ≤ `limit` results in non-increasing `score`; `score == fused_score`, `fused_rank` = position; every result has at least one of `semantic_rank`/`keyword_rank`; trace shows both routes, fusion, contribution counts.
3. **Comparison (required, small):** run one **paraphrased** question (e.g. "What is the penalty for taking someone's belongings without permission?") and one **exact legal term** question (a phrase that appears in chunk text, e.g. "criminal breach of trust"; a section-number question only if the number appears in `text`) in `semantic` and in `hybrid`, each with `generate_answer: true` and `limit: 5`. Per run record compactly: `status`, section IDs in order, which route(s) found each hybrid passage, and `generation | {outcome, confidence, text[:200]}`. Write 3–5 lines on what differed (sources, order, answer) for each question. Do not claim hybrid or semantic is better in general; report only what was observed. Read one cited passage (truncated) to confirm it supports its claim.
4. **Shared path:** `rag-hybrid` via chat (stream, `head -c 1500`) shows the DRAFT/confidence/Sources behavior and agrees with `/v1/query` on outcome and citations; `rag-semantic` behaves as before (one request, same outcome class as in Story 3.2).
5. **Failure ≠ no_results:** with the keyword index missing/not ready, hybrid returns HTTP 503 `retrieval_not_ready` while semantic still returns `ok`. If the index cannot be dropped safely, run this check only by the offline test below and say so.
6. **Offline tests** (`tests/test_hybrid_retrieval.py`, no network, few cases): (a) `fuse` ranks a chunk in both routes above single-route chunks and sets both ranks and a correct RRF score; (b) single-route chunks leave the other route's score/rank `None`; (c) tie order is deterministic; (d) `run_hybrid` with faked searches sets `score == fused_score` and the six fields, and a missing keyword index raises `retrieval_not_ready`. Update the affected smoke placeholder cases.
7. `GET /v1/models` lists six IDs; `hybrid-reranked`, `structured`, `decomposition`, `hyde` still return `not_implemented`.
8. `uv run ruff check src/building_with_rag` passes.

Run only this story's checks (the new test file, edited smoke cases, ruff). Do not run the full suite. Add no re-ranking, evaluation harness, regression suite, routing, GraphRAG, or agentic behavior. If live prerequisites (`MONGODB_URI`, `VOYAGE_API_KEY`, `GENERATION_*`, Atlas Search) are unavailable, run only checks 6–8 and report the blocked prerequisite by name.

## Handover

Record: files created/changed; data-check results (yes/no, counts, MongoDB version, Atlas Search available); the keyword index name, field, and definition; commands run; the fusion constants (`RRF_K`, `ROUTE_DEPTH` rule); compact diagnostics for checks 2 and 4; the comparison notes from check 3 (both questions, both modes, observed differences only) and the cited passage checked; the architecture sections updated; any deviation from this story.

Story report (per project instructions): `Completed.`, changed paths, test results — at most 7 lines, ending with the full-suite command (`uv run pytest`) for the developer to run if wanted.