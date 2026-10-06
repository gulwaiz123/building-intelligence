# Manual tests

## Story 1.1 — Architecture and Project Seed

**What it adds:** a running FastAPI app with health, model-listing, placeholder query, and OpenAI-compatible chat endpoints — no retrieval, embedding, or LLM calls yet.

Start the API first: `uv run uvicorn building_with_rag.app:app --reload`

```bash
# Health check — no credentials required
curl -s http://127.0.0.1:8000/healthz
```
Expect `{"status":"ok"}`.

```bash
# List the six course modes/model IDs
curl -s http://127.0.0.1:8000/v1/models
```
Expect `data` with `rag-semantic`, `rag-hybrid`, `rag-hybrid-reranked`, `rag-structured`, `rag-decomposition`, `rag-hyde`.

```bash
# Placeholder query — successful request, honest not_implemented result
curl -s -X POST http://127.0.0.1:8000/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the punishment for theft?", "pattern": "semantic"}'
```
Expect `status: "not_implemented"`, `pattern: "semantic"`, empty `results`.

```bash
# Edge case — invalid pattern value
curl -s -X POST http://127.0.0.1:8000/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is theft?", "pattern": "not-a-real-mode"}'
```
Expect a 422 validation error on the `pattern` field.

```bash
# OpenAI-compatible chat completion, non-streaming
curl -s -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "rag-semantic", "messages": [{"role": "user", "content": "What is theft?"}]}'
```
Expect a `chat.completion` object whose `choices[0].message.content` is the placeholder `not_implemented` message for `semantic`.

```bash
# OpenAI-compatible chat completion, streaming
curl -s -N -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "rag-hybrid", "messages": [{"role": "user", "content": "What is theft?"}], "stream": true}'
```
Expect a sequence of `data: {...}` SSE chunks (`chat.completion.chunk`) carrying the placeholder text, ending with `data: [DONE]`.
