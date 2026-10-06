"""OpenAI-compatible chat adapter.

Maps the selected rag-<pattern> model to the same QueryRequest / run_pattern
path as /v1/query, with server-side demo caller_id and generate_answer.
Supports normal JSON responses and role/content/stop SSE frames; no custom
SSE events. The OpenAI-style error envelope applies before streaming begins.
"""

import json
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from building_with_rag.contracts import (
    ChatCompletionRequest,
    QueryRequest,
    QueryResult,
    SemanticFilters,
)
from building_with_rag.registry import MODEL_ID_TO_PATTERN, run_pattern
from building_with_rag.settings import get_settings

router = APIRouter()

_FINISH_STOP = "stop"
_DONE = "[" + "DONE" + "]"


def _completion_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def _run_for_chat(request: ChatCompletionRequest) -> QueryResult:
    """Resolve model to pattern and run the shared path; raise OpenAI-style errors."""
    pattern = MODEL_ID_TO_PATTERN.get(request.model)
    if pattern is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": f"Model '{request.model}' not found.",
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                }
            },
        )
    latest_user = next((m.content for m in reversed(request.messages) if m.role == "user"), None)
    if latest_user is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "At least one user message is required.",
                    "type": "invalid_request_error",
                    "code": "missing_user_message",
                }
            },
        )
    options = request.rag_options
    query_request = QueryRequest(
        question=latest_user,
        pattern=pattern,
        caller_id=get_settings().webui_demo_caller_id,  # server-side demo caller
        filters=(
            SemanticFilters(
                act=options.act, status=options.status, access_level=options.access_level
            )
            if options
            else None
        ),
        limit=options.limit if options else 5,
        generate_answer=True,  # server-side decision; adapter never trusts client
        required_acts=options.required_acts if options else None,
        chapter=options.chapter if options else None,
    )
    payload = run_pattern(query_request.pattern, query_request.question, query_request.caller_id)
    return QueryResult(**payload)


def _answer_text(result: QueryResult) -> str:
    if result.generation is not None and result.generation.text:
        return result.generation.text
    return result.message


def _json_response(request: ChatCompletionRequest, result: QueryResult) -> dict:
    text = _answer_text(result)
    return {
        "id": _completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [
            {
                "index": index,
                "message": {"role": "assistant", "content": text},
                "finish_reason": _FINISH_STOP,
            }
            for index in range(request.n)
        ],
    }


def _sse_frame(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/v1/chat/completions")
def chat_completions(request: ChatCompletionRequest):
    result = _run_for_chat(request)
    if not request.stream:
        return _json_response(request, result)

    def generate():
        completion_id = _completion_id()
        created = int(time.time())
        text = _answer_text(result)
        base = {"id": completion_id, "created": created, "model": request.model}
        for index in range(request.n):
            yield _sse_frame(
                {
                    **base,
                    "object": "chat.completion.chunk",
                    "choices": [
                        {"index": index, "delta": {"role": "assistant"}, "finish_reason": None}
                    ],
                }
            )
            yield _sse_frame(
                {
                    **base,
                    "object": "chat.completion.chunk",
                    "choices": [
                        {"index": index, "delta": {"content": text}, "finish_reason": None}
                    ],
                }
            )
            yield _sse_frame(
                {
                    **base,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": index, "delta": {}, "finish_reason": _FINISH_STOP}],
                }
            )
        yield "data: " + _DONE + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
