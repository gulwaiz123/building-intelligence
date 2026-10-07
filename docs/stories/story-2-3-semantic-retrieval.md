# Story 2.3 — Semantic Retrieval

Fourth story of the three-day "Building Intelligence with RAG" classroom course. Follows Story 2.2. Implements the first real course mode, `semantic`: embed the question, run a MongoDB vector search, and return source passages. Answer generation, Open WebUI answer behavior, hybrid search, re-ranking, and evaluation belong to later stories.

## Purpose

Make `POST /v1/query` with `pattern: "semantic"` return ranked source passages in `QueryResult.results`, with enough diagnostics to inspect what was asked and what came back. A similarity score only ranks passages; it is not proof that a passage is correct or answers the question.

Reuse, do not rename or replace: `QueryRequest`, `QueryResult`, `RetrievedChunk`, `SemanticFilters`, the `Pattern` registry and `rag-<pattern>` model IDs, the four MongoDB collections and `vector_index` from Story 2.2, and the `.env.example` names (`MONGODB_URI`, `MONGODB_DB_NAME`, `MONGODB_TEST_DB_NAME`, `VOYAGE_API_KEY`, `WEBUI_DEMO_CALLER_ID`, `APP_ENV`). Add no environment variables, endpoints, or top-level `QueryResult` fields. `docs/config.yaml` does not exist, so the repository root is the project path.

## Prerequisites

- Stories 1.1, 2.1, and 2.2 complete. Read `docs/architecture.md` first.
- `.env` (untracked) has `MONGODB_URI` and `VOYAGE_API_KEY`. Never print or log credentials.

### Data check (run first; stop on the first failure and say which item failed)

1. Corpus: `data/processed/bns_sections.jsonl` (358) and `ipc_sections.jsonl` (500) exist.
2. `sections` = 858, `chunks` > 0.
3. `embeddings` count equals `chunks` count; sampled embedding has `model`/`model_version` `voyage-3.5`, `dimensions` 1024, and a vector of length 1024.
4. `vector_index` on `embeddings` is `READY`/queryable (`list_search_indexes()`).

If any fail, point the developer to Story 2.2 (`uv run python -m building_with_rag.ingestion.ingest`) instead of working around it.

## Work to do

### 1. Retrieval module (new, e.g. `src/building_with_rag/retrieval/semantic.py`)

- Constants for model, version, dimensions, collection names, and index name come from `ingestion/mongodb_schema.py`. Do not duplicate them or modify the ingestion files.
- Create the MongoDB and Voyage clients lazily, once, with short bounded timeouts. The app must still start and serve `/healthz` with no credentials.
- Embed the raw question with `voyage-3.5`, `input_type="query"`, and no overrides of dimension or truncation (documents were embedded with the same model and defaults, `input_type="document"`). Do not add the `[act_label] heading` prefix used for document inputs. Fail if the returned vector length is not 1,024.
- Run `$vectorSearch` on `embeddings` with index `vector_index`, `path: "vector"`, `limit` = request `limit`, and `numCandidates` derived from it (module constant rule, at least `limit`, at least 50, at most 200). Top-k is configurable only through the existing `limit` (default 5, 1–20); no new setting.
- Apply filters inside the `$vectorSearch` `filter` (not after the query). Project `chunk_id` and `{$meta: "vectorSearchScore"}`, then resolve each `chunk_id` through `chunks` (text) and `sections` (heading, chapter, source fields). Keep the database's descending score order. Return chunks, not de-duplicated sections: two chunks of one section may both appear.
- Before the first query, confirm readiness cheaply (credentials set, index queryable, `embeddings` non-empty); a positive result may be cached per process. Re-check after any failure.

### 2. Request validation and scope

- `question`: trim; reject empty or whitespace-only (existing 1–4,000 limit stays). `limit`: existing 1–20 range; out of range is a validation error.
- `SemanticFilters` keeps `act`, `status`, `access_level` as lists, and now forbids unknown fields. Values must be plain strings from the known sets (`BNS_2023`/`IPC_1860`, `in_force`/`repealed`, `public`), checked with the existing `mongodb_schema` validators. A dict, operator (`$in`, `$ne`, …), or MongoDB-shaped fragment is rejected. Do not reject the three list fields themselves. Existing chat code builds `SemanticFilters` with only these fields, so it keeps working.
- Effective scope: the server fixes `access_level` to `["public"]`. Caller lists only narrow it: an empty list means no narrowing for that field; non-empty lists become `$in` clauses. A caller can never widen scope, and unsupported values (for example `access_level: ["restricted"]`) are rejected, not silently dropped.
- `caller_id`: the effective caller is `WEBUI_DEMO_CALLER_ID`. Omitted or equal to it is accepted; any other value is rejected. No multi-user authorization.
- `required_acts` and `chapter` are not used by semantic mode: reject them with a clear message rather than silently ignoring a scope the caller asked for. `generate_answer` is accepted but nothing is generated (`generation` stays null); say so in `trace`.
- Reject with HTTP 422 (FastAPI validation or equivalent). Do not invent source fields: where a field is missing, return empty/null, never a guess.

