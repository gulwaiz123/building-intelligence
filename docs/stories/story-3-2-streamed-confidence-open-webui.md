# Story 3.2 — Streamed Answers with Confidence in Open WebUI

Sixth story of the "Building Intelligence with RAG" course. Follows Story 3.1. Connects real semantic retrieval and grounded generation to the existing Open WebUI streaming path (`/v1/chat/completions`), adds bounded citation/support validation with a confidence extension, and keeps `/v1/query` as the richer inspection view.

## Purpose

`/v1/query` and `/v1/chat/completions` must run the **same** selected pattern and share one retrieval → context assembly → generation → validation → final outcome path, returning/deriving the same `QueryResult` with nested `GenerationResult`. There is no second chat path. Only answer writing streams; retrieval and context assembly stay non-streamed. One request = one generation/validation operation; the stream and the final result describe that same operation (never two independent LLM calls).

Reuse, do not rename or replace: `QueryRequest`, `QueryResult`, `RetrievedChunk`, `GenerationResult`, the `Pattern` registry, `rag-<pattern>` IDs, `GET /v1/models`, mode selection, and the `.env.example` names `CAPSTONE_API_KEY`, `WEBUI_DEMO_CALLER_ID`, `GENERATION_API_BASE_URL`, `GENERATION_API_KEY`, `GENERATION_MODEL_NAME`. Add no environment variables, endpoints, top-level `QueryResult` fields, retrieval modes, agentic behavior, or UI plugins. `docs/config.yaml` does not exist; the repository root is the project path.

## Prerequisites

- Stories 1.1, 2.1–2.3, 3.1 complete. Read `docs/architecture.md` and `open_webui_functions/open-webui-operations.md` first.
- `.env` (untracked) holds `MONGODB_URI`, `VOYAGE_API_KEY`, `GENERATION_API_BASE_URL`, `GENERATION_API_KEY`; `CAPSTONE_API_KEY` is optional. Never print or log credentials or the proxy URL.

### Data check (run first; stop on first failure, name the item)

1. Report only yes/no for each of: `MONGODB_URI`, `VOYAGE_API_KEY`, `GENERATION_API_BASE_URL`, `GENERATION_API_KEY`.
2. One semantic `/v1/query` (`limit: 3`) returns `status: "ok"`; report `status` and result count only.

If a value is missing, report it as the blocked prerequisite; implement and run only the offline checks (4–5 below). Do not work around it.

## Findings to respect (verified in the repo)

- `routes/chat.py` calls `run_pattern` (placeholder) for every model, so `rag-semantic` never retrieves today. `routes/query.py` holds the only real semantic + generation flow.
- Story 3.1 generation is one non-streamed call returning strict JSON; JSON cannot be streamed as readable text. `GenerationResult` has **no** `confidence`, `draft_answer`, `issues`, `attempts`, or `low_confidence_reason` yet; this story adds them with the meanings below.
- `CAPSTONE_API_KEY` exists in `settings.py` but is not enforced anywhere; the Pipe already sends it as a Bearer token when its Valve is set.
- The Pipe sends `rag_options.filters.{act,status}` but `ChatRagOptions` reads flat `act`/`status`, so Open WebUI filters are silently dropped. The Pipe's start hint (`building_with_rag.api.app:app`) is stale; the app is `building_with_rag.app:app`.
- `tests/test_smoke.py::test_chat_json_placeholder` uses `rag-semantic` and will stop being a placeholder.

## Work to do

### 1. One shared path (new module, e.g. `src/building_with_rag/pipeline.py`)

- `retrieve(request: QueryRequest) -> QueryResult`: semantic → `run_semantic`; other modes → `run_pattern`. `QueryError` stays an HTTP error on both routes (OpenAI-style envelope on chat, existing detail on query), always raised before any streaming starts.
- `answer_events(question, retrieval)`: a generator that runs the one generation/validation operation and yields typed events (`text`, `notice`, final `GenerationResult`). `/v1/query` drains it and attaches the result to `QueryResult.generation`; chat forwards `text`/`notice` events to the stream and renders the footer from the same final result. Both routes call `retrieve` + `answer_events`; chat sets server-side `caller_id=WEBUI_DEMO_CALLER_ID` and `generate_answer=True`, unchanged.
- Non-semantic modes and `generate_answer=false` keep today's behavior (placeholder text / `generation: null`, no model call).

### 2. Chat adapter (`routes/chat.py`)

