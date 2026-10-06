from building_with_rag.models import QueryRequest, QueryResult
from building_with_rag.registry import MODES


def run_pattern(request: QueryRequest) -> QueryResult:
    """Single path shared by /v1/query and /v1/chat/completions."""
    if request.pattern not in MODES:
        return QueryResult(
            pattern=request.pattern,
            status="unknown_pattern",
            message=f"Unknown pattern '{request.pattern}'.",
            trace=["registry:miss"],
        )
    return QueryResult(
        pattern=request.pattern,
        status="not_implemented",
        message=f"Pattern '{request.pattern}' is not implemented yet.",
        trace=[f"registry:{request.pattern}", "placeholder"],
    )