### 3. Response shape (existing contracts, additive only)

- `QueryResult.pattern` = `"semantic"` (selected mode); `results` hold the passages. Leave `omitted_candidates`, `subquestions`, and `hyde_*` empty.
- `RetrievedChunk` keeps `chunk_id`, `section_id` (act-qualified, e.g. `bns:303`), `act`, `text`, `heading` (the title; empty string where the corpus has none, as with some IPC sections), `score`. Add only optional fields with defaults for available source details: `chunk_index`, `act_label`, `status`, `chapter`, `chapter_title`, `section_number`, `source_pdf`, `source_sha256`, `needs_review`.
- `status`: `"ok"` when passages are returned; `"no_results"` (HTTP 200, empty `results`) when the filters leave nothing to search. Add no score cutoff: an off-topic question still returns its nearest passages with their scores. The message states that scores rank similarity only and do not prove correctness.
- `trace` (inspectable, no vectors, no secrets): `mode`, `query` (the question as embedded), `embedding` (model, input_type, dimensions), `index`, `limit`, `num_candidates`, `filters` (effective, as applied), `caller_id`, `result_count`, `ignored` notes (for example answer generation), and `unresolved_hits` (count of vector hits whose chunk or section could not be resolved; those hits are omitted, never fabricated).
- Failures are not `no_results`. Missing credentials, absent or not-ready index, empty embeddings, or a model/dimension mismatch → HTTP 503 with a one-line actionable message and code (for example `retrieval_not_ready`). A Voyage or MongoDB error during a query → HTTP 502 (`retrieval_upstream_error`). Messages never include URIs or keys.

### 4. Routing

- `routes/query.py` hands `semantic` requests to the new module; every other mode still goes through `run_pattern` and returns its `not_implemented` placeholder unchanged.
- Leave `routes/chat.py` and the placeholder in `run_pattern` untouched: chat for `rag-semantic` keeps returning its existing placeholder until the answer-generation story. `/v1/models` still lists the six IDs, so `rag-semantic` stays selectable in Open WebUI.

### 5. Update docs

- `docs/architecture.md`: add a "Semantic retrieval (Story 2.3)" section after the MongoDB section with the flow (validate → scope filters → embed query → `$vectorSearch` → resolve chunk/section → `QueryResult`), the status/failure outcomes, the added optional `RetrievedChunk` fields, and the diagnostic command below. Fix the line saying all modes return placeholders (semantic is now real on `/v1/query`; chat is still a placeholder).
- Example diagnostic command (text truncated, never print full passages):

```bash
curl -s http://127.0.0.1:8000/v1/query -H "Content-Type: application/json" \
  -d '{"question": "What is the punishment for theft?", "pattern": "semantic", "limit": 3}' \
  | jq '{status, trace, results: [.results[] | {chunk_id, section_id, act, heading, score, text: .text[:80]}]}'
```

- `docs/manual-tests.md`: update only the semantic `/v1/query` entry, which still expects `not_implemented`. If `tests/test_smoke.py` asserts that, update just that assertion.

## Completion checks (a few direct checks only)

Start the API as in `docs/manual-tests.md`, then use the `jq` form above (truncate `text`).

1. **Natural-language question:** a BNS or IPC question (for example theft) returns `status: "ok"`, at least one result, with all listed fields and a populated `trace`.
2. **No-result vs failure:** `filters: {"act": ["IPC_1860"], "status": ["in_force"]}` (IPC is repealed) returns HTTP 200, `status: "no_results"`, empty `results`. Starting with an empty `VOYAGE_API_KEY` returns a non-200 configuration error, not `no_results`. Do not run a broad rejection suite; one request with an unknown filter field or `{"act": {"$ne": "x"}}` returning 422 is enough.
3. **Ordering and limit:** `limit: 3` returns at most 3 results in non-increasing score order; `limit: 21` is rejected.
4. **Source inspection:** take one returned passage and confirm `chunk_id`, `section_id`, `act`, and `heading` against `sections`/`chunks` in MongoDB or the matching record in `data/processed/*_sections.jsonl`, and that its `text` is part of that section's text and reads correctly against the PDF page. Record the section checked.
5. `GET /v1/models` still lists six IDs; chat with `rag-semantic` still returns its placeholder; another mode on `/v1/query` still returns `not_implemented`.
6. `uv run ruff check src/building_with_rag` passes.

Run only checks for this story. Do not run the full test suite, and add no evaluation harness, hybrid search, or re-ranking.

## Handover

Record: files created or changed; data-check results (counts, index status); commands run; the result of each check above (the section inspected in check 4, with its score); the `numCandidates` rule chosen; and the architecture section added.

Story report (per project instructions): `Completed.`, changed paths, test results — at most 7 lines, ending with the full-suite command (`uv run pytest`) for the developer to run if wanted.