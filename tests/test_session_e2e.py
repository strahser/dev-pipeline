# -*- coding: utf-8 -*-
"""E2E: явная сессия субагента — сервер + session_worker (stub opencode).

Проверяет полный цикл «общение через сервер»:
  POST /api/sessions -> session_worker читает инструкцию С СЕРВЕРА ->
  запускает opencode (stub) -> POST /status done + отчёт.

Запуск: python -X utf8 tests/test_session_e2e.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORT = 18777
DB = os.path.join(tempfile.mkdtemp(prefix="e2e_sess_"), "e2e.db")


class TestSessionE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stub = ROOT / "tests" / "stub_opencode.py"
        cls.stub.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "sys.stdout.write('STUB-OPENCODE OK\\n')\n"
            "for a in sys.argv:\n"
            "    if a.endswith('.md') and 'Отчёт' not in a and Path(a).exists():\n"
            "        rep = Path(a).parent.parent / 'Отчёты' / 'A-99_Отчёт_2026-08-08.md'\n"
            "        rep.write_text('# ОТЧЁТ: A-99\\n## Что сделано\\nstub\\n## Доказательства\\nlog\\n',"
            " encoding='utf-8')\n"
            "sys.exit(0)\n", encoding="utf-8")
        cls.stub_cmd = ROOT / "tests" / "stub_opencode.cmd"
        cls.stub_cmd.write_text(
            f"@echo off\r\npython -X utf8 \"{cls.stub}\" %*\r\nexit /b %errorlevel%\r\n",
            encoding="ascii")
        env = dict(os.environ)
        env["PIPELINE_DB"] = DB
        env["OPENCODE_CMD"] = str(cls.stub_cmd)
        cls.env = env
        cls.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "server", "--host", "127.0.0.1",
             "--port", str(PORT)], cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                cls.req("GET", "/healthz")
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("сервер не поднялся")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        for f in (cls.stub, cls.stub_cmd):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

    @classmethod
    def req(cls, method, path, body=None):
        url = f"http://127.0.0.1:{PORT}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        r = urllib.request.Request(url, data=data, method=method,
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_session_full_cycle(self):
        tmp_proj = Path(tempfile.mkdtemp(prefix="e2e_proj_"))
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Конвейер/logs"):
            (tmp_proj / sub).mkdir(parents=True)
        (tmp_proj / "Tasks" / "Активные" / "A-99_Тест.md").write_text(
            "---\nid: A-99\nстатус: open\n---\n# ЗАДАЧА\n", encoding="utf-8")
        s = self.req("POST", "/api/sessions", {
            "project": "heatlossrevit2", "task": "A-99", "agent": "session-A-99",
            "role": "worker", "model": "stub", "skill": "",
            "instruction": {
                "task_file": str(tmp_proj / "Tasks" / "Активные" / "A-99_Тест.md"),
                "report": str(tmp_proj / "Tasks" / "Отчёты" / "A-99_Отчёт_2026-08-08.md"),
                "log": str(tmp_proj / "Tasks" / "Конвейер" / "logs" / "A-99_run.log"),
                "prompt": "STUB-ПРОМПТ: выполни задачу и создай отчёт.",
                "task_id": "A-99",
            }})
        sid = s["id"]
        self.assertEqual(s["status"], "created")
        wk = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "agents" / "session_worker.py"),
             "--session", sid, "--url", f"http://127.0.0.1:{PORT}", "--cwd", str(tmp_proj)],
            cwd=str(ROOT), env=self.env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(wk.returncode, 0, wk.stdout + wk.stderr)
        s2 = self.req("GET", f"/api/sessions/{sid}")
        self.assertEqual(s2["status"], "done", s2)
        self.assertTrue(s2["report"], "должен быть путь к отчёту")
        self.assertTrue(Path(s2["report"]).exists(), "отчёт не создан")
        self.assertIsNotNone(s2["pid"], "worker должен зарегистрировать pid")
        # события в ленте
        evs = self.req("GET", "/api/ledger")
        types = {e["type"] for e in evs}
        self.assertIn("session_created", types)
        self.assertIn("session_started", types)
        self.assertIn("session_status", types)

    def test_session_abort_instruction(self):
        """Инструкция контролёра уходит в канал сессии (SSE session-<sid>)."""
        s = self.req("POST", "/api/sessions", {"project": "P", "task": "A-7"})
        sid = s["id"]
        msg = self.req("POST", f"/api/sessions/{sid}/instruction",
                       {"from": "controller", "to": f"session-{sid}", "text": "abort"})
        self.assertIn("id", msg)
        # сообщение в inbox канала сессии
        inbox = self.req("GET", f"/messages?agent=session-{sid}")
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["text"], "abort")
        # событие session_instruction в ленте
        evs = self.req("GET", "/api/ledger")
        self.assertTrue(any(e["type"] == "session_instruction"
                            and e["payload"].get("session_id") == sid for e in evs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
