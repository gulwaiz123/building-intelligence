# Story 5.1 — Structured Exact Retrieval

Ninth story of the "Building Intelligence with RAG" course. Follows Story 4.2. Adds the explicitly selectable `structured` mode: a small rule-based classifier turns the question into validated `StructuredSignals`, and one read-only MongoDB lookup on the `sections` collection returns the exact BNS or IPC section named. Decomposition and HyDE belong to later stories.

## Purpose

`pattern: "structured"` (model `rag-structured`) on `POST /v1/query` and `/v1/chat/completions` runs: classify question → (exact lookup only) one `sections` lookup by validated `act` + `section_number` → `QueryResult`. It is not LLM extraction, not a semantic-search replacement, and not a router: the selected mode decides the route, with no fallback to or from other modes. No Voyage, embedding, vector or keyword index is used.

Reuse, do not rename or replace: `QueryRequest`, `QueryResult`, `RetrievedChunk`, `GenerationResult`, the `Pattern`/`rag-<pattern>` registry, `/v1/models`, `QueryError`, `semantic.effective_filters`, the `sections` collection and its unique `(act, section_number)` index, and every `.env.example` name (`MONGODB_URI`, `MONGODB_DB_NAME`, `MONGODB_TEST_DB_NAME`, `APP_ENV`, `WEBUI_DEMO_CALLER_ID`). Add no environment variables, endpoints, dependencies, UI screens, arbitrary queries, write operations, permission systems, or automatic routing. Do not re-run ingestion or touch indexes. Semantic, hybrid and hybrid-reranked stay exactly as they are.

## Prerequisites

- Stories 1.1, 2.1–2.3, 3.1, 3.2, 4.1, 4.2 complete. Read `docs/architecture.md` first.
- `.env` (untracked) with `MONGODB_URI`; ingestion (Story 2.2) already populated `sections` (858 records). Never print or log the URI.

### Findings to respect (verified in the repo)

- **`StructuredSignals` is not defined anywhere** (not in Story 1.1, `docs/`, `contracts.py`, or the Pipe). Search once more for an uncommitted definition; if none, add it as a new additive model in `contracts.py` with exactly the fields in "Work to do" §1. Do not change any existing contract.
- `RetrievedChunk.chunk_id` is required and `sections` records have none; this story sets `chunk_id = section_id` for exact-lookup records (no embedding chunk is involved). No new `RetrievedChunk` field.
- `semantic._check_scope` rejects `chapter`; structured needs its own scope check (same `caller_id` rule, `required_acts` still rejected, `chapter` accepted). Do not change `_check_scope`.
- `sections` fields: `section_id` (`bns:103`), `act` (`BNS_2023`/`IPC_1860`), `section_number` (int), `status`, `chapter`, `chapter_title`, `heading`, `text`, `source_pdf`, `source_sha256`, `source_status_version`, `needs_review`, `access_level` (`public`). IPC sections 4, 5, 18, 34, 40, 75, 161–165 were not extracted and are absent from `sections`.
- Chat always requests an answer; `pipeline.answer_events` on an empty result would stream an "insufficient evidence" sentence that hides the classifier's message. This story guards that (see §3).
- `tests/test_smoke.py` and `docs/manual-tests.md` use `structured`/`rag-structured` as the "still a placeholder" case; they must move to `decomposition`.
- The Open WebUI Pipe already offers `structured` and sends `chapter` only with it; edit `open_webui_functions/` only if `rag-structured` is not sent.

### Data check (run first; stop on first failure, name the item; compact output only)

1. Yes/no only: `MONGODB_URI` set.
2. Counts only: `sections` documents (expect 858) and whether `{act: "BNS_2023", section_number: 103}` exists (yes/no). No text printed.

## Work to do

### 1. `StructuredSignals` and classifier (`src/building_with_rag/retrieval/structured.py`; model in `contracts.py`)

