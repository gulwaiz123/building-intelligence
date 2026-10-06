import json
import time
import uuid
from collections.abc import Iterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from building_with_rag.models import (
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelCard,
    ModelList,
    OpenAIError,
    OpenAIErrorEnvelope,
    QueryRequest,
    QueryResult,
)
from building_with_rag.patterns import run_pattern
from building_with_rag.registry import MODEL_TO_MODE, MODES
from building_with_rag.settings import get_settings

app = FastAPI(title="Building with RAG")


def _error(status: int, message: str, code: str | None = None) -> JSONResponse:
    body = OpenAIErrorEnvelope(error=OpenAIError(message=message, code=code))
    return JSONResponse(status_code=status, content=body.model_dump())


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/v1/chat/"):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []))
        return _error(400, f"Invalid request: {loc}: {first.get('msg', '')}", "invalid_request")
    return JSONResponse(status_code=422, content={"detail": jsonable(exc.errors())})


def jsonable(errors: list[dict]) -> list[dict]:
    return [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in errors]


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/query", response_model=QueryResult)
def query(request: QueryRequest) -> QueryResult:
    return run_pattern(request)


@app.get("/v1/models", response_model=ModelList)
def models() -> ModelList:
    return ModelList(data=[ModelCard(id=m) for m in MODES.values()])


def _answer_text(result: QueryResult) -> str:
    if result.generation and result.generation.answer:
        return result.generation.answer
    return f"[{result.status}] {result.message}"


def _sse(completion_id: str, created: int, model: str, delta: dict, finish: str | None) -> str:
    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


@app.post("/v1/chat/completions")
def chat_completions(body: ChatCompletionRequest):
    pattern = MODEL_TO_MODE.get(body.model)
    if pattern is None:
        return _error(404, f"The model '{body.model}' does not exist.", "model_not_found")
    users = [m for m in body.messages if m.role == "user"]
    if not users:
        return _error(400, "At least one user message is required.", "invalid_request")

    opts = body.rag_options
    fields: dict = {
        "question": users[-1].content,
        "pattern": pattern,
        # Server-side only: browser identity/access/generation settings are ignored.
        "caller_id": get_settings().webui_demo_caller_id,
        "generate_answer": False,
    }
    if opts:
        fields.update(
            filters=opts.filters,
            required_acts=opts.required_acts,
            chapter=opts.chapter,
        )
        if opts.limit is not None:
            fields["limit"] = opts.limit
    try:
        query_request = QueryRequest(**fields)
    except ValidationError as exc:
        first = exc.errors()[0]
        return _error(400, f"Invalid request: {first['loc']}: {first['msg']}", "invalid_request")

    text = _answer_text(run_pattern(query_request))
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    if body.stream:

        def frames() -> Iterator[str]:
            yield _sse(completion_id, created, body.model, {"role": "assistant"}, None)
            yield _sse(completion_id, created, body.model, {"content": text}, None)
            yield _sse(completion_id, created, body.model, {}, "stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream")

    return ChatCompletionResponse(
        id=completion_id,
        created=created,
        model=body.model,
        choices=[ChatCompletionChoice(message=ChatCompletionMessage(content=text))],
    )
