"""Model listing: the six rag-<pattern> model IDs."""

from fastapi import APIRouter

from building_with_rag.registry import PATTERN_MODEL_IDS

router = APIRouter()


@router.get("/v1/models")
def list_models() -> dict:
    return {"object": "list", "data": [{"id": mid} for mid in PATTERN_MODEL_IDS.values()]}