- `StructuredSignals` (Pydantic, `extra="forbid"`): `intent: Literal["exact_lookup","filter","aggregation"] | None`, `act: Literal["BNS_2023","IPC_1860"] | None`, `section_number: int | None` (1–999), `chapter: str | None`, `status: Literal["ok","recommendation","clarification_needed"]`, `reason: str`. `status="ok"` only for a complete exact lookup (`intent="exact_lookup"`, `act` and `section_number` set).
- `classify(question, chapter=None) -> StructuredSignals`: pure, rule-based (regex/keywords), no LLM, no I/O. Rules, in order:
  1. `aggregation` intent: "how many", "count", "total number" → `recommendation`, no lookup.
  2. `filter` intent: "list", "all sections", "which sections", "sections in/under …" → `recommendation`, no lookup.
  3. `exact_lookup`: a section reference (`section N`, `sec. N`, `s. N`, `§N`; integer only) plus an act token (`BNS`, `IPC`, "Bharatiya Nyaya Sanhita", "Indian Penal Code", case-insensitive). Exactly one act and one distinct section number → `ok`, e.g. "BNS section 103" → `BNS_2023`, 103.
  4. Section number but no act, both acts, several section numbers, or a non-integer/out-of-range number → `intent="exact_lookup"`, `clarification_needed`, `reason` says what is missing or conflicting (acts are never guessed).
  5. Anything else → `intent=None`, `recommendation`, reason suggests `semantic` or `hybrid` for open questions.
- `chapter` (optional, from `QueryRequest.chapter`): validate as 1–40 characters of letters, digits, spaces, `.` and `-`; invalid → 422 `unsupported_option`. It is only a bound value, never parsed from the question.

### 2. Exact lookup (read-only; same module)

- `run_structured(request) -> QueryResult`. Order: structured scope check (`caller_id`, `required_acts` rejected) → `classify` → if `status != "ok"`, return at once with no MongoDB call and `results=[]` → otherwise one `find_one` on `sections` using the existing `semantic._db` client.
- Predicate built **only** from classifier output and server filters: `act`, `section_number`, `access_level: {"$in": ["public"]}` (server-fixed via `effective_filters`), plus `status: {"$in": …}` if `request.filters.status` is given and `chapter` if given. If `request.filters.act` is given and excludes the classified act → `clarification_needed` (no query). Raw question text never enters a predicate; no operators from request fields; one document, projection without `provenance`.
- Outcomes (HTTP 200 unless noted): `ok` (one `RetrievedChunk`: `chunk_id = section_id`, `text` = section text, `score = 1.0` marks an exact match, not a similarity, source fields copied incl. `status`, `chapter`, `needs_review`); `not_found` (valid request, no record — message says only that this corpus has no such record, not that the law has no such section); `clarification_needed`; `recommendation`; missing `MONGODB_URI` → 503 `retrieval_not_ready` (only when a lookup is actually needed); MongoDB failure → 502 `retrieval_upstream_error` (log exception type only).
- Trace (no secrets): `mode: "structured"`, `signals` (the `StructuredSignals` dump), `mongodb_called` (bool), `collection: "sections"`, `filters`, `caller_id`, `result_count`, and for `ok` a `record` with `section_id`, `status`, `source_status_version`. `message` states: exact record from the supplied corpus; `status`/`source_status_version` describe the source document and do not claim current legal applicability.

### 3. Routing and shared path

- `pipeline.retrieve`: `STRUCTURED` → `run_structured`. Add `Pattern.STRUCTURED.value` to `REAL_PATTERNS`, but `answer_events` yields only when a structured result has `status == "ok"`.
- `generate_answer: true` (and chat) passes the one section through the existing `assemble_context` → grounded answer → citations/confidence path unchanged. Without `generate_answer`, `/v1/query` returns the record and trace only (direct inspection). For non-`ok` structured results, no model call is made; `/v1/query` keeps `generation` unset and chat `_pieces` yields `result.message` as plain content (same streaming framing).
- Registry entries unchanged; no context constants, prompts, validation, footer labels or streaming framing changes. Known limit: a section longer than the 12,000-char context cap yields `insufficient_evidence`; direct inspection still returns it.

### 4. Docs and tests

