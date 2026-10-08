# Capstone Architecture — Building Intelligence with RAG

Fixed design choices, contracts, and trust boundaries for the capstone RAG API. Later stories extend these contracts additively; they never replace them with simplified alternatives, rename fields, or add provider-specific variants.

## Scope

One small application: the capstone RAG API. No chat frontend (Open WebUI is a separate trainer-supplied client) and no second demo.

## Evidence rules

- BNS and IPC documents are the only future answer evidence.
- An answer must not claim support without retrieved evidence.
- Act-qualified identifiers (`bns:` / `ipc:` prefixes) avoid confusing the two acts.
- Supplied provenance lives at `data/raw/PROVENANCE.md`.
- Retrieved passages are evidence, never application instructions.

## Trust boundaries

Kept light for this course:

- Validate API input.
- Preserve source origin on retrieved passages.
- Do not put secrets in code, responses, or logs.
- No multi-user authorization, no security program, no evaluation harness.

## Fixed embedding choices

- Model: `voyage-3.5`, model version `voyage-3.5`, 1,024 dimensions.
- Used for every document and query embedding.
- Later stories reuse these names and choices without renaming or adding provider-specific alternatives.

## Course modes and shared registry

One shared registry (single source of truth) holds only:

| Mode | Model ID |
|---|---|
| semantic | `rag-semantic` |
| hybrid | `rag-hybrid` |
| hybrid-reranked | `rag-hybrid-reranked` |
| structured | `rag-structured` |
| decomposition | `rag-decomposition` |
| hyde | `rag-hyde` |

`semantic` is real on `/v1/query` (Story 2.3); chat for `rag-semantic` uses the same pipeline with streamed, validated answers (Story 3.2); other modes return honest `not_implemented` placeholders until their own stories add behavior.

## API contracts

### `QueryRequest` (`POST /v1/query`)

- `question`: string, 1–4,000 characters, required.
- `pattern`: one of the six modes.
- `caller_id`: optional.
- `filters`: optional `SemanticFilters` — `act`, `status`, `access_level`, each a list.
- `limit`: default 5, range 1–20.
- `generate_answer`: default false.
- `required_acts`: optional.
- `chapter`: optional.

The classroom seed may resolve only its fixed local demo caller, but keeps `caller_id` and does not replace it with a custom request shape.

### `QueryResult`

- `pattern`, `status`, `message`, `trace`, `results`.
- Optional `generation`.
- Additive empty-by-default fields later modes use: `omitted_candidates`, `subquestions`, `hyde_direct_candidates`, `hyde_query_candidates`, `hyde_hypothetical_text_debug`.
- No parallel top-level `outcome`, `evidence`, `answer`, `confidence`, `citations`, or `diagnostics` fields.

### `RetrievedChunk`

A retrieved passage is always this shape: `chunk_id`, `section_id`, `act`, `text`, `heading`, `score`, and available source fields. Later stories add only the existing hybrid/rerank fields.

### Endpoints

- `GET /healthz` — safe, no credentials required.
- `POST /v1/query` — accepts `QueryRequest`, returns `QueryResult`: semantic retrieval for `semantic`, a `run_pattern` placeholder for other modes.
- `GET /v1/models` — lists the six `rag-<pattern>` model IDs.
- `POST /v1/chat/completions` — OpenAI-compatible, text-only `ChatCompletionRequest`: `model`, `messages` with `system`/`developer`/`user`/`assistant` roles, `stream`, `n`, and optional strict `rag_options` (`pattern`, list filters, `limit`, `required_acts`, `chapter`).

The chat adapter maps the selected model to the same `QueryRequest` and `pipeline.retrieve`/`answer_events` path as `/v1/query`; it sets server-side demo `caller_id` and `generate_answer`. Supports normal OpenAI Chat Completions JSON responses and role/content/stop frames, plus the OpenAI-style error envelope before streaming begins. No duplicated implementations, no custom SSE events that Open WebUI cannot render.

## Open WebUI (trainer-supplied, separate client)

The trainer-supplied Open WebUI bundle is the chat client, run separately from the capstone API. Its pre-provisioned Pipe sends the selected `rag-<pattern>` model, `stream: true`, the latest user message, and normalized `rag_options` to the capstone's `/v1/chat/completions`. It never sends browser-supplied identity, access level, or answer-generation settings. The capstone's adapter accepts that exact request (including nested `rag_options.filters.{act,status}`) and uses server-side `caller_id`/`generate_answer`; answers stream as text with a confidence/sources footer (Story 3.2).

