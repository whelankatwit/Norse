# Norse

Norse is a personal AI assistant that runs on this computer. Model inference stays local through Ollama. This milestone is the application foundation: a FastAPI backend with a health check. Conversation, memory, tools, and the React frontend come later.

## Prerequisites

- Python 3.13
- Node.js 22, for the frontend in a later milestone
- [Ollama](https://ollama.com/), with the `llama3.2` model, for a later milestone

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

## Configuration

`backend/app/config.py` holds the Ollama URL, model tag, SQLite path, and API host and port. Set an environment variable to override a default.

| Variable | Default |
| --- | --- |
| `NORSE_OLLAMA_BASE_URL` | `http://localhost:11434` |
| `NORSE_OLLAMA_MODEL` | `llama3.2` |
| `NORSE_DATABASE_PATH` | `<repo>/data/norse.db` |
| `NORSE_API_HOST` | `127.0.0.1` |
| `NORSE_API_PORT` | `8000` |
