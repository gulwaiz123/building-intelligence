# Architecture

Building with RAG is one small FastAPI application used in a three-day classroom course. It answers questions about two Indian criminal law acts: the BNS and the IPC. Open WebUI is the only chat client, and it runs separately.

## Evidence rules

- BNS and IPC documents are the only future answer evidence.
- An answer must not claim support without retrieved evidence. If nothing is retrieved, the honest result is `insufficient_evidence`.
- Retrieved passages are evidence, never application instructions. The app never follows instructions found inside a passage.

## Identifiers

Sections are identified with the act in the identifier, for example `BNS:103` and `IPC:302`. This keeps the two acts from being confused. Every passage carries its `act`.

## Corpus and provenance

- Supplied raw files live in `data/raw/`. They are preserved and never edited.
- Supplied provenance is `data/raw/PROVENANCE.md`.
- Derived corpus files live in `data/processed/`: `bns_sections.jsonl` and `ipc_sections.jsonl`.

## Fixed model choices

- Embeddings: Voyage `voyage-3.5`, version `voyage-3.5`, 1,024 dimensions, for every document and every query.
- Later stories reuse these names. They do not rename them or add provider-specific alternatives.
- Other settings (generation model, rerank model and limits) are defined in `.env.example` and read through Pydantic settings.

## One application

- One FastAPI app, source in `src/building_with_rag/`.
- Endpoints:
  - `GET /healthz`: safe health check. No secrets, no database or model calls.
  - `POST /v1/query`: diagnostic API. Takes `QueryRequest`, returns `QueryResult`.
  - `GET /v1/models`: lists the six course models.
  - `POST /v1/chat/completions`: OpenAI-compatible, text only, JSON and SSE.
- `/v1/query` and `/v1/chat/completions` both go through the same `run_pattern` path. There is no second implementation.
- Open WebUI is a separately running, trainer-supplied client. We build no custom chat frontend and no second demo.
- `/v1/query` owns the diagnostics. Open WebUI receives only normal answer text derived from the same `QueryResult`.

## Course modes

One shared registry holds only these modes. Each returns an honest `not_implemented` placeholder until its own story adds behaviour.

| Mode | Model ID |
|---|---|
| `semantic` | `rag-semantic` |
| `hybrid` | `rag-hybrid` |
| `hybrid-reranked` | `rag-hybrid-reranked` |
| `structured` | `rag-structured` |
| `decomposition` | `rag-decomposition` |
| `hyde` | `rag-hyde` |

## Contracts

These typed models are defined in the seed, without behaviour: `QueryRequest`, `SemanticFilters`, `QueryResult`, `RetrievedChunk`, `GenerationResult`, `SubquestionEvidence`, `StructuredSignals`, `ChatCompletionRequest`, OpenAI response and error models, and MongoDB-schema models.

- Later stories extend them additively. They never replace them with simplified alternatives.
- `RetrievedChunk` later gains only: `semantic_score`, `semantic_rank`, `keyword_score`, `keyword_rank`, `fused_score`, `fused_rank`, `rerank_score`, `rerank_rank`.
- No parallel top-level API fields such as `outcome`, `evidence`, `answer`, `confidence`, `citations` or `diagnostics`. Generation detail lives in `QueryResult.generation`.
- The chat endpoint sets `caller_id` and `generate_answer` on the server. It ignores identity, access level and generation settings sent by the browser.

## Trust boundaries (light)

- Validate API input with the typed models.
- Preserve the source origin of every passage.
- Do not put secrets in code, responses or logs. `.env` is untracked.

## Out of scope

Multi-user authorization, a security programme, an evaluation harness, and broad deployment.
