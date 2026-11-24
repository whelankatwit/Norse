# Norse

Norse is a personal AI assistant that runs on this computer. Model inference stays local through Ollama. The backend stores conversations and durable facts about you in SQLite. A continued conversation receives its history and those facts. The React app is the interface for those conversations and facts.

## Prerequisites

- Python 3.13
- Node.js 22, for the React frontend
- [Ollama](https://ollama.com/), with the `llama3.2:3b` model, for `POST /chat`

## Layout

- `backend/` — FastAPI application
- `frontend/` — React app
- `data/` — local runtime files, including the SQLite database

## Run the API

From `backend/`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health). A healthy server returns `{"status":"ok"}`.

## Run the frontend

Start the API first. From `frontend/`:

```powershell
npm install
npm run dev
```

Open [http://127.0.0.1:5173/](http://127.0.0.1:5173/). The dev server proxies `/conversations`, `/memories`, and `/health` to the API on port 8000. The page lists conversations, continues the selected one, and edits durable facts. A reply can show which read-only tools ran. Those lines stay on the page until it is reloaded. If a reply fails, the message stays on screen and Retry sends that same submission again. While a reply is still being generated, the page checks that submission without starting another model run. `POST /chat` is not used by the page.

## Chat

`POST /chat` sends one message to the local Ollama model and returns the reply. The first reply can take longer while Ollama loads the model.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/chat -ContentType "application/json" -Body '{"message":"Say hello in one sentence."}'
```

A successful response looks like `{"message":"..."}`. `POST /chat` does not save the exchange and does not read or write long-term memories. Interactive docs are at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## Conversations

Conversations and their messages are stored in the SQLite file from `NORSE_DATABASE_PATH`. Restarting the API keeps that file.

- `POST /conversations` creates a conversation. The body may include `title`.
- `GET /conversations` lists conversations, most recently updated first.
- `GET /conversations/{conversation_id}` returns the conversation and its messages, oldest first.
- `DELETE /conversations/{conversation_id}` deletes the conversation, its messages, and its submission records.
- `POST /conversations/{conversation_id}/messages` requires `message` and `idempotency_key`. It may include `recover`. The key belongs to that conversation. Norse stores a SHA-256 fingerprint of the message text. The same key and message return the saved exchange with `replayed` set to true, without calling Ollama again. A different message for that key returns 409 `idempotency_conflict`. A submission that is still pending returns 409 `request_in_progress`. `recover: true` only reports that state: it does not start the model, retry a failure, or take ownership. A failed submission returns 409 `request_failed` to a recovery request. An execution request can retry it.
- The same request sends the saved history and durable facts to Ollama, then stores both turns. Facts the user explicitly states may be added to long-term memory. The fact list itself is not stored in the transcript. The request may run read-only workspace tools first. `tool_uses` lists the tools that ran, and `tool_results` contains the handler output from the tools that succeeded. An explicit request to use, call, or run a tool is kept only when that tool succeeds. Tool messages are not stored. If Ollama cannot answer, the response is an error object with a `code` such as `empty_reply`, `invalid_ollama_response`, or `ollama_http_error`, and the exchange is not saved.
- An execution request can take over a pending submission once it is 15 minutes old. That can start another model call while the older request is still running. The older request cannot save its reply, mark the newer submission failed, or run memory extraction for an exchange it did not save.

## Memories

Durable facts live in the `memories` table, separate from conversation messages. Deleting a conversation leaves those facts in place.

- `GET /memories` lists facts, most recently updated first.
- `POST /memories` with `{"content":"..."}` stores a fact directly.
- `PATCH /memories/{memory_id}` with `{"content":"..."}` edits one fact.
- `DELETE /memories/{memory_id}` removes one fact.

A conflicting statement does not change the stored fact. The message response includes it in `memory_conflicts` so it can be edited with `PATCH`.

Memory extraction stays best-effort. If the API stops after the reply is saved and before extraction finishes, that extraction is not retried. The saved conversation is unchanged.

## Configuration

`backend/app/config.py` holds the Ollama URL, model tag, SQLite path, and API host and port. Set an environment variable to override a default.

| Variable | Default |
| --- | --- |
| `NORSE_OLLAMA_BASE_URL` | `http://localhost:11434` |
| `NORSE_OLLAMA_MODEL` | `llama3.2:3b` |
| `NORSE_DATABASE_PATH` | `<repo>/data/norse.db` |
| `NORSE_API_HOST` | `127.0.0.1` |
| `NORSE_API_PORT` | `8000` |
| `NORSE_WORKSPACES` | empty |

`NORSE_WORKSPACES` is a list of absolute directories, separated by `;` on Windows. A directory is not approved until it is listed here. Conversation messages can use the read-only tools on these directories. `POST /chat` does not.
