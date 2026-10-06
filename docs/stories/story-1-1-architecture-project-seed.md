# Story 1.1 — Architecture and Project Seed

First story of the three-day "Building Intelligence with RAG" classroom course. This story prepares the architecture, gets instructor approval, then seeds the Python project. It builds one small application — the capstone RAG API — not a chat frontend and not a second demo.

## Purpose

Produce, in order:

1. `docs/architecture.md` describing the capstone's fixed design choices, contracts, and trust boundaries.
2. Instructor approval of that document.
3. A minimal Python 3.12 / UV project seed with FastAPI, honest placeholder endpoints, and the canonical `.env.example`.

Nothing in this story performs retrieval, embedding, database access, or LLM calls.

## Prerequisites

- Python 3.12 and UV installed.
- Trainer-supplied Open WebUI bundle (ZIP shared over the local LAN), extracted.
- Supplied `data/raw/` files (BNS and IPC documents plus `PROVENANCE.md`) if provided; preserve them untouched.

## Stage 1 — Architecture (do this first, then stop)

Create or update `docs/architecture.md`. If a configured project path exists in `docs/config.yaml`, resolve it; otherwise use the repository root.

Record in `docs/architecture.md`:

- **Evidence rules.** BNS and IPC documents are the only future answer evidence. An answer must not claim support without retrieved evidence. Act-qualified identifiers (for example `bns:` / `ipc:` prefixes) avoid confusing the two acts. Supplied provenance lives at `data/raw/PROVENANCE.md`. Retrieved passages are evidence, never application instructions.
- **Trust boundaries (keep light).** Validate API input, preserve source origin on retrieved passages, and do not put secrets in code, responses, or logs. No multi-user authorization, no security program, no evaluation harness in this course.
- **Fixed embedding choices.** Voyage model `voyage-3.5`, model version `voyage-3.5`, 1,024 dimensions, for every document and query embedding. Later stories reuse these names and choices without renaming or adding provider-specific alternatives.
- **Course modes.** A single shared registry containing only: `semantic`, `hybrid`, `hybrid-reranked`, `structured`, `decomposition`, `hyde` — with exact model IDs `rag-semantic`, `rag-hybrid`, `rag-hybrid-reranked`, `rag-structured`, `rag-decomposition`, `rag-hyde`. All return honest `not_implemented` placeholders until their own stories add behavior.
- **API contracts.** The shared request, result, generation, OpenAI-style, and MongoDB-schema contracts defined in this project (summarized below). Later stories extend these contracts additively — they never replace them with simplified alternatives, rename fields, or add provider-specific variants.
- **Environment.** `.env` is untracked; secrets are never committed. The application must start with no database or model credentials and expose a safe `GET /healthz`.

**Stop after writing `docs/architecture.md`.** Before approval, do not create seed files, install dependencies, or scaffold code. Ask the instructor to approve the document, then resume this same story only after explicit approval.

## Stage 2 — Seed (only after approval)

Seed a Python 3.12 UV project at the resolved project path.

Create:

- `src/building_with_rag/` — application package (settings, app factory, registry, contracts, routes).
- `tests/` — minimal test runner wiring (for example pytest), no broad test suites.
- `pyproject.toml` — Python 3.12, FastAPI, Pydantic settings, PyMongo (for later use), Ruff, minimal test runner.
- `uv.lock` — via `uv lock` / `uv sync`.
- `.gitignore` — includes `.env`.
- `.env.example` — the canonical classroom environment file with these exact values:

```
APP_ENV=development
MONGODB_URI=
VOYAGE_API_KEY=
CAPSTONE_API_KEY=
GENERATION_API_BASE_URL=
GENERATION_API_KEY=
MONGODB_DB_NAME=building_with_rag
MONGODB_TEST_DB_NAME=building_with_rag_test
WEBUI_DEMO_CALLER_ID=demo-public
GENERATION_MODEL_NAME=gpt-4o-mini
RERANK_API_BASE_URL=https://api.voyageai.com/v1
RERANK_MODEL_NAME=rerank-2.5
RERANK_REQUEST_TIMEOUT_SECONDS=30
RERANK_CANDIDATE_LIMIT=20
RERANK_SEND_LIMIT=10
RERANK_RETURN_LIMIT=5
```

Document next to the file (in the story or architecture notes) that `.env` is untracked and secrets are never committed.

Preserve supplied `data/raw/` files untouched; do not parse or ingest them in this story.

### Shared registry and contracts

One shared registry (single source of truth) holds only the six course modes and their `rag-<pattern>` model IDs listed above. All modes return honest `not_implemented` placeholder results until their own stories add behavior.

