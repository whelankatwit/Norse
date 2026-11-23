# Norse

Norse is a personal AI assistant that runs on this computer. Model inference stays local through Ollama. The backend stores conversations in SQLite and sends that history to Ollama when a conversation continues. Long-term memory, tools, and the React frontend come later.

## Prerequisites

- Python 3.13
- Node.js 22, for the frontend in a later milestone
- [Ollama](https://ollama.com/), with the `llama3.2:3b` model, for `POST /chat`

## Layout

- `backend/` — FastAPI application
- `frontend/` — reserved for the React app
- `data/` — local runtime files, including the SQLite database

## Run the API

From `backend/`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health). A healthy server returns `{"status":"ok"}`.

## Chat

`POST /chat` sends one message to the local Ollama model and returns the reply. The first reply can take longer while Ollama loads the model.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/chat -ContentType "application/json" -Body '{"message":"Say hello in one sentence."}'
```

A successful response looks like `{"message":"..."}`. `POST /chat` does not save the exchange. Interactive docs are at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## Conversations

Conversations and their messages are stored in the SQLite file from `NORSE_DATABASE_PATH`. Restarting the API keeps that file.

- `POST /conversations` creates a conversation. The body may include `title`.
- `GET /conversations` lists conversations, most recently updated first.
- `GET /conversations/{conversation_id}` returns the conversation and its messages, oldest first.
- `DELETE /conversations/{conversation_id}` deletes the conversation and its messages.
- `POST /conversations/{conversation_id}/messages` with `{"message":"..."}` sends the saved history plus the new message to Ollama, then stores both turns.

## Configuration

`backend/app/config.py` holds the Ollama URL, model tag, SQLite path, and API host and port. Set an environment variable to override a default.

| Variable | Default |
| --- | --- |
| `NORSE_OLLAMA_BASE_URL` | `http://localhost:11434` |
| `NORSE_OLLAMA_MODEL` | `llama3.2:3b` |
| `NORSE_DATABASE_PATH` | `<repo>/data/norse.db` |
| `NORSE_API_HOST` | `127.0.0.1` |
| `NORSE_API_PORT` | `8000` |
