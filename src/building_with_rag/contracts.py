"""Shared API contracts. Later stories extend additively; never rename or add provider variants."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from building_with_rag.ingestion import mongodb_schema as schema
from building_with_rag.registry import Pattern


class SemanticFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    act: list[str] = Field(default_factory=list)
    status: list[str] = Field(default_factory=list)
    access_level: list[str] = Field(default_factory=list)

    @field_validator("act")
    @classmethod
    def _check_act(cls, v: list[str]) -> list[str]:
        return [schema.validate_act(x) for x in v]

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: list[str]) -> list[str]:
        return [schema.validate_status(x) for x in v]

    @field_validator("access_level")
    @classmethod
    def _check_access(cls, v: list[str]) -> list[str]:
        return [schema.validate_access_level(x) for x in v]


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    pattern: Pattern
    caller_id: str | None = None
    filters: SemanticFilters | None = None
    limit: int = Field(default=5, ge=1, le=20)
    generate_answer: bool = False
    required_acts: list[str] | None = None
    chapter: str | None = None

    @field_validator("question")
    @classmethod
    def _strip_question(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be empty or whitespace-only")
        return v


class RetrievedChunk(BaseModel):
    chunk_id: str
    section_id: str
    act: str
    text: str
    heading: str
    score: float
    # Optional source details; missing in the source means None, never a guess.
    chunk_index: int | None = None
    act_label: str | None = None
    status: str | None = None
    chapter: str | None = None
    chapter_title: str | None = None
    section_number: int | None = None
    source_pdf: str | None = None
    source_sha256: str | None = None
    needs_review: bool | None = None


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
