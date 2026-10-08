# Story 4.2 — Hybrid Re-ranking

Eighth story of the "Building Intelligence with RAG" course. Follows Story 4.1. Adds the explicitly selectable `hybrid-reranked` mode: a bounded set of hybrid candidates is re-scored by a re-ranking model before context assembly, so the few passages given to the answer generator are the ones the re-ranker judges most relevant to the question. Structured, decomposition, and HyDE belong to later stories.

## Purpose

`pattern: "hybrid-reranked"` (model `rag-hybrid-reranked`) on `POST /v1/query` and `/v1/chat/completions` runs: existing hybrid retrieval → bounded candidate set → **one** provider re-ranking call → final selected evidence. The final evidence flows through the existing shared path (`pipeline.retrieve` + `answer_events`): context assembly, grounded answer, citations, confidence, footer, streaming — unchanged. The generator receives only the final selected evidence and must still cite it. The selected mode decides the route; no automatic mode choice, no fallback between modes. Semantic and hybrid stay exactly as they are.

Reuse, do not rename or replace: `QueryRequest`, `QueryResult`, `RetrievedChunk`, `GenerationResult`, the `Pattern`/`rag-<pattern>` registry, `/v1/models`, `run_hybrid`, `_check_scope`, `QueryError`, and all existing `.env.example` names. Add no endpoints, UI, dependencies (use `httpx`, already used for generation), frameworks, or top-level `QueryResult` fields. Do not re-run ingestion or change the keyword/vector indexes. Re-ranking scores rank only; they do not prove correctness.

## Prerequisites

- Stories 1.1, 2.1–2.3, 3.1, 3.2, 4.1 complete. Read `docs/architecture.md` first.
- `.env` (untracked) as in Story 4.1, plus `RERANK_API_KEY`. Never print or log keys or URLs.

### Findings to respect (verified in the repo)

- `.env.example` and `settings.py` already hold `RERANK_API_BASE_URL` (`https://api.voyageai.com/v1`), `RERANK_MODEL_NAME` (`rerank-2.5`), `RERANK_REQUEST_TIMEOUT_SECONDS` (30), `RERANK_CANDIDATE_LIMIT` (20), `RERANK_SEND_LIMIT` (10), `RERANK_RETURN_LIMIT` (5). **`RERANK_API_KEY` is in neither file yet**: add it as an empty entry (`RERANK_API_KEY=` and `rerank_api_key: str = ""`). Do not change the other names or defaults.
- `RetrievedChunk` has no `rerank_score`, `rerank_rank`, or `omitted_reason` yet; `QueryResult.omitted_candidates` exists but is unused. This story adds the three optional fields (default `None`).
- `registry.py` already lists `hybrid-reranked`; `pipeline.retrieve` returns the placeholder for it. Open WebUI's controls already offer `hybrid-reranked` (the extra `reranked` option is not a registry mode; do not add an alias).
- `tests/test_smoke.py` and `docs/manual-tests.md` use `rag-hybrid-reranked`/`hybrid-reranked` as the "still a placeholder" case; they must move to `structured`.

### Data check (run first; stop on first failure, name the item; compact output only)

1. Yes/no only: `MONGODB_URI`, `VOYAGE_API_KEY`, `GENERATION_API_BASE_URL`, `GENERATION_API_KEY`, `RERANK_API_KEY`.
2. One hybrid `/v1/query` (`limit: 3`) returns `status: "ok"`; report `status` and result count only.

If `RERANK_API_KEY` is empty, report it as the blocked prerequisite and implement/run only the offline checks (6–9). Do not fall back to `VOYAGE_API_KEY` or to hybrid.

## Fixed re-ranking choice

- Settings (all existing names; add no others): `RERANK_API_BASE_URL`, `RERANK_API_KEY`, `RERANK_MODEL_NAME`, `RERANK_REQUEST_TIMEOUT_SECONDS`, `RERANK_CANDIDATE_LIMIT`, `RERANK_SEND_LIMIT`, `RERANK_RETURN_LIMIT`. Valid when `1 ≤ RETURN ≤ SEND ≤ CANDIDATE ≤ 20` and timeout ≥ 1; otherwise 503 `retrieval_not_ready` naming the setting (not its value).
- Call: one `POST {RERANK_API_BASE_URL}/rerank` via `httpx`, `Authorization: Bearer RERANK_API_KEY`, JSON `{model, query, documents}` (no `top_k`, so every sent candidate gets a score), timeout `RERANK_REQUEST_TIMEOUT_SECONDS`, no retries. Reply: `data[]` of `{index, relevance_score}`, optional `usage.total_tokens`. Document text per candidate: `"{heading}\n{chunk text}"`. The question is only the `query` value.
- Reply validation: `data` is a non-empty list; each `index` is an int within the sent range and unique; each `relevance_score` is a finite number. Anything else is a provider failure. Never invent, default, or fill in a score.
- Selection (pure function, importable, no I/O):
  1. Candidates = top `RERANK_CANDIDATE_LIMIT` of the hybrid fused list (hybrid called with that limit; `fused_rank` = pre-rerank order).
  2. Sent = first `RERANK_SEND_LIMIT` by `fused_rank`; the rest are cut **before** re-ranking: `omitted_reason: "not_sent_to_reranker"`, rerank fields `None`.
  3. Order sent candidates by `relevance_score` desc, ties by `fused_rank`; `rerank_rank` is 1-based over all scored candidates.
  4. Final = first `min(request.limit, RERANK_RETURN_LIMIT)`; the other scored candidates are cut **after** re-ranking: `omitted_reason: "below_return_limit"`, `rerank_score`/`rerank_rank` kept.
