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
from unittest import mock

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

    def test_agent_restart_whitelisted_exact_cmd(self):
        """Карточка 2.1: дословное совпадение с реестром — рестарт разрешён,
        argv строится из AGENT_LAUNCH_COMMANDS, а не из БД."""
        canonical = " ".join(app_mod.AGENT_LAUNCH_COMMANDS["executor"])
        launched = []
        with mock.patch.object(app_mod, "_run_detached",
                               lambda cmd, cwd: launched.append(list(cmd))):
            self.client.post("/heartbeat", json={
                "agent": "executor", "pid": 12345, "cmd": canonical})
            r = self.client.post("/api/chat/agents/executor/restart")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(launched, [list(app_mod.AGENT_LAUNCH_COMMANDS["executor"])],
                         "исполняется ровно argv из реестра")

    def test_agent_restart_rejects_foreign_command(self):
        """Карточка 2.1: heartbeat с посторонней командой + restart -> 403,
        команда не исполняется (инцидент: произвольный cmd из БД)."""
        launched = []
        with mock.patch.object(app_mod, "_run_detached",
                               lambda cmd, cwd: launched.append(cmd)):
            self.client.post("/heartbeat", json={
                "agent": "executor", "pid": 12345,
                "cmd": "python -c \"import os; os.system('calc')\""})
            r = self.client.post("/api/chat/agents/executor/restart")
            r_unknown = self.client.post("/api/chat/agents/evil/restart")
        self.assertEqual(r.status_code, 403,
                         "команда вне реестра отклоняется дословным сравнением")
        self.assertEqual(r_unknown.status_code, 403,
                         "имя вне белого списка не рестартуется")
        self.assertEqual(launched, [], "ни одна посторонняя команда не исполнена")

    def test_heartbeat_ignores_cmd_for_unknown_agents(self):
        """Карточка 2.1: POST /heartbeat игнорирует cmd имён вне белого списка."""
        self.client.post("/heartbeat", json={
            "agent": "evil", "pid": 7, "cmd": "notepad"})
        agents = {a["name"]: a for a in
                  self.client.get("/api/chat/agents").json()}
        self.assertFalse(agents["evil"]["restartable"],
                         "cmd неизвестного агента не сохраняется в БД")
        known_cmd = " ".join(app_mod.AGENT_LAUNCH_COMMANDS["executor"])
        self.client.post("/heartbeat", json={
            "agent": "executor", "pid": 8, "cmd": known_cmd})
        agents = {a["name"]: a for a in
                  self.client.get("/api/chat/agents").json()}
        self.assertTrue(agents["executor"]["restartable"],
                        "для известного имени cmd сохраняется как раньше")


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
        # изолируемся от реальной opencode.db (live-сессии не должны мешать)
        old = app_mod._opencode_db_path
        app_mod._opencode_db_path = lambda: None
        try:
            self.client.post("/heartbeat", json={"agent": "executor"})
            r = self.client.get("/api/chat/agents")
            self.assertEqual(r.status_code, 200)
            names = {a["name"] for a in r.json()}
            self.assertIn("executor", names)
            self.assertNotIn("controller", names)  # без heartbeat — мёртвый, не показываем
            ex = next(a for a in r.json() if a["name"] == "executor")
            self.assertEqual(ex["status"], "online")
            self.assertFalse(ex["sleeping"])
            self.assertTrue(ex["live"])
            self.assertTrue(ex["chat_ok"])
            self.assertIn("heartbeat_age_sec", ex)
        finally:
            app_mod._opencode_db_path = old

    def test_chat_agents_stale_hidden(self):
        """Агент без свежего heartbeat (offline/stale) НЕ показывается в чате."""
        old = app_mod._opencode_db_path
        app_mod._opencode_db_path = lambda: None
        try:
            self.client.post("/heartbeat", json={"agent": "zombie"})
            # делаем heartbeat старым
            import datetime as _dt
            self.store._conn.execute(
                "UPDATE agents SET last_seen=? WHERE name='zombie'",
                ((_dt.datetime.now() - _dt.timedelta(minutes=10)).isoformat(),))
            self.store._conn.commit()
            r = self.client.get("/api/chat/agents")
            names = {a["name"] for a in r.json()}
            self.assertNotIn("zombie", names)
        finally:
            app_mod._opencode_db_path = old

    def test_chat_agents_live_session_shown(self):
        """Живая сессия субагента (running) показывается в чате; имя = канал
        session-<sid>, display_name = agent; можно писать (chat_ok=True)."""
        old = app_mod._opencode_db_path
        app_mod._opencode_db_path = lambda: None
        try:
            s = self.client.post("/api/sessions", json={"project": "P", "task": "A-5",
                                                        "agent": "session-A-5"}).json()
            self.client.post(f"/api/sessions/{s['id']}/start", json={"pid": 777})
            r = self.client.get("/api/chat/agents")
            rows = [a for a in r.json() if a["kind"] == "session"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], f"session-{s['id']}")
            self.assertEqual(rows[0]["display_name"], "session-A-5")
            self.assertEqual(rows[0]["session_id"], s["id"])
            self.assertTrue(rows[0]["chat_ok"], "сессии можно писать из чата")
            self.assertEqual(rows[0]["current_task"], "A-5")
        finally:
            app_mod._opencode_db_path = old

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

    def test_agent_create_roles(self):
        """POST /api/agents — сессия с ролью и скиллом (контролёр/мост/тестировщик)."""
        for role in ("controller", "browser", "reviewer"):
            r = self.client.post("/api/agents", json={"role": role, "project": "P",
                                                      "task": "проверь статус"})
            self.assertEqual(r.status_code, 200, f"роль {role}")
            s = r.json()
            self.assertEqual(s["role"], role)
            self.assertTrue(s["id"].startswith("S-"))
            self.assertEqual(s["status"], "created")
            self.assertTrue(s["instruction"]["prompt"], "промпт роли должен быть")
            self.assertIn("SKILL.md", s["instruction"]["prompt"])
            # скилл роли подхвачен
            self.assertEqual(s["skill"], app_mod.AGENT_ROLES[role]["skill"])
            # агент зарегистрирован
            self.assertTrue(s["agent"].startswith("agent-" + role))

    def test_agent_create_bad_role(self):
        r = self.client.post("/api/agents", json={"role": "нет_такой"})
        self.assertEqual(r.status_code, 400)

    def test_agent_create_instruction_contains_task(self):
        r = self.client.post("/api/agents", json={"role": "executor", "project": "P",
                                                  "task": "сделай X"})
        self.assertIn("сделай X", r.json()["instruction"]["prompt"])


    def test_request_create_list_dispatch(self):
        """Сырое задание: БД + файл в Tasks\\Входящие + git-коммит + dispatch."""
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="req_"))
        (tmp / "Tasks" / "Входящие").mkdir(parents=True)
        (tmp / "Tasks" / "Активные").mkdir(parents=True)
        (tmp / "Tasks" / "Отчёты").mkdir(parents=True)
        (tmp / "Tasks" / "Архив").mkdir(parents=True)
        (tmp / "Tasks" / "Конвейер").mkdir(parents=True)
        import subprocess
        subprocess.run(["git", "-C", str(tmp), "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.email", "t@t"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.name", "t"],
                       capture_output=True)
        (tmp / "pipeline.yaml").write_text("root: .\nname: reqtest\n", encoding="utf-8")
        old = app_mod.load_config
        from pipeline.config import ProjectConfig
        app_mod.load_config = lambda name: ProjectConfig(name=name, root=tmp)
        try:
            r = self.client.post("/api/requests", json={"project": "reqtest",
                                                        "text": "Добавить экспорт в DXF"})
            self.assertEqual(r.status_code, 200, r.text)
            row = r.json()
            self.assertTrue(row["id"].startswith("R-"))
            self.assertEqual(row["status"], "new")
            self.assertTrue(row["file"].startswith("Tasks/Входящие/"), row["file"])
            self.assertTrue((tmp / "Tasks" / "Входящие" / Path(row["file"]).name).exists(),
                            "файл задания должен быть в Входящие")
            # в БД
            got = self.store.get_request(row["id"])
            self.assertEqual(got["text"], "Добавить экспорт в DXF")
            # git-коммит
            r2 = subprocess.run(["git", "-C", str(tmp), "log", "--oneline", "-1"],
                                capture_output=True, text=True)
            self.assertIn("inbox:", r2.stdout)
            # список
            lst = self.client.get("/api/requests", params={"project": "reqtest"}).json()
            self.assertEqual(len(lst), 1)
            # dispatch
            rd = self.client.post(f"/api/requests/{row['id']}/dispatch")
            self.assertEqual(rd.status_code, 200, rd.text)
            self.assertEqual(rd.json()["status"], "dispatched")
            self.assertTrue(list((tmp / "Tasks" / "Активные").glob("A-*.md")),
                            "dispatch должен создать задачу")
        finally:
            app_mod.load_config = old
            shutil.rmtree(tmp, ignore_errors=True)

    def test_request_empty_text(self):
        r = self.client.post("/api/requests", json={"project": "P", "text": "   "})
        self.assertEqual(r.status_code, 400)

    def test_request_dispatch_unknown(self):
        r = self.client.post("/api/requests/R-NOPE/dispatch")
        self.assertEqual(r.status_code, 404)

    def test_plan_api_endpoints_no_plan(self):
        """/api/plan/* отвечают без ошибок, когда план не задан."""
        import tempfile, shutil
        from pipeline.config import ProjectConfig
        tmp = Path(tempfile.mkdtemp(prefix="planapi_"))
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив", "Tasks/Конвейер"):
            (tmp / sub).mkdir(parents=True)
        cfg = ProjectConfig(name="_load", root=tmp)
        old = app_mod.load_config
        app_mod.load_config = lambda name: cfg
        try:
            r = self.client.get("/api/plan", params={"project": "_load"})
            self.assertEqual(r.status_code, 200)
            self.assertIn("error", r.json())
            for url in ("/api/plan/tasks", "/api/plan/filters",
                        "/api/plan/running", "/api/plan/durations",
                        "/api/plan/load", "/api/questions",
                        "/api/checkpoints", "/api/runner"):
                r2 = self.client.get(url, params={"project": "_load"})
                self.assertEqual(r2.status_code, 200, url)
        finally:
            app_mod.load_config = old
            shutil.rmtree(tmp, ignore_errors=True)

    def test_questions_answer_flow(self):
        """Вопрос -> ответ из панели: секция «## Ответы» дописана, повторный ответ запрещён."""
        import tempfile, shutil
        from pipeline.config import ProjectConfig
        tmp = Path(tempfile.mkdtemp(prefix="qa_"))
        qdir = tmp / "Tasks" / "Вопросы"
        qdir.mkdir(parents=True)
        cfg = ProjectConfig(name="_load", root=tmp)
        (qdir / "1.1_Q1.md").write_text(
            "---\nкарточка: 1.1\n---\n# ВОПРОС 1.1: какой формат?\n"
            "## Варианты\n- A) json\n- B) md\n## Ответы\n", encoding="utf-8")
        old = app_mod.load_config
        app_mod.load_config = lambda name: cfg
        try:
            lst = self.client.get("/api/questions", params={"project": "_load"}).json()
            self.assertEqual(len(lst), 1)
            self.assertFalse(lst[0]["answered"])
            r = self.client.post("/api/questions/1.1_Q1/answer",
                                 json={"project": "_load", "text": "Выбирай A"})
            self.assertEqual(r.status_code, 200)
            content = (qdir / "1.1_Q1.md").read_text(encoding="utf-8")
            self.assertIn("## Ответы\n\nВыбирай A", content)
            lst2 = self.client.get("/api/questions", params={"project": "_load"}).json()
            self.assertTrue(lst2[0]["answered"])
            # повторный ответ — конфликт
            r2 = self.client.post("/api/questions/1.1_Q1/answer",
                                  json={"project": "_load", "text": "ещё"})
            self.assertEqual(r2.status_code, 409)
        finally:
            app_mod.load_config = old
            shutil.rmtree(tmp, ignore_errors=True)


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


class AgentSpawnTest(unittest.TestCase):
    """Карточка 1.2: POST /api/agents поднимает живой session_worker
    (права write из crew-профиля проекта, cwd = корень проекта)."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.client = TestClient(app_mod.app)

    def setUp(self):
        for t in ("events", "messages", "agents", "sessions"):
            app_mod.store._conn.execute(f"DELETE FROM {t}")
        app_mod.store._conn.commit()
        self.tmp = Path(tempfile.mkdtemp(prefix="spawn_"))
        (self.tmp / "Tasks").mkdir(parents=True, exist_ok=True)

    def _fake_cfg(self):
        class FakeCfg:
            name = "spawnproj"
            root = self.tmp
            crew_permissions = "write"
        return FakeCfg()

    def test_spawn_worker_and_write_permissions(self):
        spawned = []
        with mock.patch.object(app_mod, "_run_detached",
                               lambda cmd, cwd: spawned.append((cmd, cwd))), \
             mock.patch.object(app_mod, "load_config", lambda n: self._fake_cfg()):
            r = self.client.post("/api/agents", json={"role": "controller",
                                                      "project": "spawnproj"})
        self.assertEqual(r.status_code, 200)
        s = r.json()
        self.assertEqual(len(spawned), 1, "воркер должен быть запущен один раз")
        cmd, cwd = spawned[0]
        self.assertIn("session_worker.py", " ".join(cmd))
        self.assertIn("--session", cmd)
        self.assertIn(s["id"], cmd)
        self.assertIn("--project", cmd)
        self.assertEqual(cwd, str(self.tmp))
        perm = self.tmp / ".opencode" / "permissions.json"
        self.assertTrue(perm.exists(), "шаблон прав write развёрнут при первом спавне")
        self.assertIn('"allow"', perm.read_text(encoding="utf-8"))

    def test_existing_permissions_not_touched(self):
        d = self.tmp / ".opencode"
        d.mkdir(parents=True, exist_ok=True)
        (d / "permissions.json").write_text('{"permissions": {"edit": "deny"}}',
                                            encoding="utf-8")
        with mock.patch.object(app_mod, "_run_detached", lambda cmd, cwd: None), \
             mock.patch.object(app_mod, "load_config", lambda n: self._fake_cfg()):
            r = self.client.post("/api/agents", json={"role": "executor",
                                                      "project": "spawnproj"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((d / "permissions.json").read_text(encoding="utf-8"),
                         '{"permissions": {"edit": "deny"}}',
                         "существующий профиль прав не перезаписывается")

    def test_spawn_failure_is_soft(self):
        def boom(cmd, cwd):
            raise RuntimeError("нет процесса")
        with mock.patch.object(app_mod, "_run_detached", boom), \
             mock.patch.object(app_mod, "load_config", lambda n: self._fake_cfg()):
            r = self.client.post("/api/agents", json={"role": "controller",
                                                      "project": "spawnproj"})
        self.assertEqual(r.status_code == 200, True,
                         "ошибка спавна не ломает создание сессии")
        evs = [e for e in app_mod.store.recent_events(limit=10)
               if e["type"] == "session_created"]
        self.assertTrue(evs, "событие session_created опубликовано")
        self.assertIn("spawn", json.dumps(evs[-1].get("payload", {}),
                                         ensure_ascii=False).lower())

    def test_unknown_project_creates_without_spawn(self):
        spawned = []

        def cfg_err(name):
            raise app_mod.ConfigError("не найден")

        with mock.patch.object(app_mod, "_run_detached",
                               lambda cmd, cwd: spawned.append(cmd)), \
             mock.patch.object(app_mod, "load_config", cfg_err):
            r = self.client.post("/api/agents", json={"role": "executor",
                                                      "project": "нет_такого"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(spawned, [], "без конфига проекта спавна нет")


class TerminalEndpointTest(unittest.TestCase):
    """Карточка 2.1: POST /api/chat/agents/terminal открывает видимый
    терминал с agents/tui_cycle.py (промпт через env PIPELINE_TUI_PROMPT)."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.client = TestClient(app_mod.app)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="term_"))
        (self.tmp / "Tasks").mkdir(parents=True, exist_ok=True)
        captured_root = self.tmp

        class FakeCfg:
            name = "termproj"
            root = captured_root
            crew_permissions = "write"
        self.cfg = FakeCfg

    def test_opens_console_with_tui_cycle(self):
        captured = {}

        def fake_popen(cmd, cwd=None, env=None, creationflags=0):
            captured["cmd"] = list(cmd)
            captured["cwd"] = cwd
            captured["env"] = dict(env or {})
            return mock.Mock()

        with mock.patch.object(app_mod, "load_config", lambda n: self.cfg), \
             mock.patch("shutil.which", lambda name: None), \
             mock.patch("subprocess.Popen", fake_popen):
            r = self.client.post("/api/chat/agents/terminal",
                                 json={"project": "termproj", "role": "executor",
                                       "prompt": "дело"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        joined = " ".join(captured["cmd"])
        self.assertIn("tui_cycle.py", joined)
        self.assertIn("--project", captured["cmd"])
        self.assertIn("termproj", captured["cmd"])
        self.assertIn("--role", captured["cmd"])
        self.assertEqual(captured["env"].get("PIPELINE_TUI_PROMPT"), "дело")
        self.assertEqual(captured["cwd"], str(self.tmp))

    def test_wezterm_preferred_when_available(self):
        """Владелец пользуется WezTerm: окно агента открывается в нём."""
        captured = {}

        def fake_popen(cmd, cwd=None, env=None, creationflags=0):
            captured["cmd"] = list(cmd)
            captured["cwd"] = cwd
            captured["env"] = dict(env or {})
            return mock.Mock()

        wt = "C:\\Program Files\\WezTerm\\wezterm.exe"
        with mock.patch.object(app_mod, "load_config", lambda n: self.cfg), \
             mock.patch("shutil.which", lambda name: wt if name == "wezterm" else None), \
             mock.patch("subprocess.Popen", fake_popen):
            r = self.client.post("/api/chat/agents/terminal",
                                 json={"project": "termproj", "role": "executor",
                                       "prompt": "дело"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["terminal"], "WezTerm")
        cmd = captured["cmd"]
        self.assertEqual(cmd[0], wt)
        self.assertEqual(cmd[1], "start")
        self.assertIn("--cwd", cmd)
        self.assertIn("tui_cycle.py", " ".join(cmd))
        self.assertEqual(captured["cwd"], str(self.tmp))
        self.assertEqual(captured["env"].get("PIPELINE_TUI_PROMPT"), "дело")

    def test_unknown_project_404(self):
        def cfg_err(name):
            raise app_mod.ConfigError("не найден")

        with mock.patch.object(app_mod, "load_config", cfg_err), \
             mock.patch("subprocess.Popen", lambda *a, **k: mock.Mock()):
            r = self.client.post("/api/chat/agents/terminal",
                                 json={"project": "нет_такого"})
        self.assertEqual(r.status_code, 404)

    def test_manager_global_without_project(self):
        """ОБЩИЙ менеджер на все проекты: project не нужен."""
        captured = {}

        def fake_popen(cmd, cwd=None, env=None, creationflags=0):
            captured["cmd"] = list(cmd)
            captured["cwd"] = cwd
            return mock.Mock()

        with mock.patch("shutil.which", lambda name: None), \
             mock.patch("subprocess.Popen", fake_popen):
            r = self.client.post("/api/chat/agents/terminal",
                                 json={"role": "manager"})
        self.assertEqual(r.status_code, 200)
        joined = " ".join(captured["cmd"])
        self.assertIn("project_manager.py", joined)
        self.assertNotIn("--project", captured["cmd"],
                         "глобальный менеджер ведёт все проекты сразу")
        self.assertEqual(captured["cwd"],
                         str(Path(app_mod.__file__).resolve().parent.parent))

    def test_executor_role_requires_project(self):
        with mock.patch("subprocess.Popen", lambda *a, **k: mock.Mock()):
            r = self.client.post("/api/chat/agents/terminal",
                                 json={"role": "executor"})
        self.assertEqual(r.status_code, 400)


class MetaVersionTest(unittest.TestCase):
    """Баннер «перезапустите сервер»: версия кода в healthz и /api/meta."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.client = TestClient(app_mod.app)

    def test_healthz_reports_code_version_key(self):
        data = self.client.get("/healthz").json()
        self.assertTrue(data["ok"])
        self.assertIn("head", data)

    def test_meta_start_vs_now(self):
        m = self.client.get("/api/meta").json()
        self.assertEqual(m["head_at_start"], app_mod._SERVER_HEAD)
        self.assertIn("head_now", m)

    def test_git_head_is_8_chars_in_repo(self):
        self.assertEqual(len(app_mod._git_head()), 8,
                         "тесты выполняются в git-репозитории")
