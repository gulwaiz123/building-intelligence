"""OpenAI-compatible chat adapter.

Maps the selected rag-<pattern> model to the same pipeline as /v1/query
(retrieve + answer_events), with server-side demo caller_id and generate_answer.
Only answer writing streams; retrieval errors are HTTP errors raised before
streaming starts. No custom SSE events.
"""

import hmac
import json
import time
import uuid

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from building_with_rag.contracts import (
    ChatCompletionRequest,
    QueryRequest,
    QueryResult,
    SemanticFilters,
)
from building_with_rag.pipeline import answer_events, render_footer, retrieve, wants_generation
from building_with_rag.registry import MODEL_ID_TO_PATTERN
from building_with_rag.retrieval.semantic import RetrievalError
from building_with_rag.settings import get_settings

router = APIRouter()

_FINISH_STOP = "stop"
_DONE = "[" + "DONE" + "]"


def _error(status: int, message: str, code: str, type_: str = "invalid_request_error"):
    return HTTPException(
        status_code=status,
        detail={"error": {"message": message, "type": type_, "code": code}},
    )


def _completion_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def _check_auth(authorization: str | None) -> None:
    key = get_settings().capstone_api_key
    if not key:
        return
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), key.encode()):
        raise _error(401, "Invalid API key.", "invalid_api_key", "authentication_error")


def _build_request(request: ChatCompletionRequest) -> QueryRequest:
    pattern = MODEL_ID_TO_PATTERN.get(request.model)
    if pattern is None:
        raise _error(400, f"Model '{request.model}' not found.", "model_not_found")
    latest_user = next((m.content for m in reversed(request.messages) if m.role == "user"), None)
    if latest_user is None:
        raise _error(400, "At least one user message is required.", "missing_user_message")
    options = request.rag_options
    filters = None
    if options:
        nested = options.filters
        filters = SemanticFilters(
            act=options.act or (nested.act if nested else []),
            status=options.status or (nested.status if nested else []),
            access_level=options.access_level,
        )
    return QueryRequest(
        question=latest_user,
        pattern=pattern,
        caller_id=get_settings().webui_demo_caller_id,  # server-side demo caller
        filters=filters,
        limit=options.limit if options else 5,
        generate_answer=True,  # server-side decision; adapter never trusts client
        required_acts=options.required_acts if options else None,
        chapter=options.chapter if options else None,
    )


def _retrieve(query_request: QueryRequest) -> QueryResult:
    try:
        return retrieve(query_request)
    except RetrievalError as e:
        raise _error(e.status_code, e.message, e.code) from None


def _pieces(query_request: QueryRequest, result: QueryResult):
    """Yield the text pieces a client receives; footer is derived from the same final result."""
    if not wants_generation(query_request):
        yield result.message
        return
    for kind, payload in answer_events(query_request.question, result):
        if kind == "final":
            result.generation = payload
        else:
            yield payload
    yield render_footer(result)


def _chunk(base: dict, n: int, delta: dict, finish: str | None) -> str:
    payload = {
        **base,
        "object": "chat.completion.chunk",
        "choices": [{"index": i, "delta": delta, "finish_reason": finish} for i in range(n)],
    }
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/v1/chat/completions")
def chat_completions(
    request: ChatCompletionRequest, authorization: str | None = Header(default=None)
):
    _check_auth(authorization)
    query_request = _build_request(request)
    result = _retrieve(query_request)  # HTTP errors surface here, before any streaming

    if not request.stream:
        text = "".join(_pieces(query_request, result))
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

    def generate():
        base = {"id": _completion_id(), "created": int(time.time()), "model": request.model}
        yield _chunk(base, request.n, {"role": "assistant"}, None)
        for piece in _pieces(query_request, result):
            if piece:
                yield _chunk(base, request.n, {"content": piece}, None)
        yield _chunk(base, request.n, {}, _FINISH_STOP)
        yield "data: " + _DONE + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
