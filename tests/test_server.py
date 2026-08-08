# -*- coding: utf-8 -*-
"""Тесты сервера координации: db, sse, heartbeat, events, messages, dashboard API.

Запуск: python -X utf8 tests/test_server.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Переключаем БД сервера на временный файл ДО импорта app
_tmp_db = os.path.join(tempfile.mkdtemp(prefix="pipeline_srv_"), "test.db")
os.environ["PIPELINE_DB"] = _tmp_db
import server.db as db_mod        # noqa: E402
import server.app as app_mod      # noqa: E402


class TestStore(unittest.TestCase):
    def setUp(self):
        self.p = os.path.join(tempfile.mkdtemp(prefix="store_"), "t.db")
        self.s = db_mod.Store(self.p)

    def tearDown(self):
        self.s.close()

    def test_event_lifecycle(self):
        ev = self.s.add_event("task_assigned", "controller", "executor", "P", "A-1",
                              {"path": "x"})
        self.assertEqual(ev["delivery"], "queued")
        self.assertEqual(ev["to"], "executor")
        got = self.s.undelivered_events("executor")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["payload"]["path"], "x")
        self.assertTrue(self.s.ack_event(ev["id"]))
        self.assertEqual(self.s.undelivered_events("executor"), [])
        # повторный ACK неуспешен
        self.assertFalse(self.s.ack_event(ev["id"]))

    def test_event_filtered_by_to(self):
        self.s.add_event("a", "x", "executor")
        self.s.add_event("b", "x", "controller")
        self.assertEqual(len(self.s.undelivered_events("executor")), 1)

    def test_messages(self):
        m = self.s.add_message("controller", "executor", "fix A-1")
        inbox = self.s.inbox_messages("executor")
        self.assertEqual(len(inbox), 1)
        self.assertTrue(self.s.ack_message(m["id"]))
        self.assertEqual(self.s.inbox_messages("executor", undelivered=True), [])

    def test_heartbeat_and_stale(self):
        self.s.heartbeat("agent1")
        self.assertEqual(self.s.agents()[0]["status"], "online")
        # максимальный возраст = -1 -> всё stale
        stale = self.s.stale_agents(max_age_sec=-1)
        self.assertIn("agent1", stale)
        self.s.mark_offline("agent1")
        self.assertNotIn("agent1", self.s.stale_agents(max_age_sec=-1))

    def test_recent_events_project_filter(self):
        self.s.add_event("a", "x", "y", project="P1")
        self.s.add_event("b", "x", "y", project="P2")
        self.assertEqual(len(self.s.recent_events(project="P1")), 1)
        self.assertEqual(len(self.s.recent_events()), 2)

    def test_stats(self):
        self.s.add_event("a", "x", "y")
        self.s.heartbeat("z")
        st = self.s.stats()
        self.assertEqual(st["events"], 1)
        self.assertEqual(st["agents"], 1)

    def test_session_lifecycle(self):
        s = self.s.create_session("S-1", "P", task="A-1", agent="session-A-1",
                                  model="m", skill="s", instruction={"task_file": "x.md"})
        self.assertEqual(s["status"], "created")
        self.assertEqual(s["task"], "A-1")
        got = self.s.get_session("S-1")
        self.assertEqual(got["instruction"]["task_file"], "x.md")
        # start -> running + pid
        self.s.update_session("S-1", status="running", pid=123, cmd="python ...")
        self.s.touch_session("S-1")
        r = self.s.get_session("S-1")
        self.assertEqual(r["status"], "running")
        self.assertEqual(r["pid"], 123)
        # done + report
        self.s.update_session("S-1", status="done", report="rep.md")
        r = self.s.get_session("S-1")
        self.assertEqual(r["status"], "done")
        self.assertIsNotNone(r["finished_at"])
        # список с фильтрами
        self.s.create_session("S-2", "P", task="A-2")
        self.assertEqual(len(self.s.list_sessions(project="P")), 2)
        self.assertEqual(len(self.s.list_sessions(task="A-1")), 1)
        self.assertEqual(len(self.s.list_sessions(status="done")), 1)

    def test_stale_sessions(self):
        from datetime import datetime as dt
        self.s.create_session("S-old", "P", task="A-1")
        # heartbeat старее 200 с
        old_hb = dt.fromtimestamp(time.time() - 200).isoformat()
        self.s._conn.execute("UPDATE sessions SET heartbeat=? WHERE id=?",
                             (old_hb, "S-old"))
        self.s._conn.commit()
        self.s.create_session("S-fresh", "P", task="A-2")
        self.s.touch_session("S-fresh")
        stale = self.s.stale_sessions(max_age_sec=100)
        self.assertEqual([s["id"] for s in stale], ["S-old"])
        # terminal-статусы не считаются stale
        self.s.update_session("S-old", status="done")
        self.assertEqual(self.s.stale_sessions(max_age_sec=100), [])


class TestAppAPI(unittest.TestCase):
    """Тесты HTTP-API через TestClient (временная БД через env PIPELINE_DB)."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.client = TestClient(app_mod.app)

    def setUp(self):
        # очистить БД между тестами
        self.store = app_mod.store
        for t in ("events", "messages", "agents", "sessions"):
            self.store._conn.execute(f"DELETE FROM {t}")
        self.store._conn.commit()

    def test_healthz(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_post_event_and_get(self):
        r = self.client.post("/events", json={
            "type": "task_assigned", "from": "controller", "to": "executor",
            "project": "P", "task": "A-1", "payload": {"path": "x"}})
        self.assertEqual(r.status_code, 200)
        ev = r.json()
        self.assertIsInstance(ev["id"], int)
        self.assertEqual(ev["delivery"], "queued")
        r2 = self.client.get("/events", params={"project": "P"})
        self.assertEqual(len(r2.json()), 1)

    def test_ack_event(self):
        ev = self.client.post("/events", json={
            "type": "t", "from": "a", "to": "executor"}).json()
        r = self.client.post(f"/events/{ev['id']}/ack")
        self.assertEqual(r.status_code, 200)
        r2 = self.client.post(f"/events/{ev['id']}/ack")
        self.assertEqual(r2.status_code, 404)

    def test_messages_inbox(self):
        self.client.post("/messages", json={"from": "controller", "to": "executor",
                                            "text": "привет"})
        r = self.client.get("/messages", params={"agent": "executor"})
        self.assertEqual(len(r.json()), 1)
        r2 = self.client.get("/messages", params={"agent": "executor", "undelivered": "true"})
        self.assertEqual(len(r2.json()), 1)
        self.client.post(f"/messages/{r.json()[0]['id']}/ack")
        r3 = self.client.get("/messages", params={"agent": "executor", "undelivered": "true"})
        self.assertEqual(r3.json(), [])

    def test_heartbeat_agents(self):
        self.client.post("/heartbeat", json={"agent": "executor"})
        r = self.client.get("/agents")
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(r.json()[0]["status"], "online")

    def test_heartbeat_with_pid_cmd_project(self):
        self.client.post("/heartbeat", json={
            "agent": "executor", "project": "heatlossrevit2",
            "pid": 12345, "cmd": "python -X utf8 agents/executor_client.py --project heatlossrevit2"})
        r = self.client.get("/api/chat/agents")
        ex = next(a for a in r.json() if a["name"] == "executor")
        self.assertEqual(ex["project"], "heatlossrevit2")
        self.assertEqual(ex["pid"], 12345)
        self.assertTrue(ex["restartable"])
        self.assertTrue(ex["killable"])

    def test_agent_kill_unknown_pid(self):
        r = self.client.post("/api/chat/agents/executor/kill")
        self.assertEqual(r.status_code, 404)

    def test_agent_kill_bad_pid(self):
        self.client.post("/heartbeat", json={"agent": "executor", "pid": 12345})
        r = self.client.post("/api/chat/agents/executor/kill")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])
        self.assertEqual(r.json()["pid"], 12345)

    def test_agent_restart_no_cmd(self):
        self.client.post("/heartbeat", json={"agent": "executor", "pid": 12345})
        r = self.client.post("/api/chat/agents/executor/restart")
        self.assertEqual(r.status_code, 404)

    def test_agent_restart_with_cmd(self):
        self.client.post("/heartbeat", json={
            "agent": "executor", "pid": 12345,
            "cmd": "python -c \"pass\""})
        r = self.client.post("/api/chat/agents/executor/restart")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertIn("python", r.json()["cmd"])


    def test_dashboard_index(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("html", r.headers.get("content-type", ""))

    def test_api_stats(self):
        r = self.client.get("/api/stats")
        self.assertEqual(r.status_code, 200)
        self.assertIn("db", r.json())

    def test_chat_command_and_history(self):
        r = self.client.post("/api/chat/command", json={
            "from": "dashboard", "to": "executor", "text": "статус?"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["delivery"], "queued")
        hist = self.client.get("/api/chat/history", params={"agent": "executor"})
        self.assertEqual(len(hist.json()), 1)
        self.assertEqual(hist.json()[0]["from"], "dashboard")

    def test_chat_command_published_to_agent_channel(self):
        # сообщение должно попасть в inbox агента (как и POST /messages)
        self.client.post("/api/chat/command", json={
            "from": "dashboard", "to": "executor", "text": "отчёт по A-12"})
        inbox = self.client.get("/messages", params={"agent": "executor"})
        self.assertEqual(len(inbox.json()), 1)
        self.assertEqual(inbox.json()[0]["text"], "отчёт по A-12")

    def test_chat_agents_known_and_heartbeat(self):
        self.client.post("/heartbeat", json={"agent": "executor"})
        r = self.client.get("/api/chat/agents")
        self.assertEqual(r.status_code, 200)
        names = {a["name"] for a in r.json()}
        self.assertIn("executor", names)
        self.assertIn("controller", names)  # известная роль без heartbeat
        ex = next(a for a in r.json() if a["name"] == "executor")
        self.assertEqual(ex["status"], "online")
        self.assertFalse(ex["sleeping"])
        self.assertIn("heartbeat_age_sec", ex)

    def test_session_create_get_list(self):
        r = self.client.post("/api/sessions", json={
            "project": "P", "task": "A-9", "agent": "session-A-9",
            "role": "worker", "model": "m1", "skill": "pipeline-executor",
            "instruction": {"task_file": "Tasks/Активные/A-9_x.md", "report": "rep.md"}})
        self.assertEqual(r.status_code, 200)
        s = r.json()
        sid = s["id"]
        self.assertTrue(sid.startswith("S-"))
        self.assertEqual(s["status"], "created")
        self.assertEqual(s["instruction"]["task_file"], "Tasks/Активные/A-9_x.md")
        got = self.client.get(f"/api/sessions/{sid}").json()
        self.assertEqual(got["task"], "A-9")
        lst = self.client.get("/api/sessions", params={"project": "P", "task": "A-9"}).json()
        self.assertEqual(len(lst), 1)
        # событие session_created опубликовано
        evs = self.client.get("/api/ledger").json()
        self.assertTrue(any(e["type"] == "session_created" for e in evs))

    def test_session_start_status_done(self):
        s = self.client.post("/api/sessions", json={"project": "P", "task": "A-9"}).json()
        sid = s["id"]
        r = self.client.post(f"/api/sessions/{sid}/start", json={"pid": 4242, "cmd": "python w.py"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "running")
        self.assertEqual(r.json()["pid"], 4242)
        r = self.client.post(f"/api/sessions/{sid}/status",
                             json={"status": "done", "report": "rep.md", "note": "готово"})
        self.assertEqual(r.status_code, 200)
        s2 = r.json()
        self.assertEqual(s2["status"], "done")
        self.assertEqual(s2["report"], "rep.md")
        self.assertIsNotNone(s2["finished_at"])
        # событие session_status
        evs = self.client.get("/api/ledger").json()
        self.assertTrue(any(e["type"] == "session_status" and e["payload"].get("session_id") == sid
                            for e in evs))

    def test_session_invalid_status(self):
        s = self.client.post("/api/sessions", json={"project": "P"}).json()
        r = self.client.post(f"/api/sessions/{s['id']}/status", json={"status": "wat"})
        self.assertEqual(r.status_code, 400)

    def test_session_heartbeat_and_stale(self):
        s = self.client.post("/api/sessions", json={"project": "P", "task": "A-9"}).json()
        sid = s["id"]
        self.client.post(f"/api/sessions/{sid}/start", json={"pid": 1})
        self.client.post(f"/api/sessions/{sid}/heartbeat")
        self.assertIsNotNone(self.store.get_session(sid)["heartbeat"])
        # старый heartbeat -> stale_sessions находит
        self.store._conn.execute(
            "UPDATE sessions SET heartbeat='2000-01-01T00:00:00' WHERE id=?", (sid,))
        self.store._conn.commit()
        self.assertEqual(len(self.store.stale_sessions(max_age_sec=60)), 1)

    def test_session_kill(self):
        s = self.client.post("/api/sessions", json={"project": "P", "task": "A-9"}).json()
        sid = s["id"]
        # без pid — ok=false, статус всё равно killed
        r = self.client.post(f"/api/sessions/{sid}/kill")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])
        self.assertEqual(self.store.get_session(sid)["status"], "killed")
        # с несуществующим pid — kill не удаётся, но статус фиксируется
        self.client.post(f"/api/sessions/{sid}/start", json={"pid": 99999999})
        r2 = self.client.post(f"/api/sessions/{sid}/kill")
        self.assertFalse(r2.json()["ok"])

    def test_session_instruction_published_to_channel(self):
        s = self.client.post("/api/sessions", json={"project": "P", "task": "A-9"}).json()
        sid = s["id"]
        r = self.client.post(f"/api/sessions/{sid}/instruction",
                             json={"from": "controller", "to": f"session-{sid}",
                                   "text": "abort"})
        self.assertEqual(r.status_code, 200)
        inbox = self.client.get("/messages", params={"agent": f"session-{sid}"}).json()
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["text"], "abort")
        # SSE-событие session_instruction ушло в канал сессии
        evs = self.client.get("/api/ledger").json()
        self.assertTrue(any(e["type"] == "session_instruction" and e["to"] == f"session-{sid}"
                            for e in evs))

    def test_session_not_found(self):
        self.assertEqual(self.client.get("/api/sessions/NOPE").status_code, 404)
        self.assertEqual(self.client.post("/api/sessions/NOPE/start", json={}).status_code, 404)
        self.assertEqual(self.client.post("/api/sessions/NOPE/kill").status_code, 404)

    def test_sessions_live_endpoint(self):
        """/api/sessions/live — открытые opencode-сессии (тест на фейковой БД)."""
        import sqlite3
        fake = os.path.join(tempfile.mkdtemp(prefix="live_"), "opencode.db")
        con = sqlite3.connect(fake)
        con.execute("CREATE TABLE session (id TEXT, slug TEXT, title TEXT, directory TEXT,"
                    " path TEXT, agent TEXT, model TEXT, time_created INTEGER,"
                    " time_updated INTEGER, time_archived INTEGER,"
                    " tokens_input INTEGER, tokens_output INTEGER, cost REAL)")
        now_ms = int(time.time() * 1000)
        con.executemany(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [("s1", "s1", "Живая сессия", "D:/Projects/X", "", "executor", "m", now_ms - 60000, now_ms - 5000, None, 1, 1, 0.0),
             ("s2", "s2", "Старая сессия", "D:/Projects/X", "", "", "m", now_ms - 7200000, now_ms - 3600000, None, 1, 1, 0.0),
             ("s3", "s3", "Архив", "D:/Projects/X", "", "", "m", now_ms - 60000, now_ms - 4000, now_ms - 1000, 1, 1, 0.0)])
        con.commit()
        con.close()
        old = app_mod._opencode_db_path
        app_mod._opencode_db_path = lambda: Path(fake)
        try:
            r = self.client.get("/api/sessions/live", params={"minutes": 60})
            self.assertEqual(r.status_code, 200)
            rows = r.json()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["title"], "Живая сессия")
            self.assertTrue(rows[0]["live"])
            self.assertIn("age_sec", rows[0])
        finally:
            app_mod._opencode_db_path = old

    def test_sessions_live_empty_without_db(self):
        old = app_mod._opencode_db_path
        app_mod._opencode_db_path = lambda: None
        try:
            r = self.client.get("/api/sessions/live")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json(), [])
        finally:
            app_mod._opencode_db_path = old


