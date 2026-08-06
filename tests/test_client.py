# -*- coding: utf-8 -*-
"""Тесты pipeline/client.py: REST, SSE-подписка, ACK, recovery, фолбэк на файлы.

Запуск: python -X utf8 tests/test_client.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.client import Client   # noqa: E402


class _LiveServer(unittest.TestCase):
    """Поднимает реальный uvicorn-сервер на тестовом порту для всех тестов клиента."""

    @classmethod
    def setUpClass(cls):
        cls.srv_dir = tempfile.mkdtemp(prefix="pipeline_client_")
        cls.srv_db = os.path.join(cls.srv_dir, "c.db")
        cls.port = 18988
        env = dict(os.environ)
        env["PIPELINE_DB"] = cls.srv_db
        cls.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "server", "--host", "127.0.0.1",
             "--port", str(cls.port)],
            cwd=str(Path(__file__).resolve().parent.parent), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/healthz", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("сервер не поднялся")
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        import shutil
        shutil.rmtree(cls.srv_dir, ignore_errors=True)

    def _new_client(self, agent="executor"):
        nf = os.path.join(tempfile.mkdtemp(), "notif")
        return Client(agent, project="P", base_url=self.base_url, notif_dir=nf)


class TestClientREST(_LiveServer):
    def test_server_alive(self):
        self.assertTrue(self._new_client().server_alive())

    def test_notify_event_created(self):
        c = self._new_client("controller")
        ev = c.notify("task_assigned", to="executor", task="A-1", payload={"path": "x"})
        self.assertIsNotNone(ev)
        self.assertEqual(ev["task"], "A-1")
        self.assertEqual(ev["delivery"], "queued")

    def test_send_message_and_inbox(self):
        ctrl = self._new_client("controller")
        msg = ctrl.send_message(to="executor", text="fix A-3")
        self.assertIsNotNone(msg)
        ex = self._new_client("executor")
        inbox = ex.inbox()
        self.assertTrue(any(m["text"] == "fix A-3" for m in inbox))

    def test_ack(self):
        ctrl = self._new_client("controller")
        ex = self._new_client("executor")
        ev = ctrl.notify("instruction", to="executor", task="A-2")
        self.assertTrue(ex.ack(ev["id"]))
        self.assertFalse(ex.ack(ev["id"]))  # повторный ACK неуспешен


class TestClientSSE(_LiveServer):
    def test_subscribe_receives_event(self):
        c = self._new_client("ex_sub")
        got = []
        stop = threading.Event()

        def cb(ev):
            got.append(ev)

        t = threading.Thread(target=c.subscribe, args=(cb, stop), daemon=True)
        t.start()
        time.sleep(0.8)
        self._new_client("controller").notify("instruction", to="ex_sub",
                                              task="A-4", payload={"m": "1"})
        deadline = time.time() + 10
        while not any(e.get("type") == "instruction" for e in got) and time.time() < deadline:
            time.sleep(0.1)
        stop.set()
        self.assertTrue(any(e.get("type") == "instruction" for e in got), f"получено: {got}")

    def test_recovery_inbox(self):
        """Событие, созданное до подписки, воспроизводится при подключении."""
        self._new_client("controller").notify("task_assigned", to="ex_rec",
                                              task="A-5", payload={})
        c = self._new_client("ex_rec")
        got = []
        stop = threading.Event()

        def cb(ev):
            got.append(ev)
            stop.set()

        t = threading.Thread(target=c.subscribe, args=(cb, stop), daemon=True)
        t.start()
        t.join(timeout=10)
        self.assertTrue(any(e.get("task") == "A-5" for e in got), f"получено: {got}")


class TestClientFallback(unittest.TestCase):
    """Фолбэк на файлы при недоступном сервере."""

    def test_notify_writes_flag_file(self):
        notif_dir = Path(tempfile.mkdtemp()) / "notif"
        c = Client("executor", project="P", base_url="http://127.0.0.1:1", notif_dir=str(notif_dir))
        ev = c.notify("report_done", to="controller", task="A-9",
                      payload={"report": "A-9_Отчёт.md"})
        # фолбэк-файл создан
        files = list(notif_dir.glob("*"))
        self.assertTrue(files, "фолбэк-файл не создан")
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("report_done", content)
        self.assertIn("A-9", content)

    def test_heartbeat_silent_when_server_down(self):
        c = Client("executor", project="P", base_url="http://127.0.0.1:1")
        # не должно бросать
        try:
            c.heartbeat()  # стартует поток, в котором сервер недоступен
        except Exception as e:
            self.fail(f"heartbeat должен молча работать при недоступном сервере: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
