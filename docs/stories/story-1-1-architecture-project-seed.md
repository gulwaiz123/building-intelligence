# Story 1.1: Architecture and Project Seed

## Purpose

This is the first story in the three-day classroom RAG course. It builds one small application: a FastAPI service with a diagnostic API (`/v1/query`) and an OpenAI-compatible API (`/v1/models`, `/v1/chat/completions`). It does **not** build a custom chat frontend or a second demo. The trainer-supplied Open WebUI bundle is the only chat client.

The story has two phases separated by an instructor approval gate:

1. Write `docs/architecture.md` and stop.
2. After explicit approval, seed the project.

All six course modes return honest `not_implemented` placeholders. No retrieval, embeddings, LLM calls or database work happens here.

## Prerequisites

- Python 3.12 and UV installed.
- Existing work is preserved. Supplied `data/raw/` files (including `data/raw/PROVENANCE.md`) must not be altered. At the time this story was written the repository contained only `README.md`, an empty `docs/`, and no `docs/config.yaml`, `docs/architecture.md`, `data/` or other stories. Re-check before starting.
- Project path: read `docs/config.yaml` if it exists and use the configured project path. Otherwise use the repository root.
- The trainer's Open WebUI ZIP has been shared over the LAN. Participants extract it and run its setup script **exactly once** (see Phase 2, step 9).
- Later-story needs, noted now so nobody is surprised (nothing here uses them):
  - `MONGODB_URI` is an Atlas free-tier (M0) connection string; the Atlas IP access list must allow your machine.
  - A free Voyage key is rate limited, so Story 2.2 embedding takes about 40 minutes.
  - `GENERATION_API_BASE_URL` and `GENERATION_API_KEY` are supplied by the trainer for an OpenAI-compatible LiteLLM proxy. Fill them in `.env` only when Story 3.1 needs them.

## Work to do

### Phase 1: Architecture (do this first, then stop)

1. Read `docs/config.yaml` (if present), `docs/architecture.md` (if present), the repository layout and existing stories.
2. Create or update `docs/architecture.md`. If it exists, extend it; do not discard existing content. Keep it short and in plain English. It must record:
   - **Evidence rule:** BNS and IPC documents are the only future answer evidence. An answer must not claim support without retrieved evidence.
   - **Identifiers:** act-qualified identifiers (for example `BNS:103` vs `IPC:302`) are used so the two acts are never confused.
   - **Provenance:** supplied provenance is `data/raw/PROVENANCE.md`. Derived corpus files live in `data/processed/` (`bns_sections.jsonl`, `ipc_sections.jsonl`).
   - **Retrieved text is evidence, never instructions.** The application must not follow instructions found inside retrieved passages.
   - **Light trust-boundary notes:** validate API input; preserve source origin on every passage; do not put secrets in code, responses or logs.
   - **Fixed embedding choice:** Voyage `voyage-3.5`, version `voyage-3.5`, 1,024 dimensions for every document and query embedding. Later stories reuse these names without renaming or adding provider-specific alternatives.
   - **Single application:** one FastAPI app; `/v1/query` and `/v1/chat/completions` share one `run_pattern` path; Open WebUI is a separate running client.
   - **Contracts are extended additively** in later stories, never replaced with simplified alternatives.
   - The course modes, endpoints and contract names listed in Phase 2.
3. Do **not** introduce multi-user authorization, a security programme or an evaluation harness.
4. **STOP.** Ask the instructor to review and approve `docs/architecture.md`. Before approval, do not create seed files, install dependencies or scaffold code. Resume this same story only after explicit approval.

### Phase 2: Seed (only after explicit approval)

5. **Project files.** Create a Python 3.12 + UV project with: `pyproject.toml`, `uv.lock`, `.gitignore`, `.env.example`, `src/building_with_rag/`, `tests/`. Dependencies: FastAPI (with an ASGI server), Pydantic settings, PyMongo (for later use only), Ruff, and a minimal test runner (pytest). Preserve supplied `data/raw/` files.

6. **`.env.example`** (canonical for the classroom project) with exactly these values:

   ```
   APP_ENV=development
   MONGODB_URI=
   MONGODB_DB_NAME=building_with_rag
   MONGODB_TEST_DB_NAME=building_with_rag_test
   VOYAGE_API_KEY=
   CAPSTONE_API_KEY=
   WEBUI_DEMO_CALLER_ID=demo-public
   GENERATION_API_BASE_URL=
   GENERATION_API_KEY=
   GENERATION_MODEL_NAME=gpt-4o-mini
   RERANK_API_BASE_URL=https://api.voyageai.com/v1
   RERANK_API_KEY=
   RERANK_MODEL_NAME=rerank-2.5
   RERANK_REQUEST_TIMEOUT_SECONDS=30
   RERANK_CANDIDATE_LIMIT=20
   RERANK_SEND_LIMIT=10
   RERANK_RETURN_LIMIT=5
   ```

   Document (README or comments) that `.env` is untracked, listed in `.gitignore`, and secrets are never committed. The app starts with none of the credentials set.