class TestSSEStream(unittest.TestCase):
    """SSE-поток через реальный uvicorn-сервер (детерминировано, как вручную)."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        import time
        # Отдельный временный порт и БД
        cls.srv_dir = tempfile.mkdtemp(prefix="pipeline_sse_")
        cls.srv_db = os.path.join(cls.srv_dir, "sse.db")
        cls.port = 18987
        env = dict(os.environ)
        env["PIPELINE_DB"] = cls.srv_db
        cls.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "server", "--host", "127.0.0.1",
             "--port", str(cls.port)],
            cwd=str(Path(__file__).resolve().parent.parent), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # ждём готовности
        import urllib.request
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/healthz", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("сервер не поднялся")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        import shutil
        shutil.rmtree(cls.srv_dir, ignore_errors=True)

    def _post(self, body):
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/events",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_event_reaches_subscriber(self):
        import urllib.request
        received = []

        def reader():
            url = f"http://127.0.0.1:{self.port}/events/stream?agent=executor"
            with urllib.request.urlopen(url, timeout=10) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").rstrip()
                    if line.startswith("data:"):
                        received.append(json.loads(line[len("data:"):].strip()))
                        if any(e.get("type") == "instruction" for e in received):
                            break

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(0.5)
        self._post({"type": "instruction", "from": "controller", "to": "executor",
                    "project": "P", "task": "A-1", "payload": {"msg": "hi"}})
        t.join(timeout=15)
        self.assertTrue(any(e["type"] == "instruction" for e in received),
                        f"SSE-событие не получено: {received}")

    def test_undelivered_replayed_on_subscribe(self):
        import urllib.request
        self._post({"type": "task_assigned", "from": "controller", "to": "executor",
                    "project": "P", "task": "A-2", "payload": {}})
        got = []
        url = f"http://127.0.0.1:{self.port}/events/stream?agent=executor"
        with urllib.request.urlopen(url, timeout=10) as resp:
            for raw in resp:
                line = raw.decode("utf-8").rstrip()
                if line.startswith("data:"):
                    ev = json.loads(line[len("data:"):].strip())
                    got.append(ev)
                    if ev.get("task") == "A-2":
                        break
        self.assertTrue(any(e["task"] == "A-2" for e in got),
                        f"пропущенное событие не воспроизведено: {got}")


class TestWatchdog(unittest.TestCase):
    def test_zombie_detection(self):
        s = db_mod.Store(os.path.join(tempfile.mkdtemp(), "wd.db"))
        s.heartbeat("zombie")
        # stale с max_age=-1 -> zombie offline
        stale = s.stale_agents(max_age_sec=-1)
        self.assertIn("zombie", stale)
        s.mark_offline("zombie")
        s.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
