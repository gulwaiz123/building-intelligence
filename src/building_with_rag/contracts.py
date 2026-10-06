"""Shared API contracts. Later stories extend additively; never rename or add provider variants."""

from pydantic import BaseModel, Field

from building_with_rag.registry import Pattern


class SemanticFilters(BaseModel):
    act: list[str] = Field(default_factory=list)
    status: list[str] = Field(default_factory=list)
    access_level: list[str] = Field(default_factory=list)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    pattern: Pattern
    caller_id: str | None = None
    filters: SemanticFilters | None = None
    limit: int = Field(default=5, ge=1, le=20)
    generate_answer: bool = False
    required_acts: list[str] | None = None
    chapter: str | None = None


class RetrievedChunk(BaseModel):
    chunk_id: str
    section_id: str
    act: str
    text: str
    heading: str
    score: float
    # Available source fields are attached by later stories; origin is always preserved.


class GenerationResult(BaseModel):
    text: str = ""
    model: str | None = None


class QueryResult(BaseModel):
    pattern: str
    status: str
    message: str
    trace: dict
    results: list[RetrievedChunk] = Field(default_factory=list)
    generation: GenerationResult | None = None
    # Additive, empty-by-default fields later modes use:
    omitted_candidates: list[RetrievedChunk] = Field(default_factory=list)
    subquestions: list[str] = Field(default_factory=list)
    hyde_direct_candidates: list[RetrievedChunk] = Field(default_factory=list)
    hyde_query_candidates: list[RetrievedChunk] = Field(default_factory=list)
    hyde_hypothetical_text_debug: str | None = None


class ChatRagOptions(BaseModel):
    pattern: Pattern = Pattern.SEMANTIC
    act: list[str] = Field(default_factory=list)
    status: list[str] = Field(default_factory=list)
    access_level: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=20)
    required_acts: list[str] | None = None
    chapter: str | None = None


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|developer|user|assistant)$")
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    n: int = 1
    rag_options: ChatRagOptions | None = None
