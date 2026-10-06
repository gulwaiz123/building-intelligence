"""Query endpoint: one shared run_pattern path for every mode."""

from fastapi import APIRouter

from building_with_rag.contracts import QueryRequest, QueryResult
from building_with_rag.registry import run_pattern

router = APIRouter()


@router.post("/v1/query")
def query(request: QueryRequest) -> QueryResult:
    payload = run_pattern(request.pattern, request.question, request.caller_id)
    return QueryResult(**payload)