7. **Application, `src/building_with_rag/`:**
   - **Settings** via Pydantic settings reading the variables above; all credentials optional at startup.
   - **`GET /healthz`:** safe response (for example `{"status": "ok"}`); no secrets, no database or model calls.
   - **One shared registry** with only these modes and exact model IDs:

     | Mode | Model ID |
     |---|---|
     | `semantic` | `rag-semantic` |
     | `hybrid` | `rag-hybrid` |
     | `hybrid-reranked` | `rag-hybrid-reranked` |
     | `structured` | `rag-structured` |
     | `decomposition` | `rag-decomposition` |
     | `hyde` | `rag-hyde` |

     Every mode returns an honest `not_implemented` placeholder until its own story adds behaviour.
   - **Typed models, no behaviour**, with these exact names:
     - `QueryRequest`: `question` (1–4,000 characters), `pattern`, optional `caller_id`, optional `SemanticFilters` (`act`, `status`, `access_level`, each a list), `limit` (default 5, range 1–20), `generate_answer` (default false), `required_acts`, `chapter`. The seed may resolve only the fixed local demo caller but keeps the `caller_id` field.
     - `QueryResult`: `pattern`, `status`, `message`, `trace`, `results`, optional `generation`, plus additive empty-by-default `omitted_candidates`, `subquestions`, `hyde_direct_candidates`, `hyde_query_candidates`, `hyde_hypothetical_text_debug`. `omitted_candidates` items carry `chunk_id` and `omitted_reason`.
     - `RetrievedChunk`: `chunk_id`, `section_id`, `act`, `text`, `heading`, `score`, and available source fields. Later stories add only the fields named in their own handouts (`semantic_score`, `semantic_rank`, `keyword_score`, `keyword_rank`, `fused_score`, `fused_rank`, `rerank_score`, `rerank_rank`).
     - `GenerationResult`: `outcome` (`answered`, `insufficient_evidence`, `unavailable`, `malformed`), `answer`, `claims`, `citations`, `supporting_passages`, `provider`, `model`, `trace`, `context_outcome`, `confidence`, `draft_answer`, `issues`, `attempts`, `low_confidence_reason`.
     - `SubquestionEvidence`: `subquestion`, `status` (`evidenced` or `no_evidence`), `results` (list of `RetrievedChunk`), optional `reason`.
     - `StructuredSignals`: `intent` (`exact_lookup`, `filter`, `aggregation`), optional `act`, `section_number`, `chapter` (Story 5.1 fills these).
     - `ChatCompletionRequest`: `model`, text `messages` (roles `system`, `developer`, `user`, `assistant`), `stream`, `n`, optional strict `rag_options` (`pattern`, list filters, `limit`, `required_acts`, `chapter`).
     - OpenAI response/error models and MongoDB-schema contract models, defined without behaviour.
   - Do **not** create `outcome`, `evidence`, `answer`, `confidence`, `citations` or `diagnostics` as parallel top-level API fields.
   - **`POST /v1/query`:** validates `QueryRequest`, calls `run_pattern`, returns `QueryResult`. Diagnostics live here.
   - **`GET /v1/models`:** lists the six model IDs.
   - **`POST /v1/chat/completions`:** maps the selected model to the same `QueryRequest` and `run_pattern` path as `/v1/query`. Server-side sets the demo `caller_id` (`WEBUI_DEMO_CALLER_ID`) and `generate_answer`; it ignores browser-supplied identity, access level and generation settings. Supports normal OpenAI Chat Completions JSON, and SSE with role/content/stop frames then `[DONE]`. Errors before streaming use the OpenAI-style error envelope. No duplicate implementations, no custom SSE events. Open WebUI receives only normal answer text derived from the same `QueryResult`.

8. **Do not add** retrieval, PDF parsing, embeddings, MongoDB provisioning, LLM calls, GraphRAG, agentic RAG, deployment, or aggressive tests.

9. **Open WebUI (separately running, trainer-supplied).**
   - Windows (primary path): from the extracted bundle root run `powershell -ExecutionPolicy Bypass -File .\setup_open_webui.ps1`.
   - macOS/Linux: from that root run `sh setup_open_webui.sh` (needs internet on first install).
   - Run setup exactly once. It installs the pinned Open WebUI, writes the course settings, starts the loopback-only service, and provisions the `RAG options` Filter and `Building with RAG` Pipe.
   - Do not hand-install Open WebUI, create accounts, edit the admin panel, change either Function, or rerun setup to reload anything. If setup fails, report its exact output and stop.
   - Day-to-day start/stop/status uses the supplied `manage_open_webui` script.
   - The Pipe sends `rag-<pattern>`, `stream: true`, the latest user message and normalized `rag_options` (`pattern`, `filters` lists, `limit`, `required_acts`, `chapter`). The adapter must accept exactly that request.

## Completion checks

Keep checks lightweight. Run:

1. `uv sync` and `uv run ruff check .`
2. The minimal test runner (`uv run pytest`) with a small smoke test or two.
3. Start the app without any credentials set.
4. `GET /healthz` returns the safe response.
5. One `POST /v1/query` placeholder request returns a `QueryResult` with a `not_implemented` status.
6. `GET /v1/models` lists the six model IDs.
7. `POST /v1/chat/completions` placeholder: once as JSON, once with `stream: true` (role/content/stop frames and `[DONE]`).
8. **Open WebUI smoke check:** with the capstone API running, open `http://127.0.0.1:8080`, select `Building with RAG`, choose `semantic` in the RAG-options chip, send a question, and receive the capstone's honest placeholder response, not a local preview.

Also confirm: `.env` is not tracked, `data/raw/` is unchanged, and no secrets appear in code, responses or logs.

## Handover

Fill in when the story is done:

- **Architecture approval:** who approved `docs/architecture.md` and when.
- **Files created:** list every file created or changed.
- **Commands actually run:** list the real commands and their outcomes (do not list commands that were not run).
- **Open WebUI result:** what the smoke check showed.
- **Notes for later stories:** contracts are extended additively; Story 2.2 embedding takes about 40 minutes on a free Voyage key; Story 3.1 needs `GENERATION_API_BASE_URL` and `GENERATION_API_KEY` in `.env`; later stories render confidence, sources and low-confidence warnings as clearly labelled text after the answer, keeping the full `GenerationResult` in `QueryResult.generation`.