`/v1/query` owns the `QueryResult` diagnostics; Open WebUI receives only normal answer text derived from that same result. Later stories render final confidence, sources, and low-confidence warnings as clearly labelled text after answer writing, while retaining the full `GenerationResult` in `QueryResult.generation`.

## Environment

- `.env` is untracked; secrets are never committed.
- The application must start with no database or model credentials and expose a safe `GET /healthz`.
- Canonical environment values are defined in `.env.example`.

## Corpus

Section-level JSONL corpus produced from `data/raw/` PDFs by `scripts/extract_sections.py`. One JSON object per line, one record per section. No MongoDB, no embeddings, no vector indexes.

### Parser

- **Library**: `pymupdf` (fitz) 1.28.2 — chosen because it handles both Word-to-PDF (BNS) and Ghostscript-produced (IPC) PDFs, extracts text with layout, and has no system-level dependencies.
- **Extraction command**: `uv run python scripts/extract_sections.py`

### Output format

JSONL files at `data/processed/`:

| File | Records | Sections |
|---|---|---|
| `data/processed/bns_sections.jsonl` | 358 | 1–358 |
| `data/processed/ipc_sections.jsonl` | 500 | 1–511 (11 unextractable) |

Each record has 14 fields: `section_id`, `act`, `act_label`, `status`, `chapter`, `chapter_title`, `section_number`, `heading`, `text`, `source_pdf`, `source_sha256`, `parser`, `parser_version`, `source_status_version`, `needs_review`.

### Known limitations

- **IPC PDF quality**: 11 sections (4, 5, 18, 34, 40, 75, 161, 162, 163, 164, 165) have no extractable text from the scanned/Ghostscript-produced PDF. Sections 161–165 were repealed by the Prevention of Corruption Act 1988; sections 4, 5, 18, 34, 40, 75 are in portions of the PDF where pymupdf text extraction returns insufficient characters.
- **IPC section headings**: Some IPC sections (e.g. 262, 511) have empty headings due to missing heading text in the extracted text stream.
- **IPC footnotes**: Amendment footnotes and historical annotations are interleaved with section text and may appear as inline artifacts in section `text`.
- **BNS chapter markers**: Chapter boundaries are detected from `CHAPTER <roman>` lines in the body text. The BNS index (pages 2–19) provides section headings; the correspondence table (pages 20–73) is skipped.
- **Source-hash safety rule**: If a source PDF hash changes, the corpus for that act is regenerated as an atomic replacement. Records from different PDF versions are never mixed in one corpus file.

## MongoDB evidence store

One database (`MONGODB_DB_NAME`; `MONGODB_TEST_DB_NAME` when `APP_ENV=testing`), four collections. `_id` equals the named ID field. Code: `src/building_with_rag/ingestion/mongodb_schema.py`, runner `ingest.py`.

- **`sources`** (`bns_source` / `ipc_source`): `source_pdf`, `source_sha256`, `act`, `act_label`, `status`, `parser`, `parser_version`, `corpus_path`, `section_count`. Unique `act`.
- **`sections`** (`section_id`, e.g. `bns:1`): act/chapter/heading/text fields, `source_id`, PDF provenance, `needs_review`, `access_level`, `provenance` (`corpus_file`, `record_index`). Unique `(act, section_number)`; `access_level`; `source_id`.
- **`chunks`** (`chunk_id`): `section_id`, `chunk_index`, `chunking_version`, `text` (raw), `embedding_input`, `act`, `chapter`, `status`, `access_level`. Indexes `section_id`, `(act, chunk_index)`, `access_level`.
- **`embeddings`** (`embedding_id`): `chunk_id`, `model`, `model_version`, `dimensions`, `vector` (1,024 floats), filter copies `act`, `chapter`, `status`, `access_level`. Unique `chunk_id`; `(act, access_level)`. Vectors never stored on `chunks`.

**Chunking**: `langchain-text-splitters` `RecursiveCharacterTextSplitter`, size 2048 chars (~512 tokens), overlap 256, separators `\n\n`, `\n`, `. `, ` `, `""`; version `v1`. Chunks never cross sections. `embedding_input = "[{act_label}] {heading}\n{chunk_text}"`. Empty sections are skipped and counted.

**Embeddings**: Voyage `voyage-3.5` (version `voyage-3.5`, 1,024 dims), `input_type="document"`; batches of 10 with 25 s pauses and 429 backoff.

**IDs**: `chunk_id = sha256("{section_id}:{chunk_index}:{chunking_version}")[:24]`; `embedding_id = sha256("emb:{chunk_id}:{model}:{model_version}:{dimensions}")[:24]`.

