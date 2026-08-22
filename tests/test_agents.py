# -*- coding: utf-8 -*-
"""Тесты агент-менеджера и инструмента дампа: split_mission, dispatch_chunk, verify, парсеры.

Запуск: python -X utf8 tests/test_agents.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import agent_manager as am            # noqa: E402
from pipeline.models import parse_tests_dotnet    # noqa: E402


class TestSplitMission(unittest.TestCase):
    def test_split_by_headings(self):
        text = "## A\nтекст а\n\n## B\nтекст б\n\n## C\nтекст в"
        parts = am.split_mission(text, 3)
        self.assertEqual(len(parts), 3)
        self.assertIn("## A", parts[0])

    def test_split_merges_when_few_sections(self):
        text = "## A\n1\n\n## B\n2\n\n## C\n3\n\n## D\n4\n\n## E\n5\n\n## F\n6"
        parts = am.split_mission(text, 3)
        self.assertEqual(len(parts), 3)
        # сумма кусков покрывает исходник
        self.assertIn("## A", parts[0])
        self.assertIn("## F", parts[-1])

    def test_split_small_text(self):
        parts = am.split_mission("просто текст без заголовков\n\nи ещё абзац", 2)
        self.assertTrue(parts)

    def test_slug(self):
        self.assertEqual(am.slug("Вынос Projects в отдельный проект"), "Вынос_Projects_в_отдельный_проект")
        self.assertEqual(am.slug("###"), "задача")


class TestDispatchChunk(unittest.TestCase):
    def test_creates_task_file(self):
        tmp = Path(tempfile.mkdtemp(prefix="am_"))
        (tmp / "Tasks" / "Активные").mkdir(parents=True)
        (tmp / "Tasks" / "Отчёты").mkdir(parents=True)
        (tmp / "Tasks" / "Архив").mkdir(parents=True)
        (tmp / "Tasks" / "Конвейер").mkdir(parents=True)

        import argparse
        from pipeline.config import ProjectConfig
        cfg = ProjectConfig(name="_test", root=tmp)
        tid = am.dispatch_chunk(cfg, "задача про воздуховоды", 1, 2, "Миссия")
        self.assertTrue(tid.startswith("A-"))
        files = list((tmp / "Tasks" / "Активные").glob("*.md"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text(encoding="utf-8")
        self.assertIn(f"id: {tid}", content)
        self.assertIn("статус: open", content)

    def test_dispatch_creates_task_file_with_boundaries(self):
        """dispatch_chunk создаёт MD-задачу с границами и результатом."""
        tmp = Path(tempfile.mkdtemp(prefix="am_tdl_"))
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив", "Tasks/Конвейер"):
            (tmp / sub).mkdir(parents=True)
        from pipeline.config import ProjectConfig
        cfg = ProjectConfig(name="_test", root=tmp, msbuild="dotnet",
                            sln="X.csproj", test_runner="dotnet")
        tid = am.dispatch_chunk(cfg, "Сделать X с доказательством", 1, 2, "Миссия")
        files = list((tmp / "Tasks" / "Активные").glob(f"{tid}_*.md"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("Границы", content)
        self.assertIn("Отчёт", content)


class TestExecutorTakeTask(unittest.TestCase):
    """executor take_task: open -> in_progress по MD-файлу."""

    def _cfg(self, tmp):
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив", "Tasks/Конвейер"):
            (tmp / sub).mkdir(parents=True)
        from pipeline.config import ProjectConfig
        return ProjectConfig(name="_t", root=tmp, msbuild="dotnet",
                             sln="X.csproj", test_runner="dotnet")

    def test_take_task_marks_in_progress(self):
        import tempfile
        from pipeline.models import Task as T
        from agents import executor_client as ec
        tmp = Path(tempfile.mkdtemp(prefix="ex_md_"))
        cfg = self._cfg(tmp)
        tid = am.dispatch_chunk(cfg, "задача", 1, 1, "М")
        f = next(iter(cfg.abs_tasks_dir("active").glob(f"{tid}_*.md")))
        self.assertEqual(T.from_file(f).status, "open")
        task = ec.take_task(cfg, tid)
        self.assertIsNotNone(task)
        f2 = next(iter(cfg.abs_tasks_dir("active").glob(f"{tid}_*.md")))
        self.assertEqual(T.from_file(f2).status, "in_progress")
        # повторно взять нельзя
        self.assertIsNone(ec.take_task(cfg, tid))
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


class TestDumpVerify(unittest.TestCase):
    """Порт CollisionVerifier: известные случаи пересечений."""

    def _s(self, el, px, py, bx=None, by=None, refs=None, w=2.0, h=1.0):
        bx = px if bx is None else bx
        by = py if by is None else by
        return {
            "elementId": el, "placementPoint": {"x": px, "y": py},
            "basePoint": {"x": bx, "y": by},
            "direction": {"x": 1, "y": 0}, "referencedElementIds": refs or [],
            "categoryId": -2008044, "cachedWidthFeet": w, "cachedHeightFeet": h,
        }

    def test_no_collisions_empty(self):
        from agents import dump_suggestions as ds
        self.assertEqual(ds.verify([]), [])

    def test_tag_tag_overlap(self):
        from agents import dump_suggestions as ds
        s = [self._s(1, 0, 0, w=2.0, h=1.0), self._s(2, 1.0, 0, w=2.0, h=1.0)]
        issues = ds.verify(s)
        self.assertTrue(any("Tag-Tag" in i for i in issues))

    def test_separated_tags_no_overlap(self):
        from agents import dump_suggestions as ds
        s = [self._s(1, 0, 0, w=2.0, h=1.0), self._s(2, 3.0, 0, w=2.0, h=1.0)]
        self.assertEqual(ds.verify(s), [])

    def test_leader_tag_intersection(self):
        from agents import dump_suggestions as ds
        # лидер от (0,0) к (10,0) проходит через марку в (5,0) размером 2x2
        s = [self._s(1, 10, 0, bx=0, by=0, w=2, h=1),
             self._s(2, 5, 0, w=2, h=1)]
        issues = ds.verify(s)
        self.assertTrue(any("Leader-Tag" in i for i in issues))

    def test_leader_leader_intersection(self):
        from agents import dump_suggestions as ds
        s = [self._s(1, 5, 0, bx=0, by=0),
             self._s(2, 5, 5, bx=0, by=5)]
        # лидеры параллельны, не пересекаются; проверяем, что нет ложного срабатывания
        self.assertEqual(ds.verify(s), [])

    def test_point_in_polygon(self):
        from agents import dump_suggestions as ds
        square = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}]
        self.assertTrue(ds.point_in_polygon({"x": 5, "y": 5}, square))
        self.assertFalse(ds.point_in_polygon({"x": 15, "y": 5}, square))


class TestDotnetParser(unittest.TestCase):
    def test_parse_ok(self):
        out = "Не пройден!: не пройдено     7, пройдено     8, пропущено     0, всего    15"
        self.assertEqual(parse_tests_dotnet(out), (8, 15, 7))

    def test_parse_pass(self):
        out = "Пройдено!: не пройдено 0, пройдено 15, пропущено 0, всего 15"
        self.assertEqual(parse_tests_dotnet(out), (15, 15, 0))


class TestCheckStalled(unittest.TestCase):
    """Детектор зависших задач (agent_watch.check_stalled) — файловые маркеры."""

    def _cfg(self, tmp):
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив", "Tasks/Конвейер"):
            (tmp / sub).mkdir(parents=True)
        from pipeline.config import ProjectConfig
        return ProjectConfig(name="_t", root=tmp)

    def _mk_task(self, cfg, tid, status="in_progress", age_sec=0):
        f = cfg.abs_tasks_dir("active") / f"{tid}_Задача.md"
        f.write_text(f"---\nid: {tid}\nстатус: {status}\n---\n# ЗАДАЧА\n", encoding="utf-8")
        if age_sec:
            old = time.time() - age_sec
            import os
            os.utime(f, (old, old))

    def test_stalled_detected_and_marked_once(self):
        import tempfile
        from agents import agent_watch as aw
        tmp = Path(tempfile.mkdtemp(prefix="stall_"))
        cfg = self._cfg(tmp)
        self._mk_task(cfg, "A-01", age_sec=5 * 86400)      # зависла: 5 дней
        self._mk_task(cfg, "A-02", age_sec=3600)           # свежая: час
        n = aw.check_stalled(cfg, None, timeout_sec=10800)
        self.assertEqual(n, 1)
        marker = tmp / "Tasks" / "Конвейер" / "stalled" / "A-01.txt"
        self.assertTrue(marker.exists(), "маркер stalled записан")
        # повторный вызов не помечает второй раз
        self.assertEqual(aw.check_stalled(cfg, None, timeout_sec=10800), 0)
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_stalled_skips_with_report_and_open(self):
        import tempfile
        from agents import agent_watch as aw
        tmp = Path(tempfile.mkdtemp(prefix="stall2_"))
        cfg = self._cfg(tmp)
        self._mk_task(cfg, "A-01", age_sec=5 * 86400)
        (cfg.abs_tasks_dir("reports") / "A-01_Отчёт_2026-08-01.md").write_text(
            "# ОТЧЁТ: A-01\n## Что сделано\nx\n", encoding="utf-8")
        # open-задача без отчёта — не «зависшая»
        self._mk_task(cfg, "A-02", status="open", age_sec=5 * 86400)
        self.assertEqual(aw.check_stalled(cfg, None, timeout_sec=10800), 0)
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_clear_stalled(self):
        import tempfile
        from agents import agent_watch as aw
        tmp = Path(tempfile.mkdtemp(prefix="stall3_"))
        cfg = self._cfg(tmp)
        self._mk_task(cfg, "A-01", age_sec=5 * 86400)
        aw.check_stalled(cfg, None, timeout_sec=10800)
        self.assertTrue((tmp / "Tasks" / "Конвейер" / "stalled" / "A-01.txt").exists())
        aw.clear_stalled(cfg, "A-01")
        self.assertFalse((tmp / "Tasks" / "Конвейер" / "stalled" / "A-01.txt").exists())
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


class TestEnsureReportNoFake(unittest.TestCase):
    """Менеджер не создаёт фейковый отчёт при rc!=0/обрыве — маркер stalled."""
    def _cfg(self, tmp):
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив", "Tasks/Конвейер"):
            (tmp / sub).mkdir(parents=True)
        from pipeline.config import ProjectConfig
        return ProjectConfig(name="_t", root=tmp)

    def test_rc_nonzero_no_report_marks_stalled(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="rep_"))
        cfg = self._cfg(tmp)
        ok = am._ensure_report(cfg, "A-01", rc=1)
        self.assertFalse(ok)
        # фейкового отчёта НЕ создано
        self.assertEqual(list((tmp / "Tasks" / "Отчёты").glob("A-01_*")), [])
        marker = tmp / "Tasks" / "Конвейер" / "stalled" / "A-01.txt"
        self.assertTrue(marker.exists(), "обрыв без отчёта -> маркер stalled для редиспатча")
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_timeout_rc_124_marks_stalled(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="rep2_"))
        cfg = self._cfg(tmp)
        am._ensure_report(cfg, "A-01", rc=124)
        self.assertTrue((tmp / "Tasks" / "Конвейер" / "stalled" / "A-01.txt").exists())
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_report_exists_ok(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="rep3_"))
        cfg = self._cfg(tmp)
        (tmp / "Tasks" / "Отчёты" / "A-01_Отчёт_2026-08-07.md").write_text(
            "# ОТЧЁТ: A-01", encoding="utf-8")
        self.assertTrue(am._ensure_report(cfg, "A-01", rc=0))
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


class TestSessionMode(unittest.TestCase):
    """Явные сессии: run_subagent_session создаёт сессию на сервере,
    session_worker получает инструкцию через сервер; legacy — фолбэк."""

    def _cfg(self, tmp):
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив", "Tasks/Конвейер"):
            (tmp / sub).mkdir(parents=True)
        from pipeline.config import ProjectConfig
        return ProjectConfig(name="_t", root=tmp, msbuild="dotnet",
                             sln="X.csproj", test_runner="dotnet")

    class FakeClient:
        """Заглушка серверного клиента: сессии живут в памяти."""
        def __init__(self):
            self.sessions = {}
            self.alive = True
            self.created = []

        def server_alive(self, timeout=2.0):
            return self.alive

        def create_session(self, project, task="", agent="", role="worker", model="",
                           skill="", instruction=None, sid=""):
            s = {"id": sid or f"S-{len(self.sessions) + 1}", "project": project,
                 "task": task, "agent": agent, "role": role, "model": model,
                 "skill": skill, "status": "created", "instruction": instruction or {}}
            self.sessions[s["id"]] = s
            self.created.append(s)
            return s

        def get_session(self, sid):
            return self.sessions.get(sid)

        def session_start(self, sid, pid=None, cmd=""):
            s = self.sessions.get(sid)
            if s:
                s["status"] = "running"
                if pid:
                    s["pid"] = pid
            return s

        def session_status(self, sid, status, note="", report="", error=""):
            s = self.sessions.get(sid)
            if s:
                s["status"] = status
                if note:
                    s["note"] = note
                if report:
                    s["report"] = report
                if error:
                    s["error"] = error
            return s

        def session_heartbeat(self, sid):
            return True

        def session_kill(self, sid):
            s = self.sessions.get(sid)
            if s:
                s["status"] = "killed"
            return {"ok": True}

    def _mk_task_md(self, cfg, tid, status="open"):
        f = cfg.abs_tasks_dir("active") / f"{tid}_Задача.md"
        f.write_text(f"""---