- Replace the `run_pattern` call with the shared path above. Keep model lookup, error envelopes, `n`, JSON responses, and role/content/stop/`[DONE]` framing.
- Streaming: role chunk → one content chunk per text/notice piece (valid `chat.completion.chunk`, same `id`/`created`) → final empty-delta chunk with `finish_reason: "stop"` → `data: [DONE]`. For `n>1`, put the same piece in every index's choice (still one operation). No custom SSE events.
- `stream: false`: one JSON response whose content is the same text a streamed client would have received.
- If the provider fails after text began: emit one final line `Answer generation unavailable — the text above is an unchecked draft.`, then stop and `[DONE]`. Failure before any text: emit the unavailable message the same way. HTTP 200 once streaming has begun.
- `CAPSTONE_API_KEY`: when non-empty, `/v1/chat/completions` requires `Authorization: Bearer <key>` (constant-time compare; otherwise 401 OpenAI-style `invalid_api_key`, before streaming). When empty, no check. `/v1/query`, `/v1/models`, `/healthz` unchanged.
- Accept the Pipe's nested `rag_options.filters.{act,status}` in addition to the existing flat fields (additive in `ChatRagOptions`); the selected model still decides the pattern.

### 3. Streaming generation (`generation/answer.py`, additive; keep names, outcomes, `generate_answer` signature working)

- Provider call: same `POST {GENERATION_API_BASE_URL}/chat/completions` with `GENERATION_API_KEY` and `GENERATION_MODEL_NAME`, `temperature: 0`, standard `stream: true` SSE, `httpx` only, 30 s timeout per call. No SDKs or provider-specific parameters.
- Model output is now streamable plain text: a short answer with inline supplied labels (`[E1]`) on every factual sentence/bullet, naming the act. If evidence is missing/unrelated/conflicting the model must reply with only `INSUFFICIENT_EVIDENCE: <reason>`; buffer the first few characters so this sentinel is never streamed as an answer. Keep the untrusted-evidence and no-legal-applicability rules from Story 3.1's prompt.
- Context assembly is unchanged (`context.py`, same constants); it runs once per request and is reused across attempts.
- Derive `claims` (sentence/bullet text + labels) and resolve `citations`/`supporting_passages` from the supplied context only, in first-cited order, exactly as in Story 3.1.
- Keep the strict Story 3.1 parser only if still used; update its few tests to the new output form rather than leaving dead, untested code.

### 4. Bounded validation and confidence

Constants: `MAX_ATTEMPTS = 2` (initial + one retry). Per attempt, after the text finishes, run in order and record every failed check as `{check, detail}` with plain-language detail:

1. `citation_labels`: every cited label was supplied; none unknown. Never strip or rewrite invalid citations.
2. `claim_cited`: every factual sentence/bullet carries at least one label; answer text non-empty.
3. `support`: one non-streamed validator call (same settings, `temperature: 0`) judges, per claim, whether the cited passage text supports it; unsupported claims are reported by label and a short claim excerpt. Validator output is strict JSON; evidence is untrusted text here too.

New optional `GenerationResult` fields (defaults keep existing consumers working):

- `confidence`: `"high"` only when a final attempt passes all checks; `"low"` when the final attempt fails a check; absent (`None`) when no answer text could be judged (no text, provider/validator failure, `insufficient_evidence`).
- `issues`: every failed check across all attempts (`attempt`, `check`, `detail`), none discarded.
- `attempts`: one compact record per attempt (`attempt`, `status` `passed`|`failed`|`unjudged`, `chars`, `latency_ms`); no prompts or full text.
- `draft_answer`: the last attempt's text when it did not pass (low confidence or unjudged-with-text); empty otherwise.
- `low_confidence_reason`: one short sentence naming the failed check(s) when `confidence == "low"`.

Outcome mapping (existing four values only): passed → `answered`, `text` = validated answer, `confidence: "high"`. Failed final check → `malformed`, `text` empty (never present the draft as validated), `draft_answer`, `issues`, `confidence: "low"`. Provider failure → `unavailable`; validator unreachable → `unavailable` with `draft_answer`, confidence absent; validator invalid reply → `malformed`, confidence absent. `insufficient_evidence` and empty context (no model call) unchanged. A retry runs only after a failed attempt; its prompt carries the plain-language issues. `trace` stays free of prompts, vectors, secrets.

### 5. What Open WebUI shows (text only; use these exact labels)

- Every attempt starts with the line `DRAFT — checking evidence`, then its streamed text.
- After a failed non-final attempt: `Check failed: <short detail>. Retrying (attempt 2 of 2)…`, then the next attempt, again prefixed.
- Passed: footer with `Evidence check passed — confidence: high`, then `Sources:` one line per citation (`E1 · BNS §303 · Theft · bns:303`).
- Failed final check: `DRAFT — low confidence, not the final answer.` plus the short reason and the failed check details (compact).
- `insufficient_evidence`: no draft prefix; one plain sentence (reason from trace, truncated) plus nothing implying confidence. Placeholder modes: existing message.

`/v1/query` keeps the full `QueryResult` (results, citations, issues, attempts, trace) for inspection; extend its `message` sentence for the low-confidence case. Chat output stays readable text only.

