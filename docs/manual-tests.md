# Manual tests

## Story 1.1 — Architecture and Project Seed

What it adds: FastAPI project seed with health, query, model-listing, and OpenAI-compatible chat endpoints — all returning honest `not_implemented` placeholders.

Prerequisite: start the API — `uv run uvicorn building_with_rag.app:app --host 127.0.0.1 --port 8000`

### Health

```bash
curl -s http://127.0.0.1:8000/healthz
```

Expected: `{"status":"ok"}`.

### List models

```bash
curl -s http://127.0.0.1:8000/v1/models | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data']]"
```

Expected: six lines: `rag-semantic`, `rag-hybrid`, `rag-hybrid-reranked`, `rag-structured`, `rag-decomposition`, `rag-hyde`.

### Query — each RAG mode (semantic)

```bash
curl -s http://127.0.0.1:8000/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is theft?", "pattern": "semantic"}'
```

Expected: `"status":"not_implemented"`, `"message":"Pattern 'semantic' is not implemented yet..."`.

### Query — decomposition

```bash
curl -s http://127.0.0.1:8000/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is theft?", "pattern": "decomposition"}'
```

Expected: `"status":"not_implemented"`, message references `decomposition`.

### Query — hyde

```bash
curl -s http://127.0.0.1:8000/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is theft?", "pattern": "hyde"}'
```

Expected: `"status":"not_implemented"`, message references `hyde`.

### Query — empty question (failure)

```bash
curl -s http://127.0.0.1:8000/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question": "", "pattern": "semantic"}'
```

Expected: 422 validation error (question below min_length 1).

### Chat completions — JSON (non-streaming)

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "rag-semantic", "messages": [{"role": "user", "content": "What is theft?"}]}'
```

Expected: `"object":"chat.completion"`, `"finish_reason":"stop"`, content contains `not implemented yet`.

### Chat completions — streaming

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "rag-semantic", "messages": [{"role": "user", "content": "What is theft?"}], "stream": true}'
```

Expected: SSE `data:` frames with `delta` role then content, ending with `data: [DONE]`.

### Chat completions — invalid model (failure)

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}'
```

Expected: 400 with `"type":"invalid_request_error"`, `"code":"model_not_found"`.

### Chat completions — no user message (failure)

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "rag-semantic", "messages": [{"role": "system", "content": "You are helpful."}]}'
```

Expected: 400 with `"code":"missing_user_message"`.

## Story 2.1 — Document Ingestion

What it adds: PDF extraction script that produces inspectable JSONL section-level corpora from the BNS and IPC bare act PDFs.

Prerequisite: Story 1.1 complete, `data/raw/` PDFs and `PROVENANCE.md` present, `uv sync` done.

### Run extraction

```bash
uv run python scripts/extract_sections.py
```

Expected: prints parser name/version, record counts (~358 BNS, ~500 IPC), count of `needs_review` flags, count of empty-text records. Both `data/processed/bns_sections.jsonl` and `data/processed/ipc_sections.jsonl` exist.

### Re-run (skip)

```bash
uv run python scripts/extract_sections.py
```

Expected: prints "BNS corpus up to date — skipping" and "IPC corpus up to date — skipping". No records appended or overwritten.
## Story 2.2 — MongoDB, Chunks, Embeddings, and Vector Index

What it adds: an ingestion runner that loads the JSONL corpora into MongoDB as `sources`, `sections`, `chunks`, and `embeddings`, embeds every chunk with Voyage, and creates the Atlas `vector_index` on `embeddings.vector`.

Prerequisite: Story 2.1 complete (both JSONL files present), and `.env` holds `MONGODB_URI` (Atlas cluster), `MONGODB_DB_NAME`, `VOYAGE_API_KEY`. First run takes roughly 35–40 minutes on a free Voyage key.

### Run ingestion

```bash
uv run python -m building_with_rag.ingestion.ingest
```

