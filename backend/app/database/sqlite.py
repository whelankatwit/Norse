"""SQLite storage for conversations and messages."""

import hashlib
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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

CREATE TABLE IF NOT EXISTS submission_keys (
    conversation_id TEXT NOT NULL,
    submission_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'failed')),
    owner TEXT NOT NULL,
    user_message_id TEXT,
    assistant_message_id TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, submission_key),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

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


class OwnershipLost(Exception):
    """This caller no longer owns the submission."""


class SavedExchangeMissingError(Exception):
    """A completed submission points at messages that are not stored."""


SUBMISSION_STALE_AFTER = timedelta(minutes=15)


@dataclass(frozen=True)
class SubmissionClaim:
    """What the caller may do with one conversation-scoped submission key."""

    action: str
    owner: str = ""
    user_message_id: str | None = None
    assistant_message_id: str | None = None


def fingerprint_message(message: str) -> str:
    """Return the SHA-256 hex digest of the exact message text."""
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


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


def claim_submission(
    conversation_id: str,
    submission_key: str,
    fingerprint: str,
    *,
    recover: bool,
) -> SubmissionClaim:
    """Claim a submission, or report its state when this call is read-only.

    An execution claim can take over a pending row once it is at least 15 minutes
    old. That starts another model call even if the older request is still running.
    Later writes from the older owner no longer match, so it cannot save a reply
    or mark the newer owner's row failed.
    """
    with connect() as connection:
        _begin_immediate(connection)
        if recover:
            return _read_submission(connection, conversation_id, submission_key, fingerprint)
        return _claim_for_execution(connection, conversation_id, submission_key, fingerprint)


