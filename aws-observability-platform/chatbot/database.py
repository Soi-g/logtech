"""
SQLite 대화 저장소
- conversations: 대화 목록
- messages: 대화별 메시지 (히스토리)
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "chatbot.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );
        """)


# ── Conversations ──────────────────────────────────────────────

def list_conversations() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def create_conversation(first_message: str = "") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    cid = str(uuid.uuid4())
    title = first_message[:40] + "..." if len(first_message) > 40 else first_message or "새 대화"
    with _conn() as conn:
        conn.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            (cid, title, now, now)
        )
    return {"id": cid, "title": title, "created_at": now, "updated_at": now}


def get_conversation(cid: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=?", (cid,)
        ).fetchone()
    return dict(row) if row else None


def delete_conversation(cid: str):
    with _conn() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        conn.execute("DELETE FROM conversations WHERE id=?", (cid,))


def _touch_conversation(cid: str):
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?", (now, cid)
        )


# ── Messages ───────────────────────────────────────────────────

def get_messages(cid: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at",
            (cid,)
        ).fetchall()
    return [dict(r) for r in rows]


def add_message(cid: str, role: str, content: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    mid = str(uuid.uuid4())
    with _conn() as conn:
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
            (mid, cid, role, content, now)
        )
    _touch_conversation(cid)
    return {"id": mid, "conversation_id": cid, "role": role, "content": content, "created_at": now}
