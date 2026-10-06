"""Load the course Functions and the Building with RAG model into a running Open WebUI.

Called by setup_open_webui.sh. Idempotent: re-running updates the Function
source and fixes model settings without touching chats.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("OPEN_WEBUI_URL", "http://127.0.0.1:8080")
HERE = Path(__file__).resolve().parent

FUNCTIONS = {
    "capstone_retrieval_controls": ("RAG options", "Governed RAG options for the Building with RAG model."),
    "building_with_rag_ui_preview": (
        "Building with RAG",
        "Sends each chat to the capstone's Chat Completions adapter with the chosen RAG options.",
    ),
}
PIPE_MODEL_ID = "building_with_rag_ui_preview.building-with-rag-ui-preview"
PREVIEW_MODEL_ID = "building-with-rag-ui-preview"
FILTER_ID = "capstone_retrieval_controls"


def call(path: str, data: dict | None = None, token: str | None = None) -> dict | None:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(BASE + path, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 404):
            return None
        raise


def main() -> None:
    # WEBUI_AUTH=false: empty sign-in returns the single local admin.
    token = call("/api/v1/auths/signin", {"email": "", "password": ""})["token"]

    for function_id, (name, description) in FUNCTIONS.items():
        form = {
            "id": function_id,
            "name": name,
            "meta": {"description": description},
            "content": (HERE / f"{function_id}.py").read_text(encoding="utf-8"),
        }
        if call(f"/api/v1/functions/id/{function_id}", token=token) is None:
            call("/api/v1/functions/create", form, token)
        else:
            call(f"/api/v1/functions/id/{function_id}/update", form, token)
        if not call(f"/api/v1/functions/id/{function_id}", token=token)["is_active"]:
            call(f"/api/v1/functions/id/{function_id}/toggle", {}, token)
        print(f"Function ready: {name}")

    preview = {
        "id": PREVIEW_MODEL_ID,
        "base_model_id": PIPE_MODEL_ID,
        "name": "Building with RAG",
        "meta": {
            "profile_image_url": "/static/favicon.png",
            "description": "Chats with the capstone app using the RAG options chip.",
            "filterIds": [FILTER_ID],
            "defaultFilterIds": [FILTER_ID],
        },
        "params": {},
    }
    hidden_pipe = {"id": PIPE_MODEL_ID, "name": "Building with RAG", "meta": {"hidden": True}, "params": {}}
    for model in (preview, hidden_pipe):
        if call(f"/api/v1/models/model?id={model['id']}", token=token) is None:
            call("/api/v1/models/create", model, token)
        else:
            call("/api/v1/models/model/update", model, token)

    models = call("/api/models?refresh=true", token=token)["data"]
    ready = any(
        m["id"] == PREVIEW_MODEL_ID and any(f.get("has_user_valves") for f in m.get("filters", []))
        for m in models
    )
    if not ready:
        raise SystemExit("Building with RAG model or RAG options chip not found; check ~/open-webui/server.log")
    print("Building with RAG model ready with the RAG options chip.")


if __name__ == "__main__":
    main()
