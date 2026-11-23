"""SQLite persistence: conversations, messages and handoff tickets (MEM-02, HITL-01, ADR-03)."""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '', summarized_until INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL, content TEXT NOT NULL, agent TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), user_id TEXT NOT NULL,
    status TEXT NOT NULL, reason TEXT NOT NULL, context TEXT NOT NULL, operator_id TEXT, resolution TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, created_at);
CREATE TABLE IF NOT EXISTS idempotency (
    user_id TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL, response TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (user_id, key));
"""


class NotFound(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._migrate()
            self._db.commit()

    def _migrate(self) -> None:
        """Additive migrations for databases created by earlier versions (no data loss)."""
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(conversations)")}
        for name, ddl in (("summary", "TEXT NOT NULL DEFAULT ''"), ("summarized_until", "INTEGER NOT NULL DEFAULT 0")):
            if name not in columns:
                self._db.execute(f"ALTER TABLE conversations ADD COLUMN {name} {ddl}")

    def _write(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._db.execute(sql, params)
            self._db.commit()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    # --- idempotency (REQ-API-03) ----------------------------------------------------------------------
    def get_idempotent(self, user_id: str, key: str) -> dict[str, Any] | None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.IDEMPOTENCY_TTL_HOURS)).isoformat(timespec="seconds")
        rows = self._rows("SELECT request_hash, response FROM idempotency WHERE user_id=? AND key=? AND created_at >= ?",
                          (user_id, key, cutoff))
        return rows[0] if rows else None

    IDEMPOTENCY_TTL_HOURS = 24

    def save_idempotent(self, user_id: str, key: str, request_hash: str, response: str) -> None:
        """Keys live 24 h (retry window); expired rows are purged on write so the table stays bounded."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.IDEMPOTENCY_TTL_HOURS)).isoformat(timespec="seconds")
        with self._lock:
            self._db.execute("DELETE FROM idempotency WHERE created_at < ?", (cutoff,))
            self._db.execute("INSERT OR IGNORE INTO idempotency VALUES (?,?,?,?,?)",
                             (user_id, key, request_hash, response, _now()))
            self._db.commit()

    # --- conversations -------------------------------------------------------------------------------
    def open_conversation(self, conversation_id: str | None, user_id: str) -> dict[str, Any]:
        """Return the caller's conversation, or create one. Someone else's id is reported as not found."""
        if conversation_id:
            conv = self.get_conversation(conversation_id)
            if conv is None or conv["user_id"] != user_id:
                raise NotFound("conversation")
            return conv
        now = _now()
        conv = {"id": f"c_{uuid.uuid4().hex[:16]}", "user_id": user_id, "status": "active", "created_at": now, "updated_at": now}
        self._write("INSERT INTO conversations (id, user_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    tuple(conv.values()))
        return self.get_conversation(conv["id"])

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        return rows[0] if rows else None

    def set_status(self, conversation_id: str, status: str) -> None:
        self._write("UPDATE conversations SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), conversation_id))

    def set_summary(self, conversation_id: str, summary: str, summarized_until: int) -> None:
        self._write("UPDATE conversations SET summary = ?, summarized_until = ? WHERE id = ?",
                    (summary, summarized_until, conversation_id))

    def messages_between(self, conversation_id: str, after_id: int, before_id: int) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM messages WHERE conversation_id = ? AND id > ? AND id < ? ORDER BY id",
                          (conversation_id, after_id, before_id))

    def add_message(self, conversation_id: str, role: str, content: str, agent: str | None = None) -> None:
        self._write("INSERT INTO messages (conversation_id, role, content, agent, created_at) VALUES (?, ?, ?, ?, ?)",
                    (conversation_id, role, content, agent, _now()))

    def messages(self, conversation_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is None:
            return self._rows("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,))
        return self._rows("SELECT * FROM (SELECT * FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?) "
                          "ORDER BY id", (conversation_id, limit))

    # --- tickets -------------------------------------------------------------------------------------
    def create_ticket(self, conversation_id: str, user_id: str, reason: str, context: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        ticket = {"id": f"HO-{uuid.uuid4().hex[:8].upper()}", "conversation_id": conversation_id, "user_id": user_id,
                  "status": "open", "reason": reason, "context": json.dumps(context, ensure_ascii=False),
                  "operator_id": None, "resolution": None, "created_at": now, "updated_at": now}
        self._write("INSERT INTO tickets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(ticket.values()))
        return self.get_ticket(ticket["id"])

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        return self._decode(rows[0]) if rows else None

    def list_tickets(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._rows("SELECT * FROM tickets WHERE status = ? ORDER BY created_at", (status,))
        else:
            rows = self._rows("SELECT * FROM tickets ORDER BY created_at")
        return [self._decode(r) for r in rows]

    def active_ticket(self, conversation_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM tickets WHERE conversation_id = ? AND status IN ('open', 'claimed') "
                          "ORDER BY created_at DESC LIMIT 1", (conversation_id,))
        return self._decode(rows[0]) if rows else None

    def update_ticket(self, ticket_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._write(f"UPDATE tickets SET {cols}, updated_at = ? WHERE id = ?", (*fields.values(), _now(), ticket_id))

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "context": json.loads(row["context"])}
