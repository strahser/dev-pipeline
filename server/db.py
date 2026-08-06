# -*- coding: utf-8 -*-
"""SQLite-хранилище координации сервера: события, сообщения, агенты.

ВАЖНО: хранит ТОЛЬКО координацию (лента, inbox, heartbeat). Задачи и артефакты
остаются в файлах+git проекта (источник правды). Схема согласована с docs/architecture.md.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  from_ TEXT NOT NULL,
  "to" TEXT NOT NULL,
  project TEXT NOT NULL DEFAULT '',
  task TEXT DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  delivery TEXT NOT NULL DEFAULT 'queued'
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_ TEXT NOT NULL,
  "to" TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  delivery TEXT NOT NULL DEFAULT 'queued'
);
CREATE TABLE IF NOT EXISTS agents (
  name TEXT PRIMARY KEY,
  last_seen TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'online'
);
CREATE INDEX IF NOT EXISTS idx_events_to_delivery ON events("to", delivery);
CREATE INDEX IF NOT EXISTS idx_messages_to_delivery ON messages("to", delivery);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._lock = sqlite3.connect(self.db_path)  # отдельное соединение для write-lock

    def close(self):
        self._conn.close()
        self._lock.close()

    # --- события ----------------------------------------------------------

    def add_event(self, type_: str, from_: str, to: str, project: str = "",
                  task: str = "", payload: dict | None = None) -> dict:
        row = {
            "id": None, "type": type_, "from": from_, "to": to,
            "project": project, "task": task,
            "payload": payload or {}, "created_at": now_iso(), "delivery": "queued",
        }
        cur = self._conn.execute(
            "INSERT INTO events (type,from_,\"to\",project,task,payload,created_at,delivery) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (type_, from_, to, project, task, json.dumps(payload or {}, ensure_ascii=False),
             row["created_at"], "queued"))
        self._conn.commit()
        row["id"] = cur.lastrowid
        return row

    def get_event(self, event_id: int) -> dict | None:
        r = self._conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self._row_to_event(r) if r else None

    def ack_event(self, event_id: int) -> bool:
        cur = self._conn.execute(
            "UPDATE events SET delivery='acked' WHERE id=? AND delivery IN ('queued','delivered')",
            (event_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def mark_events_delivered(self, to: str) -> int:
        """Отметить как delivered при отправке по SSE (до ACK)."""
        cur = self._conn.execute(
            "UPDATE events SET delivery='delivered' WHERE \"to\"=? AND delivery='queued'", (to,))
        self._conn.commit()
        return cur.rowcount

    def undelivered_events(self, to: str, limit: int = 200) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE \"to\"=? AND delivery IN ('queued','delivered') "
            "ORDER BY id LIMIT ?", (to, limit)).fetchall()
        return [self._row_to_event(r) for r in rows]

    def recent_events(self, limit: int = 200, project: str = "") -> list[dict]:
        if project:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE project=? ORDER BY id DESC LIMIT ?",
                (project, limit)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_event(r) for r in rows]

    # --- сообщения ---------------------------------------------------------

    def add_message(self, from_: str, to: str, text: str) -> dict:
        row = {"id": None, "from": from_, "to": to, "text": text,
               "created_at": now_iso(), "delivery": "queued"}
        cur = self._conn.execute(
            "INSERT INTO messages (from_,\"to\",text,created_at,delivery) VALUES (?,?,?,?,?)",
            (from_, to, text, row["created_at"], "queued"))
        self._conn.commit()
        row["id"] = cur.lastrowid
        return row

    def inbox_messages(self, to: str, undelivered: bool = False) -> list[dict]:
        q = "SELECT * FROM messages WHERE \"to\"=?"
        args = [to]
        if undelivered:
            q += " AND delivery IN ('queued','delivered')"
        q += " ORDER BY id"
        rows = self._conn.execute(q, args).fetchall()
        return [{"id": r["id"], "from": r["from_"], "to": r["to"], "text": r["text"],
                 "created_at": r["created_at"], "delivery": r["delivery"]} for r in rows]

    def ack_message(self, msg_id: int) -> bool:
        cur = self._conn.execute(
            "UPDATE messages SET delivery='acked' WHERE id=? AND delivery IN ('queued','delivered')",
            (msg_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # --- агенты / heartbeat --------------------------------------------------

    def heartbeat(self, name: str) -> None:
        self._conn.execute(
            "INSERT INTO agents (name,last_seen,status) VALUES (?,?, 'online') "
            "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen, status='online'",
            (name, now_iso()))
        self._conn.commit()

    def mark_offline(self, name: str) -> None:
        self._conn.execute("UPDATE agents SET status='offline' WHERE name=?", (name,))
        self._conn.commit()

    def agents(self) -> list[dict]:
        rows = self._conn.execute("SELECT name,last_seen,status FROM agents ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def stale_agents(self, max_age_sec: int) -> list[str]:
        """Агенты с последним heartbeat старше max_age_sec И статусом online."""
        from datetime import datetime as dt
        cutoff = dt.now().timestamp() - max_age_sec
        out = []
        for r in self.agents():
            try:
                t = dt.fromisoformat(r["last_seen"]).timestamp()
            except ValueError:
                continue
            if r["status"] == "online" and t < cutoff:
                out.append(r["name"])
        return out

    # --- вспомогательное -----------------------------------------------------

    @staticmethod
    def _row_to_event(r: sqlite3.Row) -> dict:
        try:
            payload = json.loads(r["payload"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        return {"id": r["id"], "type": r["type"], "from": r["from_"], "to": r["to"],
                "project": r["project"], "task": r["task"], "payload": payload,
                "created_at": r["created_at"], "delivery": r["delivery"]}

    def stats(self) -> dict:
        ev = self._conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
        ms = self._conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        ag = self._conn.execute("SELECT COUNT(*) AS c FROM agents").fetchone()["c"]
        return {"events": ev, "messages": ms, "agents": ag}