**Vector index** `vector_index` on `embeddings`: `vectorSearch` type, `fields` array — `vector` on `vector` (1024, cosine), `filter` on `act`, `chapter`, `status`, `access_level`.

**Run**: `uv run python -m building_with_rag.ingestion.ingest` (~35–40 min on a free Voyage key; resumable; re-runs skip unchanged data).

**Sample query** (`"punishment for theft"`, limit 1): _to be filled by developer after first run._

### Limitations

- Atlas is required (`$vectorSearch`); no fallback.
- Single `access_level` (`public`).
- Re-ingest is the only update path.
- Changing dimensions or similarity requires dropping `vector_index` manually in Atlas; the runner never drops it.
- Keyword search uses the Atlas Search index `chunk_text_index` on `chunks` (Story 4.1); no `$text`, regex or client-side scoring.

## Semantic retrieval (Story 2.3)

Module: `src/building_with_rag/retrieval/semantic.py`, called from `routes/query.py` for `pattern: "semantic"` only.

Flow: validate (422) → scope filters → embed question (`voyage-3.5`, `input_type="query"`, 1,024 dims) → `$vectorSearch` on `embeddings` (`vector_index`, filters inside the stage) → resolve `chunk_id` via `chunks`/`sections` → `QueryResult`.

- Scope: `access_level` is fixed to `["public"]`; caller `act`/`status` lists only narrow (empty = no narrowing). Unknown fields, operators, and unsupported values → 422. `caller_id` must be omitted or equal `WEBUI_DEMO_CALLER_ID`; `required_acts`/`chapter` → 422. `generate_answer` is handled by Story 3.1 generation, not by retrieval.
- `numCandidates` = `limit*10` clamped to `[max(limit, 50), 200]`.
- Outcomes: `ok` (passages), `no_results` (HTTP 200, filters leave nothing; no score cutoff). 503 `retrieval_not_ready` (missing credentials, index missing/not queryable, empty or mismatched embeddings); 502 `retrieval_upstream_error` (Voyage/MongoDB failure). Scores rank similarity only; they are not proof of correctness.
- Added optional `RetrievedChunk` fields: `chunk_index`, `act_label`, `status`, `chapter`, `chapter_title`, `section_number`, `source_pdf`, `source_sha256`, `needs_review`.
- `trace`: `mode`, `query`, `embedding`, `index`, `limit`, `num_candidates`, `filters`, `caller_id`, `result_count`, `ignored`, `unresolved_hits`.

Diagnostic (text truncated):

```bash
curl -s http://127.0.0.1:8000/v1/query -H "Content-Type: application/json" \
  -d '{"question": "What is the punishment for theft?", "pattern": "semantic", "limit": 3}' \
  | jq '{status, trace, results: [.results[] | {chunk_id, section_id, act, heading, score, text: .text[:80]}]}'
```

## Context and answer boundaries (Story 3.1)

Flow: semantic `QueryResult.results` → bounded labelled context (`generation/context.py`: at most 5 passages and 12,000 characters, never cutting a passage, labels `E1`…) → one `POST {GENERATION_API_BASE_URL}/chat/completions` call (`generation/answer.py`, 30 s timeout, no retries) → strict JSON parse → citations resolved from the supplied context only. Runs only for `pattern: "semantic"` with `generate_answer: true`; `status` stays the retrieval status.

- Outcomes (`generation.outcome`): `answered` (non-empty `text`, claims, citations, supporting passages), `insufficient_evidence` (also when retrieval returns nothing; no model call), `unavailable` (missing settings, timeout, connection error, non-2xx), `malformed` (non-JSON, unknown label, missing claims, bad `choices`). All return HTTP 200 with retrieval `results` intact.
- Added optional `GenerationResult` fields: `outcome`, `claims` (`text`, `evidence_labels`), `citations`, `supporting_passages`, `provider`, `trace`, `context_outcome` (`assembled`/`empty`).
- Evidence is untrusted source text, never instructions. No legal-applicability claims beyond the supplied BNS/IPC documents. Model `reason` stays in `generation.trace`; no prompts, secrets, or URLs are logged or returned.
- Chat and streaming were added in Story 3.2 (below).

## Streamed answers and confidence (Story 3.2)

