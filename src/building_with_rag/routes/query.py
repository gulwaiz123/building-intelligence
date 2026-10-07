"""Query endpoint: semantic is real; every other mode goes through run_pattern."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from building_with_rag.contracts import QueryRequest, QueryResult
from building_with_rag.generation.answer import generate_answer
from building_with_rag.registry import Pattern, run_pattern
from building_with_rag.retrieval.semantic import RetrievalError, semantic_retrieve
from building_with_rag.settings import get_settings

router = APIRouter()


def _reject(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


@router.post("/v1/query")
def query(request: QueryRequest):
    if request.pattern is Pattern.SEMANTIC:
        caller = get_settings().webui_demo_caller_id
        if request.caller_id is not None and request.caller_id != caller:
            raise _reject("caller_id does not match the effective caller.")
        if request.required_acts is not None:
            raise _reject("required_acts is not supported by semantic mode.")
        if request.chapter is not None:
            raise _reject("chapter is not supported by semantic mode.")
        try:
            result = semantic_retrieve(request)
        except RetrievalError as e:
            return JSONResponse(
                status_code=e.status_code, content={"code": e.code, "message": e.message}
            )
        if request.generate_answer:
            result.generation = generate_answer(request.question, result.results)
            result.message += f" Answer generation: {result.generation.outcome}."
        return result
    payload = run_pattern(request.pattern, request.question, request.caller_id)
    return QueryResult(**payload)
