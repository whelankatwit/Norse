"""SQLite storage for conversations and messages."""

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import DATABASE_PATH

DEFAULT_TITLE = "New conversation"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON messages (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source_conversation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class ConversationNotFoundError(Exception):
    """The conversation id is not in the database."""


class MemoryNotFoundError(Exception):
    """The memory id is not in the database."""


class DuplicateMemoryError(Exception):
    """A memory with the same fact is already stored."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, and always close it."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    """Create tables when they are missing. Existing rows stay in place."""
    with connect() as connection:
        connection.executescript(SCHEMA)


def create_conversation(title: str | None) -> dict[str, str]:
    conversation_id = _new_id()
    timestamp = _utc_now()
    stored_title = title if title else DEFAULT_TITLE
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO conversations (id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, stored_title, timestamp, timestamp),
        )
        row = connection.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,),
        ).fetchone()
        return dict(row)


def list_conversations() -> list[dict[str, str]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            ORDER BY updated_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_conversation(conversation_id: str) -> dict[str, str] | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,),
        ).fetchone()
        return dict(row) if row is not None else None


def list_messages(conversation_id: str) -> list[dict[str, str]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, role, content, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            (conversation_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def delete_conversation(conversation_id: str) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        return cursor.rowcount == 1


def save_exchange(
    conversation_id: str,
    user_content: str,
    assistant_content: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Store a completed user/assistant turn and mark the conversation updated."""
    user_id = _new_id()
    assistant_id = _new_id()
    user_time = _utc_now()
    assistant_time = _utc_now()
    with connect() as connection:
        existing = connection.execute(
            "SELECT id FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if existing is None:
            raise ConversationNotFoundError(conversation_id)

        connection.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, created_at)
            VALUES (?, ?, 'user', ?, ?)
            """,
            (user_id, conversation_id, user_content, user_time),
        )
        connection.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, created_at)
            VALUES (?, ?, 'assistant', ?, ?)
            """,
            (assistant_id, conversation_id, assistant_content, assistant_time),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (assistant_time, conversation_id),
        )
        user_row = connection.execute(
            "SELECT id, role, content, created_at FROM messages WHERE id = ?",
            (user_id,),
        ).fetchone()
        assistant_row = connection.execute(
            "SELECT id, role, content, created_at FROM messages WHERE id = ?",
            (assistant_id,),
        ).fetchone()
        return dict(user_row), dict(assistant_row)


def normalize_fact(content: str) -> str:
    """Compare facts without surrounding space, repeated spaces, or letter case."""
    return " ".join(content.split()).casefold()


def _memory_content_exists(
    connection: sqlite3.Connection,
    content: str,
    exclude_id: str | None = None,
) -> bool:
    rows = connection.execute("SELECT id, content FROM memories").fetchall()
    key = normalize_fact(content)
    return any(
        normalize_fact(row["content"]) == key and row["id"] != exclude_id for row in rows
    )


def list_memories() -> list[dict[str, str | None]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, content, source_conversation_id, created_at, updated_at
            FROM memories
            ORDER BY updated_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def _insert_memory(
    connection: sqlite3.Connection,
    content: str,
    source_conversation_id: str | None,
) -> dict[str, str | None]:
    memory_id = _new_id()
    timestamp = _utc_now()
    connection.execute(
        """
        INSERT INTO memories (
            id, content, source_conversation_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (memory_id, content, source_conversation_id, timestamp, timestamp),
    )
    row = connection.execute(
        """
        SELECT id, content, source_conversation_id, created_at, updated_at
        FROM memories
        WHERE id = ?
        """,
        (memory_id,),
    ).fetchone()
    return dict(row)


def create_memory(content: str) -> dict[str, str | None]:
    cleaned = " ".join(content.split())
    with connect() as connection:
        if _memory_content_exists(connection, cleaned):
            raise DuplicateMemoryError(cleaned)
        return _insert_memory(connection, cleaned, None)


def add_memories(
    contents: list[str],
    source_conversation_id: str | None,
) -> list[dict[str, str | None]]:
    """Insert facts that are not already stored. Existing rows stay unchanged."""
    if not contents:
        return []
    with connect() as connection:
        inserted: list[dict[str, str | None]] = []
        for content in contents:
            cleaned = " ".join(content.split())
            if not cleaned or _memory_content_exists(connection, cleaned):
                continue
            inserted.append(_insert_memory(connection, cleaned, source_conversation_id))
        return inserted


def update_memory(memory_id: str, content: str) -> dict[str, str | None]:
    cleaned = " ".join(content.split())
    timestamp = _utc_now()
    with connect() as connection:
        existing = connection.execute(
            "SELECT id FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        if existing is None:
            raise MemoryNotFoundError(memory_id)
        if _memory_content_exists(connection, cleaned, exclude_id=memory_id):
            raise DuplicateMemoryError(cleaned)
        connection.execute(
            """
            UPDATE memories
            SET content = ?, updated_at = ?
            WHERE id = ?
            """,
            (cleaned, timestamp, memory_id),
        )
        row = connection.execute(
            """
            SELECT id, content, source_conversation_id, created_at, updated_at
            FROM memories
            WHERE id = ?
            """,
            (memory_id,),
        ).fetchone()
        return dict(row)


def delete_memory(memory_id: str) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM memories WHERE id = ?",
            (memory_id,),
        )
        return cursor.rowcount == 1
