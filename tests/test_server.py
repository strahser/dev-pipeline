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


class TestAppAPI(unittest.TestCase):
    """Тесты HTTP-API через TestClient (временная БД через env PIPELINE_DB)."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.client = TestClient(app_mod.app)

    def setUp(self):
        # очистить БД между тестами
        self.store = app_mod.store
        for t in ("events", "messages", "agents"):
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
