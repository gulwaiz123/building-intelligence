"""
title: Building with RAG
id: building_with_rag_ui_preview
author: Building with RAG course
version: 0.3.1
required_open_webui_version: 0.11.4
description: Sends each chat to the capstone's Chat Completions adapter with the chosen RAG options.

Controls are owned solely by the companion Filter (the "RAG options" chip).
This Pipe forwards the latest user message and those options to
``POST {capstone_base_url}/chat/completions`` and relays the streamed text.
Each reply also gets a status line above it naming the options it used, so
the chosen settings are visible without opening the chip.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_OPTIONS = {
    "pattern": "semantic",
    "filters": {"act": [], "status": []},
    "limit": 5,
    "required_acts": None,
    "chapter": None,
}
START_HINT = "uv run uvicorn building_with_rag.app:app --reload"


def safe_options(body: dict[str, Any]) -> dict[str, Any]:
    """Whitelist the Filter's options so identity, access controls, answer
    settings, headers, or free-form filters can never be forwarded."""
    candidate = body.get("capstone_rag_options")
    if not isinstance(candidate, dict):
        return json.loads(json.dumps(DEFAULT_OPTIONS))
    filters = candidate.get("filters") if isinstance(candidate.get("filters"), dict) else {}
    return {
        "pattern": candidate.get("pattern") or "semantic",
        "filters": {
            key: list(filters[key]) if isinstance(filters.get(key), list) else [] for key in ("act", "status")
        },
        "limit": candidate.get("limit", 5),
        "required_acts": candidate.get("required_acts") or None,
        "chapter": candidate.get("chapter") or None,
    }


def latest_user_message(body: dict[str, Any]) -> str:
    for message in reversed(body.get("messages") or []):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def build_request(body: dict[str, Any], base_url: str, api_key: str) -> urllib.request.Request:
    options = safe_options(body)
    payload = {
        "model": f"rag-{options['pattern']}",
        "stream": True,
        "messages": [{"role": "user", "content": latest_user_message(body)}],
        "rag_options": options,
    }
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )


def unreachable_message(base_url: str) -> str:
    server = base_url.rstrip("/").removesuffix("/v1")
    return f"Capstone API is not reachable at {server} — start it with {START_HINT}"


def _error_message(exc: urllib.error.HTTPError) -> str:
    try:
        message = json.loads(exc.read())["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = "request failed"
    return f"Capstone API error (HTTP {exc.code}): {message}"


def relay(request: urllib.request.Request, base_url: str, timeout: float = 120) -> Iterator[str]:
    """Yield the adapter's streamed content unchanged, or one plain line."""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    return
                for choice in json.loads(data).get("choices", []):
                    content = (choice.get("delta") or {}).get("content")
                    if content:
                        yield content
    except urllib.error.HTTPError as exc:
        yield _error_message(exc)
    except OSError:
        yield unreachable_message(base_url)


def options_label(options: dict[str, Any]) -> str:
    """One short line, e.g. `semantic · 5 results · acts: all · status: all`."""
    filters = options.get("filters") or {}
    parts = [
        options["pattern"],
        f"{options['limit']} results",
        f"acts: {', '.join(filters.get('act') or []) or 'all'}",
        f"status: {', '.join(filters.get('status') or []) or 'all'}",
    ]
    if options.get("required_acts"):
        parts.append(f"required: {', '.join(options['required_acts'])}")
    if options.get("chapter"):
        parts.append(f"chapter: {options['chapter']}")
    return " · ".join(parts)


class Pipe:
    class Valves(BaseModel):
        """Admin-only connection settings; never exposed as user controls."""

        capstone_base_url: str = Field(default="http://127.0.0.1:8000/v1", title="Capstone API base URL")
        capstone_api_key: str = Field(
            default="",
            title="Capstone API key (optional)",
            json_schema_extra={"input": {"type": "password"}},
        )

    def __init__(self) -> None:
        self.type = "manifold"
        self.valves = self.Valves()

    def pipes(self) -> list[dict[str, str]]:
        return [{"id": "building-with-rag-ui-preview", "name": "Building with RAG"}]

    async def pipe(
        self,
        body: dict[str, Any],
        __event_emitter__: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> Iterator[str]:
        if __event_emitter__ is not None:
            description = f"RAG options: {options_label(safe_options(body))}"
            await __event_emitter__({"type": "status", "data": {"description": description, "done": True}})
        base_url = self.valves.capstone_base_url
        return relay(build_request(body, base_url, self.valves.capstone_api_key), base_url)


# Runtime discovery requires ``Pipe``; the alias documents the stable artifact name.
BuildingWithRagUiPreview = Pipe