- `docs/architecture.md`: add "Structured exact retrieval (Story 5.1)" with the **exact-input contract** (only `StructuredSignals` from the classifier — validated `act`, `section_number`, optional validated `chapter` — may reach MongoDB; raw question text never does; what is classified, what is refused) and the **answer boundary** (retrieval returns the exact record; explanation only via the existing grounded-answer path when requested; `not_found`/`clarification_needed`/`recommendation` never produce an answer; status/version are source metadata, not current applicability). Also: `StructuredSignals` in contracts; modes paragraph, `QueryResult.status` values, endpoints/chat statements, "real modes" sentence in the context section; limitations (integer sections only, no `103A`, no multi-section or cross-act comparison, filter/aggregation recognised but not executed, 11 IPC sections absent, rule-based phrasing misses).
- `docs/manual-tests.md`: replace the Story 1.1 `structured` query/chat placeholder entries (and the Story 4.1 "still not implemented" line) with `decomposition`/`rag-decomposition`; add a compact Story 5.1 section.
- `tests/test_smoke.py`: placeholder cases use `decomposition`/`rag-decomposition`.

## Completion checks (compact; never print passages, vectors, keys, or whole responses)

Diagnostic form (text truncated):

```bash
curl -s http://127.0.0.1:8000/v1/query -H "Content-Type: application/json" \
  -d '{"question":"<q>","pattern":"structured"}' \
  | jq '{status, t: (.trace | {signals, mongodb_called, record}), r: [.results[] | {section_id, act, status, text: .text[:60]}]}'
```

1. **Valid exact lookup:** "What does BNS section 103 say?" → `status: "ok"`, one result `bns:103`, `mongodb_called: true`, `trace.record` has `status` and `source_status_version`. Repeat once for IPC ("IPC section 302") → `ipc:302`. With `generate_answer: true`, `generation.outcome` is reported with citations drawn from that one result.
2. **Ambiguous request:** "What does section 103 say?" → `status: "clarification_needed"`, empty `results`, `mongodb_called: false`, reason names the missing act. No guessed act.
3. **Missing record:** "IPC section 4" (not extracted) or "BNS section 999" → `status: "not_found"`, empty `results`, `mongodb_called: true`; HTTP 200.
4. **Other question:** "What is the punishment for theft?" → `recommendation`, `mongodb_called: false`.
5. **Chat:** `rag-structured` (stream, `head -c 1500`) for check 1 shows DRAFT/confidence/`Sources:`; for check 2 the clarification text; `rag-semantic`, `rag-hybrid`, `rag-hybrid-reranked` behave as before.
6. **Offline tests** (`tests/test_structured_retrieval.py`; no network; fake the `sections` collection; few cases): (a) classifier: "BNS section 103", "IPC sec. 302", "Indian Penal Code section 420" → ok with right act/number; (b) no act, both acts, two numbers, `section 103A` → `clarification_needed`; "how many sections…" → aggregation, "list all sections in chapter…" → filter, open question → `recommendation`; (c) the predicate passed to the fake collection contains only `act`, `section_number`, `access_level`, optional `status`/`chapter`, and nothing from the raw question (e.g. a question containing `{"$ne": null}`); (d) non-`ok` signals make no collection call; (e) missing record → `not_found`; (f) `pipeline.retrieve` routes `structured` here and `answer_events` yields nothing for non-`ok`; `assemble_context(result.results)` labels the one record.
7. `GET /v1/models` lists six IDs; `decomposition` and `hyde` still return `not_implemented`; updated smoke cases pass.
8. `uv run ruff check src/building_with_rag` passes.

Run only this story's checks (the new test file, `tests/test_smoke.py`, ruff). Do not run the full suite. If live prerequisites are unavailable, run only checks 6–8 and name the blocked prerequisite.

## Handover

Record: files created/changed; data-check results (yes/no, counts); commands run; compact diagnostics for checks 1–3; whether `StructuredSignals` already existed; architecture sections updated; deviations from this story.

Story report (per project instructions): `Completed.`, changed paths, test results — at most 7 lines, ending with the full-suite command (`uv run pytest`) for the developer to run if wanted.