### 6. Pipe and docs

- `open_webui_functions/building_with_rag_ui_preview.py`: fix the stale start hint to `uv run uvicorn building_with_rag.app:app --reload`; bump `version`; no other behavior change (it already relays content unchanged). Open WebUI keeps its own copy: re-paste the Pipe via Function Menu → Edit and save.
- `docs/architecture.md`: update the "chat for rag-semantic still placeholder" statements, the chat adapter paragraph, and the Open WebUI paragraph; add "Streamed answers and confidence (Story 3.2)": shared path, one operation, event flow, `MAX_ATTEMPTS`, checks, new `GenerationResult` fields and meanings, outcome mapping, DRAFT/footer labels, `CAPSTONE_API_KEY` behavior, failure-after-text rule.
- `open_webui_functions/open-webui-operations.md`: add a short "Reading answers" section (DRAFT lines, retry notice, confidence/sources footer, why drafts can't be retracted), the `capstone_api_key` Valve ↔ `CAPSTONE_API_KEY` pairing, and the re-paste step.
- `docs/manual-tests.md`: add a compact Story 3.2 section; change the Story 1.1 chat checks that expect a `rag-semantic` placeholder to `rag-hybrid`.

## Completion checks (compact; never print passages or whole responses)

**Do not test with `curl` or any live HTTP/server calls (Claude/Codex).** The assistant runs only check 4 (offline tests) and check 7 (ruff). Checks 1–3, 5 and 6 are participant-run directly in the Open WebUI interface (not Postman, which only repeats Story 3.1); report them as "pending participant". The `curl` form below is a reference for the request shape only.

Start the API as in `docs/manual-tests.md`. Streamed demo form (bounded output):

```bash
curl -sN http://127.0.0.1:8000/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"rag-semantic","stream":true,"messages":[{"role":"user","content":"<q>"}]}' \
  | grep '^data: {' | sed 's/^data: //' | jq -rj '.choices[0].delta.content // empty' | head -c 1500
```

1. **Supported:** "What is the punishment for theft under the BNS?" streams `DRAFT — checking evidence`, answer text with labels, then `confidence: high` and sources. Run the `/v1/query` equivalent (`generate_answer: true`) and compare with `jq '{status, g: (.generation | {outcome, confidence, attempts, issues, citations: [.citations[] | {label, section_id}]})}'`: same pattern, outcome, citations; record one cited section checked against its (truncated) passage.
2. **Unsupported:** "What is the GST rate on restaurant services?" ends with the insufficient-evidence sentence, no confidence, `[DONE]` present; `/v1/query` shows `outcome: "insufficient_evidence"` with retrieval `results` intact.
3. **Stream shape:** one stream has role chunk, content chunks, `finish_reason: "stop"`, and `[DONE]` last (check with `grep -c`, no body dump). Wrong Bearer with `CAPSTONE_API_KEY` set → 401 envelope; unset → works.
4. **Offline tests** (`tests/test_streamed_confidence.py`, fake provider, no network, few cases): (a) passing attempt → one generation call, streamed text equals final `text`, `confidence: "high"`; (b) failing attempt then passing retry → both attempts DRAFT-prefixed, `attempts`/`issues` recorded, high; (c) both attempts fail → `DRAFT — low confidence` closing, `text` empty, `draft_answer` + `issues` + `low_confidence_reason` set, invalid citation preserved; (d) provider failure mid-stream → unavailable line, `stop` + `[DONE]` still sent, confidence absent. Update the `rag-semantic` placeholder case in `tests/test_smoke.py` to `rag-hybrid` and any Story 3.1 parser tests affected by the output change.
5. `GET /v1/models` lists six IDs; a non-semantic model on chat and `/v1/query` still returns `not_implemented`; `generate_answer` omitted on `/v1/query` → `generation: null`.
6. **Open WebUI smoke (when configured):** with Open WebUI running per the operations doc and the Pipe re-pasted, ask check 1 and check 2 once each in the `Building with RAG` model and note what is displayed. If Open WebUI or any `GENERATION_*` value is unavailable, report the blocked prerequisite by name.
7. `uv run ruff check src/building_with_rag` passes.

Run only this story's checks (the new test file plus the edited smoke cases). Do not run the full suite; finish with the command for the developer.

## Handover

Record: files created/changed; data-check results (yes/no, counts); commands run; compact diagnostics for checks 1–2 (including whether stream and `/v1/query` agreed) and the Open WebUI observation or blocked prerequisite; `MAX_ATTEMPTS` and timeouts used; the architecture/operations sections updated; any deviation from the outcome mapping above.

Story report (per project instructions): `Completed.`, changed paths, test results — at most 7 lines, ending with the full-suite command (`uv run pytest`) for the developer to run if wanted.