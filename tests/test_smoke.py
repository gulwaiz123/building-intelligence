"""Minimal smoke tests: seed behavior only (health, placeholders, models, chat)."""

import pytest
from fastapi.testclient import TestClient

from building_with_rag.app import create_app
from building_with_rag.registry import PATTERN_MODEL_IDS


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "pattern,model_id", [(p, m) for p, m in PATTERN_MODEL_IDS.items() if p.value not in ("semantic", "hybrid", "hybrid-reranked")]
)
def test_query_placeholder_per_mode(client: TestClient, pattern: str, model_id: str) -> None:
    response = client.post(
        "/v1/query", json={"question": "What is theft?", "pattern": pattern.value}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pattern"] == pattern.value
    assert body["status"] == "not_implemented"
    assert body["results"] == []


def test_models_lists_six(client: TestClient) -> None:
    response = client.get("/v1/models")
    assert response.status_code == 200
    ids = [m["id"] for m in response.json()["data"]]
    assert ids == list(PATTERN_MODEL_IDS.values())


def test_chat_json_placeholder(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"model": "rag-structured", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert "not implemented" in body["choices"][0]["message"]["content"]


def test_chat_stream_placeholder(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "rag-structured",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        raw = b"".join(response.iter_bytes()).decode()
    assert "chat.completion.chunk" in raw
    assert '"finish_reason": "stop"' in raw
    done = "[" + "DONE" + "]"
    assert raw.strip().endswith("data: " + done)


def test_chat_unknown_model_error(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-bogus", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["type"] == "invalid_request_error"