id: {tid}
статус: {status}
---
# ЗАДАЧА: тест
## Цель
## Требования
""", encoding="utf-8")

    def test_build_subprompt_contains_task_and_report(self):
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp(prefix="sp_"))
        cfg = self._cfg(tmp)
        p = am._build_subprompt(cfg, "A-01", Path("A-01_x.md"), Path("rep.md"), skill="pipeline-executor")
        self.assertIn("A-01_x.md", p)
        self.assertIn("rep.md", p)
        self.assertIn("pipeline-executor", p)
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_run_subagent_session_done(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="sess_"))
        cfg = self._cfg(tmp)
        self._mk_task_md(cfg, "A-01")
        fc = self.FakeClient()
        report = cfg.abs_tasks_dir("reports") / "A-01_Отчёт_2026-08-08.md"
        log = tmp / "Tasks" / "Конвейер" / "logs" / "A-01_run.log"
        # субагент: стартуем в фоне, сессия переходит running -> done + отчёт
        import threading
        def _worker():
            s = fc.created[0]
            am._mark_in_progress = None  # не трогаем
            fc.session_start(s["id"], pid=1)
            fc.session_status(s["id"], "running", note="работаю")
            fc.session_status(s["id"], "done", report=str(report))
        t = threading.Timer(0.5, _worker)
        t.start()
        rc = am.run_subagent_session(cfg, "A-01", report, log, client=fc, poll_sec=1)
        t.join(timeout=10)
        self.assertEqual(rc, 0)
        self.assertEqual(fc.created[0]["status"], "done")
        # инструкция передана в сессию (задача/отчёт/промпт)
        instr = fc.created[0]["instruction"]
        self.assertEqual(instr["task_id"], "A-01")
        self.assertIn("A-01", instr["prompt"])
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_run_subagent_session_failed(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="sessf_"))
        cfg = self._cfg(tmp)
        self._mk_task_md(cfg, "A-02")
        fc = self.FakeClient()
        report = cfg.abs_tasks_dir("reports") / "A-02_Отчёт_2026-08-08.md"
        log = tmp / "Tasks" / "Конвейер" / "logs" / "A-02_run.log"
        def _worker():
            s = fc.created[0]
            fc.session_start(s["id"], pid=1)
            fc.session_status(s["id"], "failed", error="rc=1, сборка упала")
        import threading
        t = threading.Timer(0.5, _worker)
        t.start()
        rc = am.run_subagent_session(cfg, "A-02", report, log, client=fc, poll_sec=1)
        t.join(timeout=10)
        self.assertEqual(rc, 1)
        self.assertEqual(fc.created[0]["status"], "failed")
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_run_subagent_session_killed_on_timeout(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="sessk_"))
        cfg = self._cfg(tmp)
        self._mk_task_md(cfg, "A-03")
        fc = self.FakeClient()
        report = cfg.abs_tasks_dir("reports") / "A-03_Отчёт_2026-08-08.md"
        log = tmp / "Tasks" / "Конвейер" / "logs" / "A-03_run.log"
        # субагент молчит -> менеджер убивает сессию по таймауту
        old_timeout = am.SUBAGENT_TIMEOUT
        am.SUBAGENT_TIMEOUT = 3
        try:
            rc = am.run_subagent_session(cfg, "A-03", report, log, client=fc, poll_sec=1)
        finally:
            am.SUBAGENT_TIMEOUT = old_timeout
        self.assertEqual(rc, 124)
        self.assertEqual(fc.created[0]["status"], "killed")
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_run_subagent_falls_back_to_legacy_when_server_down(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="sessl_"))
        cfg = self._cfg(tmp)
        self._mk_task_md(cfg, "A-04")
        fc = self.FakeClient()
        fc.alive = False
        report = cfg.abs_tasks_dir("reports") / "A-04_Отчёт_2026-08-08.md"
        log = tmp / "Tasks" / "Конвейер" / "logs" / "A-04_run.log"
        # legacy-режим пытается запустить opencode; с неизвестной командой — rc!=0,
        # но главное: НЕ создаёт сессию (fc.created пуст)
        am.OPENCODE = "python -c \"import sys; sys.exit(7)\""
        rc = am.run_subagent(cfg, "A-04", report, log, client=fc)
        self.assertEqual(fc.created, [], "без сервера сессия не создаётся")
        self.assertNotEqual(rc, 0)
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


class TestSubagentZombies(unittest.TestCase):
    """Сторож: убийство сирот-субагентов по PID-файлам менеджера."""
    def _cfg(self, tmp):
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив",
                    "Tasks/Конвейер", "Tasks/Конвейер/logs"):
            (tmp / sub).mkdir(parents=True)
        from pipeline.config import ProjectConfig
        return ProjectConfig(name="_t", root=tmp)

    def test_old_alive_killed_stale_removed_young_kept(self):
        import subprocess, sys, tempfile, time
        from agents import agent_watch as aw
        tmp = Path(tempfile.mkdtemp(prefix="zom_"))
        cfg = self._cfg(tmp)
        logs = tmp / "Tasks" / "Конвейер" / "logs"
        # старый PID-файл живого процесса -> убить дерево
        victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        (logs / "A-01.pid").write_text(
            f"{victim.pid}\n{int(time.time()) - 7200}", encoding="utf-8")
        # старый PID-файл мёртвого процесса -> просто удалить
        (logs / "A-02.pid").write_text(
            f"999999999\n{int(time.time()) - 7200}", encoding="utf-8")
        n = aw.check_subagent_zombies(cfg, None, max_age_sec=1800)
        self.assertEqual(n, 1, "убит только живой сирота")
        self.assertFalse(am._pid_alive(victim.pid), "процесс-сирота убит")
        victim.wait()
        self.assertFalse((logs / "A-01.pid").exists())
        self.assertFalse((logs / "A-02.pid").exists(), "мёртвый pid-файл удалён")
        # молодой PID-файл живого процесса -> не трогаем
        young = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        (logs / "A-03.pid").write_text(
            f"{young.pid}\n{int(time.time())}", encoding="utf-8")
        self.assertEqual(aw.check_subagent_zombies(cfg, None, max_age_sec=1800), 0)
        self.assertTrue((logs / "A-03.pid").exists())
        am._kill_tree(young.pid)
        young.wait()
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_zombie_marks_task_stalled(self):
        import subprocess, sys, tempfile, time
        from agents import agent_watch as aw
        tmp = Path(tempfile.mkdtemp(prefix="zom2_"))
        cfg = self._cfg(tmp)
        logs = tmp / "Tasks" / "Конвейер" / "logs"
        victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        (logs / "A-05.pid").write_text(
            f"{victim.pid}\n{int(time.time()) - 7200}", encoding="utf-8")
        aw.check_subagent_zombies(cfg, None, max_age_sec=1800)
        victim.wait()
        marker = tmp / "Tasks" / "Конвейер" / "stalled" / "A-05.txt"
        self.assertTrue(marker.exists(), "сирота -> маркер task_stalled")
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
