"""
title: RAG options
id: capstone_retrieval_controls
author: Building with RAG course
version: 0.1.0
required_open_webui_version: 0.11.4
description: Governed RAG options for the Building with RAG UI Preview model.

Story 1.1.1 replaces only the preview Pipe execution path with the capstone's
Chat Completions adapter.  These UserValves, accepted values, labels, and
normalization contract are intentionally stable across that replacement.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

PATTERN_OPTIONS = (
    "semantic",
    "hybrid",
    "hybrid-reranked",
    "reranked",
    "structured",
    "decomposition",
    "hyde",
    "graph",
    "agentic",
)
ACT_OPTIONS = ("BNS_2023", "IPC_1860")
STATUS_OPTIONS = ("in_force", "repealed")


def _select_options(values: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"value": value, "label": value} for value in values]


class UserValves(BaseModel):
    """Per-user controls shown by Open WebUI as the ``RAG options`` chip."""

    pattern: Literal[
        "semantic", "hybrid", "hybrid-reranked", "reranked", "structured",
        "decomposition", "hyde", "graph", "agentic",
    ] = Field(
        default="semantic",
        title="Retrieval pattern",
        description="Choose the explicit retrieval pattern for this chat request.",
        json_schema_extra={"input": {"type": "select", "options": _select_options(PATTERN_OPTIONS)}},
    )
    act: list[Literal["BNS_2023", "IPC_1860"]] = Field(
        default_factory=list,
        title="Acts (optional)",
        description="Narrow results to one or both acts.",
        json_schema_extra={"input": {"type": "select", "multiple": True, "options": _select_options(ACT_OPTIONS)}},
    )
    status: list[Literal["in_force", "repealed"]] = Field(
        default_factory=list,
        title="Status (optional)",
        description="Narrow results by legal status.",
        json_schema_extra={"input": {"type": "select", "multiple": True, "options": _select_options(STATUS_OPTIONS)}},
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        title="Result limit",
        description="Number of results to request (1–20).",
    )
    required_acts: list[Literal["BNS_2023", "IPC_1860"]] = Field(
        default_factory=list,
        title="Required acts (advanced, optional)",
        description="For comparisons, retain only requests that require these acts.",
        json_schema_extra={"input": {"type": "select", "multiple": True, "options": _select_options(ACT_OPTIONS)}},
    )
    chapter: str | None = Field(
        default=None,
        max_length=120,
        title="Chapter (structured only, optional)",
        description="An exact chapter value, available only with the structured pattern.",
    )

    @field_validator("act", "status", "required_acts", mode="before")
    @classmethod
    def list_values_are_lists(cls, value: Any) -> Any:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("Select values from the provided list.")
        return value

    @field_validator("chapter")
    @classmethod
    def clean_chapter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def chapter_requires_structured(self) -> "UserValves":
        if self.chapter is not None and self.pattern != "structured":
            raise ValueError("Chapter can be selected only with the structured pattern.")
        return self


def normalize_options(values: UserValves | dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the sole non-sensitive, backend-ready options shape."""
    try:
        parsed = values if isinstance(values, UserValves) else UserValves.model_validate(values or {})
    except ValidationError as exc:
        raise ValueError(f"RAG options are invalid: {exc.errors()[0]['msg']}") from exc

    filters: dict[str, list[str]] = {}
    if parsed.act:
        filters["act"] = list(parsed.act)
    if parsed.status:
        filters["status"] = list(parsed.status)
    return {
        "pattern": parsed.pattern,
        "filters": filters,
        "limit": parsed.limit,
        "required_acts": list(parsed.required_acts) or None,
        "chapter": parsed.chapter if parsed.pattern == "structured" else None,
    }


class Filter:
    """Places validated options on the in-process Open WebUI request only."""

    class Valves(BaseModel):
        pass

    # Open WebUI detects editable chip controls via ``hasattr(instance, "UserValves")``.
    UserValves = UserValves

    def __init__(self) -> None:
        self.valves = self.Valves()
        self.toggle = True
        self.icon = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%234f46e5' d='M4 5h16v2H4zm3 6h10v2H7zm3 6h4v2h-4z'/%3E%3C/svg%3E"

    def inlet(self, body: dict[str, Any], __user__: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate UserValves without changing messages or unrelated fields."""
        user_values = ((__user__ or {}).get("valves"))
        options = normalize_options(user_values)
        updated = dict(body)
        metadata = dict(updated.get("metadata") or {})
        metadata["capstone_rag_options"] = options
        updated["metadata"] = metadata
        # Open WebUI 0.11.4 consumes ``metadata`` before invoking a Pipe.
        # This private, non-sensitive handoff lets the local preview read the
        # same canonical object; Story 1.1.1 replaces this execution path.
        updated["capstone_rag_options"] = options
        return updated


# Runtime discovery in Open WebUI requires the conventional ``Filter`` name.
# This descriptive alias is retained for reviewers and tests of the course contract.
CapstoneRetrievalControls = Filter