Expected: steps 1–10 print in order. `sources=2`, `sections=858` with the supplied PDFs, `chunks` > 0, and `embeddings == chunks: True`. `bns:1 chunks` shows one or more with `all linked: True`, sample vector length 1024, index status `READY`, and the sample query for "punishment for theft" prints `chunk_id`, `section_id`, heading, and score (or `vector query pending — index not ready`).

### Re-run (skip)

```bash
uv run python -m building_with_rag.ingestion.ingest
```

Expected: sources and sections report skipped, chunks report all skipped with 0 inserted/replaced, `Embeddings: 0 inserted, <n> skipped, 0 deleted` (no Voyage calls), and the existing `vector_index` is reused rather than recreated.

### Missing MongoDB URI (failure)

```bash
MONGODB_URI= uv run python -m building_with_rag.ingestion.ingest
```

Expected: stops at step 1 with `MongoDB unavailable: MONGODB_URI is empty. Set it in .env and re-run.` Nothing is written and no Voyage call is made.

## Story 4.1 — Hybrid Search

What it adds: `pattern: "hybrid"` (`rag-hybrid`) fuses Atlas Search keyword hits on `chunks.text` with semantic hits using Reciprocal Rank Fusion.

Prerequisite: API running; `.env` has `MONGODB_URI`, `VOYAGE_API_KEY`; create the keyword index once with `uv run python -m building_with_rag.ingestion.keyword_index` (ends with `chunk_text_index: READY (queryable)`; re-run reuses it).

```bash
# Success (text truncated)
curl -s http://127.0.0.1:8000/v1/query -H "Content-Type: application/json"   -d '{"question": "criminal breach of trust", "pattern": "hybrid", "limit": 5}'   | jq '{status, t: (.trace | {fusion, contribution}), r: [.results[] | {section_id, score, sr: .semantic_rank, kr: .keyword_rank, fr: .fused_rank, text: .text[:60]}]}'

# Edge: unsupported chapter filter (HTTP 422)
curl -s -o /dev/null -w "%{http_code}
" http://127.0.0.1:8000/v1/query -H "Content-Type: application/json"   -d '{"question": "theft", "pattern": "hybrid", "chapter": "XVII"}'

# Chat, streamed
curl -sN http://127.0.0.1:8000/v1/chat/completions -H "Content-Type: application/json"   -d '{"model": "rag-hybrid", "stream": true, "messages": [{"role": "user", "content": "What is criminal breach of trust?"}]}'   | grep '^data: {' | sed 's/^data: //' | jq -rj '.choices[0].delta.content // empty' | head -c 1500
```

Expected: first returns `"status":"ok"`, at most 5 results in non-increasing `score` (`score` = fused score, `fr` = position), each with `sr` and/or `kr`, `trace.fusion` `rrf`/`k` 60. Second prints `422`. Third streams a `DRAFT` line, `[E1]`-labelled answer, `Evidence check passed — confidence: high` and `Sources:`, and the stream ends with `data: [DONE]`. If `chunk_text_index` is missing, hybrid returns HTTP 503 `retrieval_not_ready` while `semantic` still works.

## Story 4.2 — Hybrid Re-ranking

What it adds: `pattern: "hybrid-reranked"` (`rag-hybrid-reranked`) re-scores up to `RERANK_CANDIDATE_LIMIT` hybrid candidates with one re-ranking call and keeps the best `min(limit, RERANK_RETURN_LIMIT)`.

Prerequisite: API running; `.env` has `MONGODB_URI`, `VOYAGE_API_KEY`, `GENERATION_*`, `RERANK_API_KEY`; keyword index created (Story 4.1).

