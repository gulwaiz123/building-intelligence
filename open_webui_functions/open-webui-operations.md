# Open WebUI operations

## First-time setup (participants)

**Windows, classroom (no internet needed):** the trainer runs
`sh open_webui_functions/build_offline_bundle.sh` once (downloads about
700 MB into `~/open-webui-bundle`) and shares that folder on the LAN. Each
participant copies the folder to their PC and double-clicks
`setup_open_webui.cmd` inside it. Afterwards use
`manage_open_webui.cmd start|stop|status` from the same folder.

**Windows, own internet:** double-click `open_webui_functions/setup_open_webui.cmd`.

**macOS/Linux:** from the repository root run
`sh open_webui_functions/setup_open_webui.sh` (needs `uv` or Python 3.11).

It installs Open WebUI 0.11.4 into `~/open-webui`, writes its settings,
starts it, and loads the `RAG options` controls and preview model. Re-running
is safe and keeps chats.

Run these commands from the repository root. They control the pinned,
loopback-only Open WebUI installation at `$HOME/open-webui`.

```sh
sh open_webui_functions/manage_open_webui.sh start
sh open_webui_functions/manage_open_webui.sh status
sh open_webui_functions/manage_open_webui.sh stop
sh open_webui_functions/manage_open_webui.sh restart
```

The script records only the process it starts in `$HOME/open-webui/server.pid`.
It refuses to kill an unverified PID or a different process that happens to use
port 8080. Its log is `$HOME/open-webui/server.log`.

To use another isolated installation, set `WEBUI_HOME` for one command:

```sh
WEBUI_HOME=/path/to/open-webui sh open_webui_functions/manage_open_webui.sh status
```

Open the UI at `http://127.0.0.1:8080` (not `localhost`; live updates are
only allowed from `127.0.0.1`).

## Using the RAG options controls

1. In **Admin Panel → Functions**, keep `RAG options` (Filter) and
   `Building with RAG` (Pipe) enabled. After changing either
   `.py` file, paste it into **Function Menu → Edit** and save; Open WebUI
   keeps its own copy and does not reload from this repository.
2. The direct Pipe model is hidden in **Workspace → Models**. Use only the
   visible `Building with RAG` model, which has `RAG options` as
   a Filter and Default Filter.
   Its admin-only Valves (**Admin Panel → Functions → Building with RAG →
   Valves**) hold `capstone_base_url` (default `http://127.0.0.1:8000/v1`)
   and the optional `capstone_api_key` (the app's `CAPSTONE_API_KEY`).
3. Start a new chat with that model. The `RAG options` chip sits beside the
   `+` button in the prompt box. Click it to open the controls, set them,
   save, and send a question. Hover the chip and click `×` to turn it off;
   re-enable it from the integrations menu next to `+`.

## Safe cleanup

If you want to remove this incomplete local shell, first stop the server, then
in **Admin Panel → Functions** disable/delete `RAG options`
and `Building with RAG`; finally delete the custom preview model
from **Workspace → Models**. This does not require deleting
`$HOME/open-webui/data`.

## Reading answers

- `DRAFT — checking evidence` starts every attempt; the text streams while it is still being checked, so a draft cannot be retracted once shown.
- `Check failed: … Retrying (attempt 2 of 2)…` means the first draft failed a check; the second draft follows.
- `Evidence check passed — confidence: high` plus a `Sources:` list marks the checked answer.
- `DRAFT — low confidence, not the final answer.` means the final draft failed the check; treat it as unverified.
- Pairing: the Pipe Valve `capstone_api_key` must equal the app's `CAPSTONE_API_KEY` when that is set.
- After editing a Pipe or Filter `.py` file, re-paste it via **Function Menu → Edit** and save.
