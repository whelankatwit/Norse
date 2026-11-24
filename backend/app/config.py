"""Runtime settings for Norse.

Environment variables override the defaults. No settings library is involved.
"""

import os
from pathlib import Path

# This file is backend/app/config.py. parents[2] is the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]

OLLAMA_BASE_URL = os.environ.get("NORSE_OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("NORSE_OLLAMA_MODEL", "llama3.2:3b")
DATABASE_PATH = Path(
    os.environ.get("NORSE_DATABASE_PATH", str(REPO_ROOT / "data" / "norse.db"))
)

API_HOST = os.environ.get("NORSE_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("NORSE_API_PORT", "8000"))
# Semicolon-separated absolute directories on Windows. Empty approves nothing.
WORKSPACES = os.environ.get("NORSE_WORKSPACES", "")