```bash
# Success (text truncated; returned and omitted records merged)
curl -s http://127.0.0.1:8000/v1/query -H "Content-Type: application/json"   -d '{"question": "What is the difference between culpable homicide and murder?", "pattern": "hybrid-reranked", "limit": 5}'   | jq '{status, t: .trace.rerank, r: [(.results[]|.+{kept:true}), (.omitted_candidates[]|.+{kept:false})] | map({section_id, kept, fr: .fused_rank, rr: .rerank_rank, rs: .rerank_score, why: .omitted_reason, text: .text[:50]}) | sort_by(.fr)}'

# Edge: unsupported chapter filter (HTTP 422)
curl -s -o /dev/null -w "%{http_code}
" http://127.0.0.1:8000/v1/query -H "Content-Type: application/json"   -d '{"question": "theft", "pattern": "hybrid-reranked", "chapter": "XVII"}'

# Chat, streamed
curl -sN http://127.0.0.1:8000/v1/chat/completions -H "Content-Type: application/json"   -d '{"model": "rag-hybrid-reranked", "stream": true, "messages": [{"role": "user", "content": "What is the difference between culpable homicide and murder?"}]}'   | grep '^data: {' | sed 's/^data: //' | jq -rj '.choices[0].delta.content // empty' | head -c 1500
```

Expected: first returns `"status":"ok"`, `kept:true` records ordered by `rerank_rank` (1..n, `score` = `rerank_score`) with their `fused_rank`, the rest `kept:false` with `omitted_reason` `not_sent_to_reranker` or `below_return_limit`; `trace.rerank` shows counts and `latency_ms`. Second prints `422`. Third streams `DRAFT`, `[E1]`-labelled answer, `Evidence check passed — confidence: high`, `Sources:` (cited from kept results only) and ends with `data: [DONE]`. With `RERANK_API_KEY` empty, `hybrid-reranked` returns HTTP 503 `retrieval_not_ready` (no hybrid fallback); a provider failure returns 502 `retrieval_upstream_error`.

## Story 5.1 — Structured Exact Retrieval

What it adds: `pattern: "structured"` (`rag-structured`) classifies the question by rules and returns the exact BNS or IPC section named, using one read-only `sections` lookup (no Voyage call).

Prerequisite: API running; `.env` has `MONGODB_URI`; Story 2.2 data ingested.

```bash
# Success (text truncated)
curl -s http://127.0.0.1:8000/v1/query -H "Content-Type: application/json" \
  -d '{"question": "What does BNS section 103 say?", "pattern": "structured"}' \
  | jq '{status, t: (.trace | {signals, mongodb_called, record}), r: [.results[] | {section_id, act, status, text: .text[:60]}]}'

# Ambiguous: no act named
curl -s http://127.0.0.1:8000/v1/query -H "Content-Type: application/json" \
  -d '{"question": "What does section 103 say?", "pattern": "structured"}' \
  | jq '{status, message, mongodb_called: .trace.mongodb_called, results}'

# Missing record (IPC section 4 is not extracted)
curl -s http://127.0.0.1:8000/v1/query -H "Content-Type: application/json" \
  -d '{"question": "IPC section 4", "pattern": "structured"}' | jq '{status, mongodb_called: .trace.mongodb_called, results}'

# Chat, streamed
curl -sN http://127.0.0.1:8000/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model": "rag-structured", "stream": true, "messages": [{"role": "user", "content": "What does BNS section 103 say?"}]}' \
  | grep '^data: {' | sed 's/^data: //' | jq -rj '.choices[0].delta.content // empty' | head -c 1500
```

Expected: first returns `"status":"ok"`, one result `bns:103` (`score` 1.0 = exact match, not similarity), `mongodb_called: true`, `record` with `status` and `source_status_version`. Second returns `clarification_needed`, empty `results`, `mongodb_called: false`, message asking for the act. Third returns HTTP 200 `not_found`, empty `results`, `mongodb_called: true`. Fourth streams `DRAFT`, an answer citing `[E1]`, `Evidence check passed — confidence: high`, `Sources:`, and ends with `data: [DONE]`; an ambiguous question streams the clarification text instead.