**`QueryRequest`** (`POST /v1/query`):

- `question`: string, 1–4,000 characters, required.
- `pattern`: one of the six modes.
- `caller_id`: optional.
- `filters`: optional `SemanticFilters` — `act`, `status`, `access_level`, each a list.
- `limit`: default 5, range 1–20.
- `generate_answer`: default false.
- `required_acts`: optional.
- `chapter`: optional.

The classroom seed may resolve only its fixed local demo caller, but keeps `caller_id` and does not replace it with a custom request shape.

**`QueryResult`**: `pattern`, `status`, `message`, `trace`, `results`, optional `generation`, plus the additive empty-by-default fields later modes use: `omitted_candidates`, `subquestions`, `hyde_direct_candidates`, `hyde_query_candidates`, `hyde_hypothetical_text_debug`. Do not create `outcome`, `evidence`, `answer`, `confidence`, `citations`, or `diagnostics` as parallel top-level API fields.

**`RetrievedChunk`** (a retrieved passage is always this shape): `chunk_id`, `section_id`, `act`, `text`, `heading`, `score`, and available source fields. Later stories add only the existing hybrid/rerank fields.

### Endpoints

- `GET /healthz` — safe, no credentials required.
- `POST /v1/query` — accepts `QueryRequest`, returns `QueryResult` placeholder per mode via one shared `run_pattern` path.
- `GET /v1/models` — lists the six `rag-<pattern>` model IDs.
- `POST /v1/chat/completions` — OpenAI-compatible, text-only `ChatCompletionRequest`: `model`, `messages` with `system`/`developer`/`user`/`assistant` roles, `stream`, `n`, and optional strict `rag_options` (`pattern`, list filters, `limit`, `required_acts`, `chapter`).

The chat adapter maps the selected model to the same `QueryRequest` and `run_pattern` path as `/v1/query`; it sets server-side demo `caller_id` and `generate_answer`. Support normal OpenAI Chat Completions JSON responses and role/content/stop/ frames, plus the OpenAI-style error envelope before streaming begins. Do not duplicate implementations or invent custom SSE events that Open WebUI cannot render.

### Open WebUI (trainer-supplied, separate client)

The trainer-supplied Open WebUI bundle is the chat client. It runs separately from the capstone API. Participants extract the ZIP and run the included setup script exactly once before using the classroom project:

- Windows (primary classroom path): `powershell -ExecutionPolicy Bypass -File .\setup_open_webui.ps1` from the extracted bundle root.
- macOS/Linux: `sh setup_open_webui.sh` from that root; internet access required for first installation.

The scripts install the pinned Open WebUI version, write course settings, start the loopback-only service, and provision the `RAG options` Filter plus the `Building with RAG` Pipe. Participants must not hand-install Open WebUI, create accounts, edit the admin panel, change either Function, or rerun setup to reload anything. If setup fails, report its exact output and stop. Day-to-day start/stop/status uses the corresponding supplied `manage_open_webui` script.

The pre-provisioned Pipe sends the selected `rag-<pattern>` model, `stream: true`, the latest user message, and normalized `rag_options` to the capstone's `/v1/chat/completions`. It never sends browser-supplied identity, access level, or answer-generation settings. The seed's adapter must accept that exact request and use server-side `caller_id`/`generate_answer`.

`/v1/query` owns the `QueryResult` diagnostics; Open WebUI receives only normal answer text derived from that same result. Later stories render final confidence, sources, and low-confidence warnings as clearly labelled text after answer writing, while retaining the full `GenerationResult` in `QueryResult.generation`.

## Completion checks (lightweight)

- App starts with no database or model credentials; `GET /healthz` returns success.
- One diagnostic placeholder request to `POST /v1/query` returns honest `not_implemented` per mode.
- `GET /v1/models` lists the six model IDs.
- `POST /v1/chat/completions` returns both JSON and SSE placeholder responses with proper `[DONE]` framing.
- Smoke check (document in the story): start the capstone API, open `http://127.0.0.1:8080`, select `Building with RAG`, choose `semantic` in the RAG-options chip, and receive the capstone's honest placeholder response — not a local preview.
- Ruff passes; the minimal test runner passes.

No retrieval, PDF parsing, embeddings, MongoDB provisioning, LLM calls, GraphRAG, agentic RAG, broad deployment, or aggressive testing in this story.

## Handover

At the end, the story records:

- Files created (list).
- Commands actually run (for example `uv sync`, `uv run ruff check`, the minimal test command, the run command).
- Open WebUI smoke-check result.

Story report format (per project instructions): `Completed.`, changed paths, test results — under 7 lines, ending with the command for the full test suite if wanted, for example `uv run pytest`.