def complete_submission(
    conversation_id: str,
    submission_key: str,
    owner: str,
    user_content: str,
    assistant_content: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Save both messages and mark this owner's submission completed, or roll back."""
    user_id = _new_id()
    assistant_id = _new_id()
    user_time = _utc_now()
    assistant_time = _utc_now()
    with connect() as connection:
        _begin_immediate(connection)
        existing = connection.execute(
            "SELECT id FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if existing is None:
            raise ConversationNotFoundError(conversation_id)
        cursor = connection.execute(
            """
            UPDATE submission_keys
            SET status = 'completed',
                user_message_id = ?,
                assistant_message_id = ?,
                updated_at = ?
            WHERE conversation_id = ? AND submission_key = ? AND owner = ? AND status = 'pending'
            """,
            (user_id, assistant_id, assistant_time, conversation_id, submission_key, owner),
        )
        if cursor.rowcount != 1:
            raise OwnershipLost(submission_key)
        _insert_exchange(
            connection,
            conversation_id,
            user_id,
            assistant_id,
            user_content,
            assistant_content,
            user_time,
            assistant_time,
        )
        return _message_pair(connection, user_id, assistant_id)


def mark_submission_failed(conversation_id: str, submission_key: str, owner: str) -> bool:
    """Mark this owner's pending submission failed. A lost owner changes nothing."""
    with connect() as connection:
        _begin_immediate(connection)
        cursor = connection.execute(
            """
            UPDATE submission_keys
            SET status = 'failed', updated_at = ?
            WHERE conversation_id = ? AND submission_key = ? AND owner = ? AND status = 'pending'
            """,
            (_utc_now(), conversation_id, submission_key, owner),
        )
        return cursor.rowcount == 1


def load_saved_exchange(
    conversation_id: str,
    user_message_id: str | None,
    assistant_message_id: str | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Load the message pair recorded on a completed submission."""
    with connect() as connection:
        if user_message_id is None or assistant_message_id is None:
            raise SavedExchangeMissingError(conversation_id)
        user_row = connection.execute(
            """
            SELECT id, role, content, created_at
            FROM messages
            WHERE id = ? AND conversation_id = ? AND role = 'user'
            """,
            (user_message_id, conversation_id),
        ).fetchone()
        assistant_row = connection.execute(
            """
            SELECT id, role, content, created_at
            FROM messages
            WHERE id = ? AND conversation_id = ? AND role = 'assistant'
            """,
            (assistant_message_id, conversation_id),
        ).fetchone()
        if user_row is None or assistant_row is None:
            raise SavedExchangeMissingError(conversation_id)
        return dict(user_row), dict(assistant_row)


def _read_submission(
    connection: sqlite3.Connection,
    conversation_id: str,
    submission_key: str,
    fingerprint: str,
) -> SubmissionClaim:
    row = _get_submission(connection, conversation_id, submission_key)
    if row is None:
        return SubmissionClaim("request_in_progress")
    if row["fingerprint"] != fingerprint:
        return SubmissionClaim("idempotency_conflict")
    if row["status"] == "completed":
        return _replay_claim(row)
    if row["status"] == "failed":
        return SubmissionClaim("request_failed")
    return SubmissionClaim("request_in_progress")


def _claim_for_execution(
    connection: sqlite3.Connection,
    conversation_id: str,
    submission_key: str,
    fingerprint: str,
) -> SubmissionClaim:
    for _ in range(2):
        row = _get_submission(connection, conversation_id, submission_key)
        if row is None:
            owner = _new_id()
            try:
                connection.execute(
                    """
                    INSERT INTO submission_keys (
                        conversation_id, submission_key, fingerprint, status, owner, updated_at
                    )
                    VALUES (?, ?, ?, 'pending', ?, ?)
                    """,
                    (conversation_id, submission_key, fingerprint, owner, _utc_now()),
                )
            except sqlite3.IntegrityError:
                if not _conversation_exists(connection, conversation_id):
                    raise ConversationNotFoundError(conversation_id) from None
                continue
            return SubmissionClaim("run", owner=owner)
        if row["fingerprint"] != fingerprint:
            return SubmissionClaim("idempotency_conflict")
        if row["status"] == "completed":
            return _replay_claim(row)
        if row["status"] == "failed":
            owner = _new_id()
            cursor = connection.execute(
                """
                UPDATE submission_keys
                SET status = 'pending', owner = ?, updated_at = ?,
                    user_message_id = NULL, assistant_message_id = NULL
                WHERE conversation_id = ? AND submission_key = ? AND status = 'failed'
                  AND fingerprint = ?
                """,
                (owner, _utc_now(), conversation_id, submission_key, fingerprint),
            )
            if cursor.rowcount == 1:
                return SubmissionClaim("run", owner=owner)
            continue
        if row["status"] == "pending" and _is_stale(row["updated_at"]):
            owner = _new_id()
            cursor = connection.execute(
                """
                UPDATE submission_keys
                SET owner = ?, updated_at = ?,
                    user_message_id = NULL, assistant_message_id = NULL
                WHERE conversation_id = ? AND submission_key = ? AND status = 'pending'
                  AND fingerprint = ? AND updated_at = ?
                """,
                (owner, _utc_now(), conversation_id, submission_key, fingerprint, row["updated_at"]),
            )
            if cursor.rowcount == 1:
                return SubmissionClaim("run", owner=owner)
            continue
        return SubmissionClaim("request_in_progress")
    return SubmissionClaim("request_in_progress")


def _replay_claim(row: sqlite3.Row) -> SubmissionClaim:
    return SubmissionClaim(
        "replay",
        owner=row["owner"],
        user_message_id=row["user_message_id"],
        assistant_message_id=row["assistant_message_id"],
    )


def _get_submission(
    connection: sqlite3.Connection,
    conversation_id: str,
    submission_key: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT fingerprint, status, owner, user_message_id, assistant_message_id, updated_at
        FROM submission_keys
        WHERE conversation_id = ? AND submission_key = ?
        """,
        (conversation_id, submission_key),
    ).fetchone()


def _conversation_exists(connection: sqlite3.Connection, conversation_id: str) -> bool:
    row = connection.execute(
        "SELECT id FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    return row is not None


def _begin_immediate(connection: sqlite3.Connection) -> None:
    """Start the write transaction before SQLite's implicit deferred transaction."""
    connection.isolation_level = None
    connection.execute("BEGIN IMMEDIATE")


def _is_stale(updated_at: str) -> bool:
    try:
        stamp = datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        return False
    return datetime.now(timezone.utc) - stamp >= SUBMISSION_STALE_AFTER


def _insert_exchange(
    connection: sqlite3.Connection,
    conversation_id: str,
    user_id: str,
    assistant_id: str,
    user_content: str,
    assistant_content: str,
    user_time: str,
    assistant_time: str,
) -> None:
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


def _message_pair(
    connection: sqlite3.Connection,
    user_id: str,
    assistant_id: str,
) -> tuple[dict[str, str], dict[str, str]]:
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
