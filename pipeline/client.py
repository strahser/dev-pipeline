# -*- coding: utf-8 -*-
"""Клиент сервера координации для агентов.

Единый интерфейс notify()/subscribe()/ack()/heartbeat() + явные сессии
(create_session/get_session/session_status/...):
  - если сервер доступен — SSE-подписка и REST;
  - если нет — фолбэк на файловые флаги в Tasks\\Конвейер\\Уведомления\\ (как в v1).

Использование:
    from pipeline.client import Client
    c = Client("executor", project="HeatLossRevit2", base_url="http://127.0.0.1:8787")
    c.notify("report_done", to="controller", task="A-10", payload={"report": "..."})
    c.subscribe(callback)          # callback(event: dict) — блокирующий цикл
    c.ack(event["id"])
    c.heartbeat()
    s = c.create_session(project, task="A-11", instruction={"task_file": "..."})
    c.session_status(s["id"], "done", report="rep.md")
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SERVER_URL = "http://127.0.0.1:8787"
HEARTBEAT_SEC = 30


class Client:
    def __init__(self, agent: str, project: str = "", base_url: str = SERVER_URL,
                 notif_dir: str | None = None):
        self.agent = agent
        self.project = project
        self.base_url = base_url.rstrip("/")
        self.notif_dir = Path(notif_dir) if notif_dir else None
        self._last_event_id = 0

    # --- низкоуровневый HTTP -------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None,
                 params: dict | None = None, timeout: float = 5.0):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def server_alive(self, timeout: float = 2.0) -> bool:
        try:
            self._request("GET", "/healthz", timeout=timeout)
            return True
        except Exception:
            return False

    # --- события/сообщения ---------------------------------------------------

    def notify(self, type_: str, to: str, task: str = "", payload: dict | None = None,
               fallback: bool = True) -> dict | None:
        """Создать событие на сервере; при недоступности сервера — файловый флажок."""
        try:
            ev = self._request("POST", "/events", body={
                "type": type_, "from": self.agent, "to": to,
                "project": self.project, "task": task,
                "payload": payload or {},
            })
            return ev
        except Exception:
            if fallback and self.notif_dir:
                return self._file_fallback(type_, to, task, payload)
            return None

    def send_message(self, to: str, text: str) -> dict | None:
        try:
            return self._request("POST", "/messages",
                                 body={"from": self.agent, "to": to, "text": text})
        except Exception:
            return None

    def inbox(self, undelivered: bool = True) -> list[dict]:
        try:
            return self._request("GET", "/messages",
                                 params={"agent": self.agent, "undelivered": str(undelivered).lower()})
        except Exception:
            return []

    def ack(self, event_id: int) -> bool:
        try:
            self._request("POST", f"/events/{event_id}/ack")
            return True
        except Exception:
            return False

    def heartbeat(self, interval: float = HEARTBEAT_SEC, stop_event: threading.Event | None = None):
        """Фоновый поток heartbeats (остановка — stop_event.set()).
        Передаёт проект, PID и команду запуска — для чата (kill/restart, фильтр по проекту)."""
        def _q(s: str) -> str:
            return f'"{s}"' if " " in s else s
        if sys.argv and sys.argv[0] and sys.argv[0].endswith(".py"):
            cmd = "python -X utf8 " + " ".join(_q(a) for a in sys.argv)
        else:
            cmd = "python -X utf8 -m " + " ".join(_q(a) for a in sys.argv[1:])

        def _loop():
            while not (stop_event and stop_event.is_set()):
                try:
                    self._request("POST", "/heartbeat", timeout=3.0, body={
                        "agent": self.agent, "project": self.project,
                        "pid": os.getpid(), "cmd": cmd})
                except Exception:
                    pass
                time.sleep(interval)
        t = threading.Thread(target=_loop, daemon=True, name=f"hb-{self.agent}")
        t.start()
        return t

    # --- SSE-подписка ----------------------------------------------------------

    def subscribe(self, callback, stop_event: threading.Event | None = None):
        """Блокирующий цикл: читает SSE-ленту и вызывает callback(event_dict).
        При обрыве соединения — переподключение (Last-Event-ID). Ctrl+C/stop_event — выход."""
        url = (f"{self.base_url}/events/stream?agent={urllib.parse.quote(self.agent)}"
               f"&last_event_id={self._last_event_id}")
        while not (stop_event and stop_event.is_set()):
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=30.0) as resp:
                    event_name, data = None, None
                    for raw in resp:
                        line = raw.decode("utf-8").rstrip("\n")
                        if not line:
                            if data:
                                try:
                                    ev = json.loads(data)
                                except json.JSONDecodeError:
                                    ev = {"type": event_name, "data": data}
                                self._last_event_id = max(self._last_event_id, ev.get("id", 0))
                                callback(ev)
                            event_name, data = None, None
                        elif line.startswith("event:"):
                            event_name = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data = line[len("data:"):].strip()
            except Exception:
                if stop_event and stop_event.is_set():
                    break
                time.sleep(2.0)  # reconnect

    # --- явные сессии субагентов ---------------------------------------------

    def create_session(self, project: str, task: str = "", agent: str = "",
                       role: str = "worker", model: str = "", skill: str = "",
                       instruction: dict | None = None, sid: str = "") -> dict | None:
        """Создать сессию субагента на сервере. Возвращает сессию или None."""
        try:
            return self._request("POST", "/api/sessions", body={
                "id": sid, "project": project, "task": task, "agent": agent,
                "role": role, "model": model, "skill": skill,
                "instruction": instruction or {}}, timeout=10.0)
        except Exception:
            return None

    def get_session(self, sid: str) -> dict | None:
        try:
            return self._request("GET", f"/api/sessions/{sid}", timeout=10.0)
        except Exception:
            return None

    def list_sessions(self, project: str = "", task: str = "", status: str = "") -> list:
        try:
            return self._request("GET", "/api/sessions",
                                 params={"project": project, "task": task, "status": status},
                                 timeout=10.0)
        except Exception:
            return []

    def session_start(self, sid: str, pid: int | None = None, cmd: str = "") -> dict | None:
        try:
            return self._request("POST", f"/api/sessions/{sid}/start",
                                 body={"pid": pid, "cmd": cmd}, timeout=10.0)
        except Exception:
            return None

    def session_status(self, sid: str, status: str, note: str = "",
                       report: str = "", error: str = "") -> dict | None:
        try:
            return self._request("POST", f"/api/sessions/{sid}/status",
                                 body={"status": status, "note": note,
                                       "report": report, "error": error}, timeout=10.0)
        except Exception:
            return None

    def session_heartbeat(self, sid: str) -> bool:
        try:
            self._request("POST", f"/api/sessions/{sid}/heartbeat", timeout=3.0)
            return True
        except Exception:
            return False

    def session_instruction(self, sid: str, text: str, from_: str = "controller") -> dict | None:
        """Контролёр -> субагент: инструкция в канал сессии."""
        try:
            return self._request("POST", f"/api/sessions/{sid}/instruction",
                                 body={"from": from_, "to": f"session-{sid}", "text": text},
                                 timeout=10.0)
        except Exception:
            return None

    def session_kill(self, sid: str) -> dict | None:
        try:
            return self._request("POST", f"/api/sessions/{sid}/kill", timeout=10.0)
        except Exception:
            return None

    def session_stalled(self, sid: str, reason: str = "") -> dict | None:
        return self.session_status(sid, "stalled", error=reason)

    # --- фолбэк на файлы --------------------------------------------------------

    def _file_fallback(self, type_: str, to: str, task: str, payload: dict | None) -> dict:
        self.notif_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%H%M%S")
        name = f"{task}_done_{stamp}.txt" if task else f"{type_}_{stamp}.txt"
        path = self.notif_dir / name
        lines = [f"Агент-1:", f"тип: {type_}", f"от: {self.agent}", f"к: {to}"]
        if task:
            lines.append(f"задача: {task}")
        if payload:
            lines.append("payload: " + json.dumps(payload, ensure_ascii=False))
        path.write_text("\n".join(lines), encoding="utf-8")
        return {"id": -1, "type": type_, "from": self.agent, "to": to, "task": task,
                "payload": payload or {}, "fallback_file": str(path)}
