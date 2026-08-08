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
  status TEXT NOT NULL DEFAULT 'online',
  project TEXT NOT NULL DEFAULT '',
  pid INTEGER,
  cmd TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL DEFAULT '',
  task TEXT NOT NULL DEFAULT '',
  agent TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'worker',
  model TEXT NOT NULL DEFAULT '',
  skill TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'created',
  pid INTEGER,
  cmd TEXT NOT NULL DEFAULT '',
  instruction TEXT NOT NULL DEFAULT '{}',
  note TEXT NOT NULL DEFAULT '',
  report TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  heartbeat TEXT
);
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'new',
  file TEXT NOT NULL DEFAULT '',
  commit_sha TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_to_delivery ON events("to", delivery);
CREATE INDEX IF NOT EXISTS idx_messages_to_delivery ON messages("to", delivery);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


_HUMAN = {
    "task_started": "Субагент взялся за задачу",
    "subagent_finished": "Субагент закончил задачу",
    "task_assigned": "Задача назначена",
    "report_done": "Исполнитель сдал отчёт",
    "verdict": "Контролёр вынес вердикт",
    "agent_offline": "Агент перестал отвечать",
    "browser_done": "Облачный ИИ ответил",
    "message": "Сообщение",
}


def _human_event(etype: str, task: str, payload: dict) -> str:
    base = _HUMAN.get(etype, etype)
    parts = []
    if task:
        parts.append(f"задача {task}")
    if etype == "subagent_finished":
        rc = payload.get("rc")
        ok = payload.get("report")
        if ok:
            parts.append("— отчёт готов")
        elif rc is not None:
            parts.append(f"— rc={rc}, отчёта нет (проверь)")
    elif etype == "agent_offline":
        parts.append(f"— {payload.get('agent', '?')}")
    elif etype == "verdict":
        pass
    if parts:
        base += " " + " ".join(parts)
    return base


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate_agents()
        self._lock = sqlite3.connect(self.db_path)  # отдельное соединение для write-lock

    def _migrate_agents(self):
        """Добавить колонки project/pid/cmd в старые БД (если их нет)."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(agents)")}
        for col, ddl in (("project", "TEXT NOT NULL DEFAULT ''"),
                         ("pid", "INTEGER"),
                         ("cmd", "TEXT NOT NULL DEFAULT ''")):
            if col not in cols:
                self._conn.execute(f"ALTER TABLE agents ADD COLUMN {col} {ddl}")
        self._conn.commit()

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

    def activity(self, limit: int = 50, project: str = "") -> list[dict]:
        """Человекочитаемая сводка активности: последние события с типом/задачей/отправителем.
        Для панели «что происходит»."""
        evs = self.recent_events(limit=limit, project=project)
        out = []
        for e in evs:
            label = e.get("type", "")
            task = e.get("task") or ""
            payload = e.get("payload") or {}
            out.append({
                "id": e["id"], "type": label, "from": e["from"], "to": e["to"],
                "task": task, "created_at": e["created_at"],
                "detail": _human_event(label, task, payload),
            })
        return out

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

    def dialog_messages(self, agent: str, limit: int = 200) -> list[dict]:
        """Все сообщения диалога с агентом (в обе стороны), новые последними."""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE from_=? OR \"to\"=? ORDER BY id DESC LIMIT ?",
            (agent, agent, limit)).fetchall()
        msgs = [{"id": r["id"], "from": r["from_"], "to": r["to"], "text": r["text"],
                 "created_at": r["created_at"], "delivery": r["delivery"]} for r in rows]
        return list(reversed(msgs))

    # --- агенты / heartbeat --------------------------------------------------

    def heartbeat(self, name: str, project: str = "", pid: int | None = None,
                  cmd: str = "") -> None:
        self._conn.execute(
            "INSERT INTO agents (name,last_seen,status,project,pid,cmd) "
            "VALUES (?,?, 'online',?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen, status='online', "
            "project=CASE WHEN excluded.project!='' THEN excluded.project ELSE project END, "
            "pid=CASE WHEN excluded.pid IS NOT NULL THEN excluded.pid ELSE pid END, "
            "cmd=CASE WHEN excluded.cmd!='' THEN excluded.cmd ELSE cmd END",
            (name, now_iso(), project, pid, cmd))
        self._conn.commit()

    def mark_offline(self, name: str) -> None:
        self._conn.execute("UPDATE agents SET status='offline' WHERE name=?", (name,))
        self._conn.commit()

    def agents(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT name,last_seen,status,project,pid,cmd FROM agents ORDER BY name").fetchall()
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

    # --- сессии субагентов ---------------------------------------------------

    _SESSION_TERMINAL = ("done", "failed", "killed", "stalled")

    def create_session(self, session_id: str, project: str, task: str = "",
                       agent: str = "", role: str = "worker", model: str = "",
                       skill: str = "", instruction: dict | None = None) -> dict:
        """Явная сессия субагента (id — ключ). Инструкция — JSON (задача, отчёт, параметры)."""
        row = {"id": session_id, "project": project, "task": task, "agent": agent,
               "role": role, "model": model, "skill": skill,
               "status": "created", "pid": None, "cmd": "",
               "instruction": instruction or {}, "note": "", "report": "", "error": "",
               "created_at": now_iso(), "started_at": None, "finished_at": None,
               "heartbeat": None}
        self._conn.execute(
            "INSERT INTO sessions (id,project,task,agent,role,model,skill,status,pid,cmd,"
            "instruction,note,report,error,created_at,started_at,finished_at,heartbeat) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["project"], row["task"], row["agent"], row["role"], row["model"],
             row["skill"], row["status"], row["pid"], row["cmd"],
             json.dumps(row["instruction"], ensure_ascii=False), row["note"], row["report"],
             row["error"], row["created_at"], row["started_at"], row["finished_at"],
             row["heartbeat"]))
        self._conn.commit()
        return row

    @staticmethod
    def _row_to_session(r: sqlite3.Row) -> dict:
        try:
            instruction = json.loads(r["instruction"])
        except (TypeError, json.JSONDecodeError):
            instruction = {}
        return {"id": r["id"], "project": r["project"], "task": r["task"],
                "agent": r["agent"], "role": r["role"], "model": r["model"],
                "skill": r["skill"], "status": r["status"], "pid": r["pid"],
                "cmd": r["cmd"], "instruction": instruction, "note": r["note"],
                "report": r["report"], "error": r["error"], "created_at": r["created_at"],
                "started_at": r["started_at"], "finished_at": r["finished_at"],
                "heartbeat": r["heartbeat"]}

    def get_session(self, session_id: str) -> dict | None:
        r = self._conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return self._row_to_session(r) if r else None

    def list_sessions(self, project: str = "", task: str = "", status: str = "") -> list[dict]:
        q = "SELECT * FROM sessions WHERE 1=1"
        args = []
        if project:
            q += " AND project=?"
            args.append(project)
        if task:
            q += " AND task=?"
            args.append(task)
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY created_at DESC, id DESC LIMIT 200"
        rows = self._conn.execute(q, args).fetchall()
        return [self._row_to_session(r) for r in rows]

    def update_session(self, session_id: str, **fields) -> dict | None:
        """Обновить поля сессии (status/pid/cmd/note/report/error/heartbeat/
        started_at/finished_at). Возвращает обновлённую сессию или None."""
        allowed = {"status", "pid", "cmd", "note", "report", "error", "heartbeat",
                   "started_at", "finished_at", "instruction"}
        if fields.get("status") in ("done", "failed", "killed", "stalled"):
            fields.setdefault("finished_at", now_iso())
        sets, args = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "instruction":
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f"{k}=?")
            args.append(v)
        if not sets:
            return self.get_session(session_id)
        args.append(session_id)
        self._conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id=?", args)
        self._conn.commit()
        return self.get_session(session_id)

    def touch_session(self, session_id: str) -> None:
        self._conn.execute("UPDATE sessions SET heartbeat=? WHERE id=?",
                           (now_iso(), session_id))
        self._conn.commit()

    def stale_sessions(self, max_age_sec: int) -> list[dict]:
        """Сессии в работе (running/created) с heartbeat старше max_age_sec."""
        from datetime import datetime as dt
        cutoff = dt.now().timestamp() - max_age_sec
        out = []
        for s in self.list_sessions():
            if s["status"] not in ("created", "running"):
                continue
            hb = s.get("heartbeat") or s.get("created_at")
            try:
                t = dt.fromisoformat(hb).timestamp()
            except (ValueError, TypeError):
                continue
            if t < cutoff:
                out.append(s)
        return out

    # --- сырые задания пользователя (Входящие) --------------------------------

    def add_request(self, req_id: str, project: str, text: str,
                    status: str = "new", file: str = "", commit_msg: str = "") -> dict:
        """Зафиксировать сырое задание пользователя: БД + файл + git-коммит
        (как общение агентов: БД — координация, файлы+git — источник правды)."""
        row = {"id": req_id, "project": project, "text": text, "status": status,
               "file": file, "commit_msg": commit_msg, "created_at": now_iso()}
        self._conn.execute(
            "INSERT INTO requests (id,project,text,status,file,commit_sha,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (req_id, project, text, status, file, commit_msg, row["created_at"]))
        self._conn.commit()
        return row

    @staticmethod
    def _row_to_request(r: sqlite3.Row) -> dict:
        return {"id": r["id"], "project": r["project"], "text": r["text"],
                "status": r["status"], "file": r["file"],
                "commit": r["commit_sha"], "created_at": r["created_at"]}

    def get_request(self, req_id: str) -> dict | None:
        r = self._conn.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
        return self._row_to_request(r) if r else None

    def list_requests(self, project: str = "", status: str = "",
                      limit: int = 100) -> list[dict]:
        q = "SELECT * FROM requests WHERE 1=1"
        args = []
        if project:
            q += " AND project=?"
            args.append(project)
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(min(limit, 500))
        return [self._row_to_request(r) for r in self._conn.execute(q, args).fetchall()]

    def update_request(self, req_id: str, **fields) -> dict | None:
        allowed = {"status", "file", "commit_sha", "text"}
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                args.append(v)
        if not sets:
            return self.get_request(req_id)
        args.append(req_id)
        self._conn.execute(f"UPDATE requests SET {', '.join(sets)} WHERE id=?", args)
        self._conn.commit()
        return self.get_request(req_id)

    def stats(self) -> dict:
        ev = self._conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
        ms = self._conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        ag = self._conn.execute("SELECT COUNT(*) AS c FROM agents").fetchone()["c"]
        ss = self._conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
        rq = self._conn.execute("SELECT COUNT(*) AS c FROM requests").fetchone()["c"]
        return {"events": ev, "messages": ms, "agents": ag, "sessions": ss, "requests": rq}
