"""Query endpoint: shared pipeline (retrieve + answer_events) with the full QueryResult."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from building_with_rag.contracts import QueryRequest
from building_with_rag.pipeline import retrieve, run_generation, wants_generation
from building_with_rag.retrieval.semantic import RetrievalError

router = APIRouter()


@router.post("/v1/query")
def query(request: QueryRequest):
    try:
        result = retrieve(request)
    except RetrievalError as e:
        if e.status_code == 422:
            raise HTTPException(status_code=422, detail=e.message) from None
        return JSONResponse(status_code=e.status_code, content={"code": e.code, "message": e.message})
    if wants_generation(request):
        run_generation(request.question, result)
    return result