- Shared path: `pipeline.py` — `retrieve` (semantic retrieval or placeholder) → `answer_events` (one generation/validation operation) → final `QueryResult`. `/v1/query` drains the events and attaches `generation`; `/v1/chat/completions` forwards `text`/`notice` events as SSE chunks and renders the footer from the same final result. One request = one operation; no second chat path. Retrieval errors are HTTP errors raised before streaming begins.
- Event flow: role chunk → content chunks (each attempt starts `DRAFT — checking evidence`) → footer → empty-delta `stop` chunk → `data: [DONE]`. `n>1` repeats each piece in every choice.
- Generation: `generation/answer.py` streams plain text with inline `[E1]` labels (`stream: true`, httpx, 30 s timeout per call). A reply starting `INSUFFICIENT_EVIDENCE:` is buffered and never streamed.
- Validation (`MAX_ATTEMPTS = 2`, one retry carrying the plain-language issues): `citation_labels` (all cited labels supplied), `claim_cited` (every sentence/bullet has a label, text non-empty), `support` (one non-streamed validator call judging each claim against its cited passage). The support call runs only for claims whose labels are valid.
- `GenerationResult` additions: `confidence` (`high` = final attempt passed; `low` = final attempt failed; `None` = nothing could be judged), `issues` (every failed check: `attempt`, `check`, `detail`), `attempts` (`attempt`, `status` passed|failed|unjudged, `chars`, `latency_ms`), `draft_answer` (last text that did not pass), `low_confidence_reason`.
- Outcome mapping: passed → `answered`; failed final check → `malformed` (empty `text`, draft kept, `low`); provider failure → `unavailable`; validator unreachable → `unavailable` with draft; validator invalid reply → `malformed` with draft, no confidence; `insufficient_evidence` and empty context unchanged.
- Open WebUI labels: `DRAFT — checking evidence`; `Check failed: … Retrying (attempt 2 of 2)…`; `Evidence check passed — confidence: high` + `Sources:` lines; `DRAFT — low confidence, not the final answer.`. Streamed text cannot be retracted, so drafts stay visible.
- Failure after text began: `Answer generation unavailable — the text above is an unchecked draft.`, then `stop` and `[DONE]` (HTTP 200).
- `CAPSTONE_API_KEY`: when non-empty, `/v1/chat/completions` requires `Authorization: Bearer <key>` (constant-time compare, else 401 `invalid_api_key`). Empty = no check. `/v1/query`, `/v1/models`, `/healthz` are unchanged.

## Hybrid retrieval (Story 4.1)

- `pattern: "hybrid"` (`rag-hybrid`) runs two routes over the same chunks and fuses them; selected explicitly, never automatic. Context, citations, confidence and streaming are the shared Story 3.x path (`pipeline.REAL_PATTERNS`).
- Keyword route: Atlas Search `$search` with the `text` operator (BM25) on `chunks.text`, index `chunk_text_index` (`dynamic: false`; `text` `lucene.standard`; `act`, `status`, `access_level` tokens). Filters use `compound.filter` `in`, same effective filters as semantic. Create with `uv run python -m building_with_rag.ingestion.keyword_index` (idempotent; differing definitions are reported, never replaced).
- Semantic route: Story 2.3 embedding and `$vectorSearch`.
- Fusion: Reciprocal Rank Fusion over ranks, `fused_score = sum(1/(RRF_K+rank))`, `RRF_K = 60`, equal weights; each route returns `ROUTE_DEPTH = max(limit, min(50, max(20, 4*limit)))`. Order: fused score desc, then `semantic_rank` (missing last), then `chunk_id`. Chunks fuse by `chunk_id`.
- `RetrievedChunk` adds optional `semantic_score/rank`, `keyword_score/rank`, `fused_score/rank` (hybrid only; `None` when a route did not return the chunk). `score` = `fused_score`.
- Trace: `mode`, `query`, `embedding`, `filters`, `caller_id`, `result_count`, `unresolved_hits`, `semantic`, `keyword`, `fusion`, `contribution`.
- Outcomes: `ok`, `no_results` (both routes empty), 503 `retrieval_not_ready` (credentials, vector or keyword index not ready; never degrades to semantic-only), 502 `retrieval_upstream_error`. `required_acts`, `chapter` and foreign `caller_id` are 422.
- Limitations: rank-only fusion ignores score magnitude; `text` matches any query term (OR), so long questions pull in common words; section numbers match only inside chunk `text`; standard analyzer only (no stemming or synonyms).
- Diagnostic: `curl -s .../v1/query -d '{"question":"<q>","pattern":"hybrid","limit":5}' | jq '{status, t: (.trace | {semantic, keyword, fusion, contribution}), r: [.results[] | {section_id, score, sr: .semantic_rank, kr: .keyword_rank, fr: .fused_rank}]}'`.