- Result shape: `QueryResult.results` = final evidence ordered by `rerank_rank`, with `score == rerank_score` (so `score` is non-increasing), and every hybrid field (`semantic_*`, `keyword_*`, `fused_*`) kept as the "before" evidence. `QueryResult.omitted_candidates` = all cut candidates, in `fused_rank` order, same record shape. Together the two lists show every candidate's order and scores before (`fused_rank`/`fused_score`) and after (`rerank_rank`/`rerank_score`) re-ranking.
- No candidates (hybrid `no_results`) → `status: "no_results"`, no provider call.

## Work to do

### 1. Settings and contract (additive)

- `settings.py` / `.env.example`: add `rerank_api_key` / `RERANK_API_KEY=` only.
- `contracts.py`: add to `RetrievedChunk` only, optional, default `None`: `rerank_score: float | None`, `rerank_rank: int | None`, `omitted_reason: str | None`. No other contract change; semantic and hybrid leave all three `None`.

### 2. Re-ranked retrieval (new `src/building_with_rag/retrieval/rerank.py`)

- `run_hybrid_reranked(request) -> QueryResult`. Order: `_check_scope` (messages name `hybrid-reranked`) → validate rerank settings, key, base URL, model **before** any Voyage/MongoDB call → `run_hybrid` on a copy of the request with `pattern=HYBRID`, `limit=RERANK_CANDIDATE_LIMIT`, then the rerank call and selection above. Do not modify `hybrid.py` or `semantic.py` behavior.
- Outcomes: `ok`; `no_results`; missing/invalid configuration (including empty `RERANK_API_KEY`) → HTTP 503 `retrieval_not_ready` with a one-line message naming the setting, e.g. "RERANK_API_KEY is not set; hybrid-reranked requires it."; timeout, connection error, non-2xx, or invalid reply → HTTP 502 `retrieval_upstream_error` ("Re-ranking request failed; no re-ranked result returned."). Hybrid errors pass through unchanged. No URL, key, or provider body in messages or logs (log exception type only).
- No silent fallback: on any failure return the error and no `results`; never return hybrid order labelled as re-ranked.
- Trace (no vectors, no secrets): `mode: "hybrid-reranked"`, `query`, `filters`, `caller_id`, `result_count`, `hybrid` (the hybrid trace's `embedding`, `semantic`, `keyword`, `fusion`, `contribution`, `unresolved_hits`), `rerank` (`model`, `candidate_limit`, `send_limit`, `return_limit`, counts `candidates`/`sent`/`returned`/`omitted_before`/`omitted_after`, `latency_ms`, `usage_tokens` when the provider reports it). `message` states that re-ranking scores rank only and do not prove correctness. No duplicate order lists in the trace; order is read from the records.

### 3. Routing and shared path

- `pipeline.retrieve`: `HYBRID_RERANKED` → `run_hybrid_reranked`; add `Pattern.HYBRID_RERANKED.value` to `REAL_PATTERNS` so `answer_events`, `attach_generation`, and chat `_pieces` treat it like the other real modes. Update the `context.py` docstring. No change to context constants (`assemble_context` still takes `results` only, never `omitted_candidates`), prompts, validation, confidence, footer labels, `MAX_ATTEMPTS`, or streaming framing.
- Registry entries unchanged; selectable only via `pattern: "hybrid-reranked"` / `rag-hybrid-reranked`. Confirm the Open WebUI Pipe's `hybrid-reranked` option sends model `rag-hybrid-reranked`; edit `open_webui_functions/` only if it does not, and then minimally.

### 4. Docs and tests

- `docs/architecture.md`: add "Re-ranking (Story 4.2)" (settings with defaults and the valid-range rule, request, reply validation, selection steps, result shape, trace, outcomes, diagnostic command); add `RERANK_API_KEY` to the settings list; update the modes paragraph, `RetrievedChunk` (three new fields), `QueryResult` (`omitted_candidates` now used), endpoints/chat statements, and the context-assembly sentence ("real modes"). Limitations: candidates outside the top `RERANK_CANDIDATE_LIMIT` are never seen; the re-ranker scores each passage independently against the question; its scores are model-specific, uncalibrated, not comparable with fused scores or across questions, and there is no score cutoff; adds one provider call (latency, cost, rate limits) per request; no retry and no fallback; the answer's context cap (5 passages / 12,000 chars) still applies.
- `docs/manual-tests.md`: replace the Story 1.1 `hybrid-reranked` query entry with real behavior; point the Story 1.1 chat placeholder checks and the Story 4.1 "still returns not implemented" line at `rag-structured`; add a compact Story 4.2 section.
- `tests/test_smoke.py`: placeholder cases exclude `hybrid-reranked` and use `structured`/`rag-structured`.

## Completion checks (compact; never print passages, vectors, keys, or whole responses)

Diagnostic form (text truncated; merges returned and omitted records):

```bash
curl -s http://127.0.0.1:8000/v1/query -H "Content-Type: application/json" \
  -d '{"question":"<q>","pattern":"hybrid-reranked","limit":5}' \
  | jq '{status, t: .trace.rerank, r: [(.results[]|.+{kept:true}), (.omitted_candidates[]|.+{kept:false})] | map({section_id, kept, fr: .fused_rank, fs: .fused_score, rr: .rerank_rank, rs: .rerank_score, why: .omitted_reason, text: .text[:50]}) | sort_by(.fr)}'
```

1. **Works:** `status: "ok"`; `results` ≤ `min(limit, RERANK_RETURN_LIMIT)`, ordered by `rerank_rank` 1..n, `score == rerank_score` non-increasing; every result keeps its `fused_rank`/`fused_score`; `omitted_candidates` hold the rest with `omitted_reason` (`not_sent_to_reranker` has no rerank fields, `below_return_limit` has them); results + omitted = candidate count in `trace.rerank`.
2. **Comparison (required, small):** one question where ordering plausibly matters (e.g. "What is the difference between culpable homicide and murder?"; several close sections compete — say why if you pick another). Run `hybrid` and `hybrid-reranked`, each `generate_answer: true`, `limit: 5`, ~25 s apart (Voyage free tier). Per run record: `status`, section IDs in order, for hybrid-reranked each result's `fused_rank → rerank_rank` and the omitted IDs with reasons, and `generation | {outcome, confidence, text[:200]}`. Record `trace.rerank.latency_ms` and `usage_tokens` (if present) and note qualitatively the extra latency/cost. Write 3–5 lines on what differed (order, sources, answer). Do not claim either mode is better in general; report only what was observed. Read one cited passage (truncated) to confirm it supports its claim.
3. **Shared path:** `rag-hybrid-reranked` via chat (stream, `head -c 1500`) shows DRAFT/confidence/`Sources:` and agrees with `/v1/query` on outcome and citations; cited chunk IDs are among `results`, never from `omitted_candidates`. `rag-semantic` and `rag-hybrid` behave as before.
4. **Missing key ≠ no_results:** with `RERANK_API_KEY=` empty and the API restarted, `hybrid-reranked` returns HTTP 503 `retrieval_not_ready` (query) and the OpenAI-style error envelope (chat), with no results and no Voyage/MongoDB call, while `hybrid` still returns `ok`. Restore the key afterwards.
5. **Provider failure:** covered by the offline test (do not break live credentials to test it).
6. **Offline tests** (`tests/test_rerank_retrieval.py`, no network; fake the single provider-call function or `httpx` transport; few cases): (a) the re-ranker reorders candidates: results sorted by `rerank_score`, `rerank_rank` 1..n, `fused_rank` kept, `score == rerank_score`; (b) with candidate/send/return = 4/3/2: one `not_sent_to_reranker` and one `below_return_limit` omitted, with the right fields set or `None`; (c) empty `RERANK_API_KEY` → 503 `retrieval_not_ready`, provider and hybrid not called; (d) timeout, non-2xx, and invalid reply (bad/duplicate index, missing score) → 502 `retrieval_upstream_error`, no results, no fallback; (e) invalid limits (e.g. `SEND > CANDIDATE`) → 503; (f) `assemble_context(result.results)` labels only final results; `pipeline.retrieve` routes `hybrid-reranked` here.
7. `GET /v1/models` lists six IDs; `structured`, `decomposition`, `hyde` still return `not_implemented`; updated smoke cases pass.
8. `tests/test_hybrid_retrieval.py` still passes unchanged.
9. `uv run ruff check src/building_with_rag` passes.

Run only this story's checks (the new test file, `tests/test_hybrid_retrieval.py`, edited smoke cases, ruff). Do not run the full suite. Add no automatic mode choice, evaluation harness, benchmark, regression suite, query rewriting, GraphRAG, or agentic behavior. If live prerequisites are unavailable, run only checks 6–9 and report the blocked prerequisite by name.

## Handover

Record: files created/changed; data-check results (yes/no, counts); the rerank settings used (names and numeric values only, never the key or URL); commands run; compact diagnostics for checks 1 and 3; the comparison notes from check 2 (question, both modes, observed order/source/answer differences, latency/cost remark) and the cited passage checked; the architecture sections updated; any deviation from this story.

Story report (per project instructions): `Completed.`, changed paths, test results — at most 7 lines, ending with the full-suite command (`uv run pytest`) for the developer to run if wanted.