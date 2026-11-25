"""SQLite persistence: conversations, messages, summaries, tickets, idempotency keys and knowledge chunks (ADR-06)."""

import json
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
    summarized_until INTEGER NOT NULL DEFAULT 0, clarifications INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
    content TEXT NOT NULL, agent TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_messages ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, user_id TEXT NOT NULL, status TEXT NOT NULL,
    reason TEXT NOT NULL, context TEXT NOT NULL, operator_id TEXT, resolution TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency (
    user_id TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL, response TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (user_id, key));
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL, position INTEGER NOT NULL, text TEXT NOT NULL);
"""

IDEMPOTENCY_TTL = timedelta(hours=24)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def _exec(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._db.execute(sql, params)
            self._db.commit()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    # --- conversations (MEM-01) ---------------------------------------------------------------------
    def open_conversation(self, conversation_id: str | None, user_id: str) -> dict[str, Any]:
        if conversation_id:
            conv = self.get_conversation(conversation_id)
            if conv is None or conv["user_id"] != user_id:  # foreign ids are indistinguishable from missing
                raise NotFound(conversation_id)
            return conv
        cid = f"c_{secrets.token_hex(8)}"
        ts = now_iso()
        self._exec("INSERT INTO conversations (id, user_id, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                   (cid, user_id, "active", ts, ts))
        return self.get_conversation(cid)

    def get_conversation(self, cid: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM conversations WHERE id=?", (cid,))
        return rows[0] if rows else None

    def update_conversation(self, cid: str, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE conversations SET {cols}, updated_at=? WHERE id=?", (*fields.values(), now_iso(), cid))

    def add_message(self, cid: str, role: str, content: str, agent: str | None = None) -> None:
        self._exec("INSERT INTO messages (conversation_id, role, content, agent, created_at) VALUES (?,?,?,?,?)",
                   (cid, role, content, agent, now_iso()))

    def messages(self, cid: str) -> list[dict[str, Any]]:
        return self._rows("SELECT id, role, content, agent, created_at FROM messages WHERE conversation_id=? ORDER BY id",
                          (cid,))

    # --- tickets (HITL-01..03) ----------------------------------------------------------------------
    def create_ticket(self, cid: str, user_id: str, reason: str, context: dict[str, Any]) -> str:
        tid = f"HO-{secrets.token_hex(4).upper()}"
        ts = now_iso()
        self._exec("INSERT INTO tickets (id, conversation_id, user_id, status, reason, context, created_at, updated_at)"
                   " VALUES (?,?,?,?,?,?,?,?)", (tid, cid, user_id, "open", reason, json.dumps(context, default=str), ts, ts))
        return tid

    def get_ticket(self, tid: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM tickets WHERE id=?", (tid,))
        return self._ticket(rows[0]) if rows else None

    def active_ticket(self, cid: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM tickets WHERE conversation_id=? AND status IN ('open','claimed') "
                          "ORDER BY created_at DESC LIMIT 1", (cid,))
        return self._ticket(rows[0]) if rows else None

    def list_tickets(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._rows("SELECT * FROM tickets WHERE status=? ORDER BY created_at", (status,))
        else:
            rows = self._rows("SELECT * FROM tickets ORDER BY created_at")
        return [self._ticket(r) for r in rows]

    def update_ticket(self, tid: str, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE tickets SET {cols}, updated_at=? WHERE id=?", (*fields.values(), now_iso(), tid))

    @staticmethod
    def _ticket(row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "context": json.loads(row["context"])}

    # --- idempotency (API-02) -----------------------------------------------------------------------
    def get_idempotent(self, user_id: str, key: str) -> dict[str, Any] | None:
        cutoff = (datetime.now(timezone.utc) - IDEMPOTENCY_TTL).isoformat(timespec="seconds")
        rows = self._rows("SELECT request_hash, response FROM idempotency WHERE user_id=? AND key=? AND created_at>=?",
                          (user_id, key, cutoff))
        return rows[0] if rows else None

    def save_idempotent(self, user_id: str, key: str, request_hash: str, response: str) -> None:
        cutoff = (datetime.now(timezone.utc) - IDEMPOTENCY_TTL).isoformat(timespec="seconds")
        with self._lock:
            self._db.execute("DELETE FROM idempotency WHERE created_at<?", (cutoff,))
            self._db.execute("INSERT OR REPLACE INTO idempotency VALUES (?,?,?,?,?)",
                             (user_id, key, request_hash, response, now_iso()))
            self._db.commit()

    # --- knowledge chunks (KNW-03, KNW-06) ----------------------------------------------------------
    def chunk_ids(self) -> set[str]:
        return {r["id"] for r in self._rows("SELECT id FROM chunks")}

    def add_chunks(self, rows: list[tuple[str, str, str, int, str]]) -> None:
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?)", rows)
            self._db.commit()

    def delete_chunks(self, ids: list[str]) -> None:
        with self._lock:
            self._db.executemany("DELETE FROM chunks WHERE id=?", [(i,) for i in ids])
            self._db.commit()

    def all_chunks(self) -> list[dict[str, Any]]:
        return self._rows("SELECT id, url, title, position, text FROM chunks ORDER BY url, position")
