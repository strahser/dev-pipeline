# -*- coding: utf-8 -*-
"""Тесты агент-менеджера и инструмента дампа: split_mission, dispatch_chunk, verify, парсеры.

Запуск: python -X utf8 tests/test_agents.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
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

    def test_dispatch_creates_tdl_task(self):
        """dispatch_chunk создаёт TDL JSON-задачу (wbs, goal), если tdl_enabled."""
        tmp = Path(tempfile.mkdtemp(prefix="am_tdl_"))
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив", "Tasks/Конвейер"):
            (tmp / sub).mkdir(parents=True)
        from pipeline.config import ProjectConfig
        cfg = ProjectConfig(name="_test", root=tmp, msbuild="dotnet",
                            sln="X.csproj", test_runner="dotnet")
        tid = am.dispatch_chunk(cfg, "Сделать X с доказательством", 1, 2, "Миссия")
        from pipeline.tdl import store as tdl_store
        t = tdl_store.load_task(cfg, tid)
        self.assertIsNotNone(t, "JSON-задача должна создаться")
        self.assertEqual(t["wbs_code"], "1.01")
        self.assertIn("Сделать X", t["goal"])
        self.assertTrue(t["verification"]["commands"], "должны быть команды проверки")


class TestExecutorTdl(unittest.TestCase):
    """executor take_task / tdl-report при включённом TDL."""

    def _cfg(self, tmp):
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив", "Tasks/Конвейер"):
            (tmp / sub).mkdir(parents=True)
        from pipeline.config import ProjectConfig
        return ProjectConfig(name="_t", root=tmp, msbuild="dotnet",
                             sln="X.csproj", test_runner="dotnet")

    def test_take_task_marks_tdl_in_progress(self):
        import tempfile
        from pipeline.tdl import store as tdl_store
        from agents import executor_client as ec
        tmp = Path(tempfile.mkdtemp(prefix="ex_tdl_"))
        cfg = self._cfg(tmp)
        # создать задачу через dispatch (создаст и MD, и JSON)
        tid = am.dispatch_chunk(cfg, "задача", 1, 1, "М")
        # MD-задача open
        task = ec.take_task(cfg, tid)
        self.assertIsNotNone(task)
        # JSON-задача переведена в in_progress
        t = tdl_store.load_task(cfg, tid)
        self.assertEqual(t["workflow_state"], "in_progress")
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_tdl_report_created_after_execution(self):
        import tempfile, time
        from pipeline.tdl import store as tdl_store
        from agents import executor_client as ec
        tmp = Path(tempfile.mkdtemp(prefix="ex_tdl2_"))
        cfg = self._cfg(tmp)
        tid = am.dispatch_chunk(cfg, "задача", 1, 1, "М")
        # имитируем отчёт, созданный субагентом
        md = cfg.abs_tasks_dir("reports") / f"{tid}_Отчёт_2026-08-07.md"
        md.write_text("# ОТЧЁТ\n## Что сделано\nсделал X\n## Доказательства\nлог\n", encoding="utf-8")
        ec._tdl_report_if_needed(cfg, tid, md)
        r = tdl_store.load_report(cfg, tid)
        self.assertIsNotNone(r, "JSON-отчёт должен создаться из MD")
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
    """Детектор зависших задач (agent_watch.check_stalled)."""

    def _cfg(self, tmp):
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив",
                    "Tasks/Конвейер", "Tasks/JSON/Active"):
            (tmp / sub).mkdir(parents=True)
        from pipeline.config import ProjectConfig
        return ProjectConfig(name="_t", root=tmp)

    def _mk_task(self, cfg, tid, start, workflow="in_progress"):
        import datetime
        from pipeline.tdl._tpl import make_task
        from pipeline.tdl import store as tdl_store
        t = make_task(tid, "_t", "Задача", "1.1", goal="ц", acceptance=["к"], commands=["b"])
        t["workflow_state"] = workflow
        t["dates"]["start"] = start
        tdl_store.save_task(cfg, t)

    def test_stalled_detected_and_marked_once(self):
        import datetime
        import tempfile
        from agents import agent_watch as aw
        from pipeline.tdl import store as tdl_store
        tmp = Path(tempfile.mkdtemp(prefix="stall_"))
        cfg = self._cfg(tmp)
        now = datetime.datetime.now()
        old = (now - datetime.timedelta(days=5)).isoformat()          # зависла: 5 дней
        fresh = (now - datetime.timedelta(hours=1)).isoformat()       # свежая: час
        self._mk_task(cfg, "A-01", old)
        self._mk_task(cfg, "A-02", fresh)
        n = aw.check_stalled(cfg, None, timeout_sec=10800)
        self.assertEqual(n, 1)
        t = tdl_store.load_task(cfg, "A-01")
        self.assertEqual(t["workflow_state"], "in_progress")  # статус не меняем
        self.assertTrue(any(h["action"] == "stalled" for h in t["history"]))
        # повторный вызов не помечает второй раз
        self.assertEqual(aw.check_stalled(cfg, None, timeout_sec=10800), 0)
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_stalled_skips_with_report(self):
        import datetime
        import tempfile
        from agents import agent_watch as aw
        from pipeline.tdl import store as tdl_store
        tmp = Path(tempfile.mkdtemp(prefix="stall2_"))
        cfg = self._cfg(tmp)
        old = (datetime.datetime.now() - datetime.timedelta(days=5)).isoformat()
        self._mk_task(cfg, "A-01", old)
        (tdl_store.reports_dir(cfg) / "A-01_2026-08-01.report.json").write_text(
            '{"report_id":"A-01_2026-08-01"}', encoding="utf-8")
        self.assertEqual(aw.check_stalled(cfg, None, timeout_sec=10800), 0)
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


class TestEnsureReportNoFake(unittest.TestCase):
    """Менеджер не создаёт фейковый отчёт при rc!=0/обрыве — пометка stalled."""

    def _cfg(self, tmp):
        for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив", "Tasks/Конвейер"):
            (tmp / sub).mkdir(parents=True)
        from pipeline.config import ProjectConfig
        return ProjectConfig(name="_t", root=tmp)

    def _mk_task(self, cfg, tid):
        from pipeline.tdl._tpl import make_task
        from pipeline.tdl import store as tdl_store
        t = make_task(tid, "_t", "Задача", "1.1", goal="ц", acceptance=["к"], commands=["b"])
        tdl_store.save_task(cfg, t)

    def test_rc_nonzero_no_report_marks_stalled(self):
        import tempfile
        from pipeline.tdl import store as tdl_store
        tmp = Path(tempfile.mkdtemp(prefix="rep_"))
        cfg = self._cfg(tmp)
        self._mk_task(cfg, "A-01")
        ok = am._ensure_report(cfg, "A-01", rc=1)
        self.assertFalse(ok)
        # фейкового отчёта НЕ создано
        self.assertEqual(list((tmp / "Tasks" / "Отчёты").glob("A-01_*")), [])
        t = tdl_store.load_task(cfg, "A-01")
        self.assertTrue(any(h["action"] == "task_stalled" for h in t["history"]),
                        "обрыв без отчёта -> task_stalled для редиспатча")
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_timeout_rc_124_marks_stalled(self):
        import tempfile
        from pipeline.tdl import store as tdl_store
        tmp = Path(tempfile.mkdtemp(prefix="rep2_"))
        cfg = self._cfg(tmp)
        self._mk_task(cfg, "A-01")
        am._ensure_report(cfg, "A-01", rc=124)
        t = tdl_store.load_task(cfg, "A-01")
        self.assertTrue(any(h["action"] == "task_stalled" for h in t["history"]))
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def test_report_exists_ok(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="rep3_"))
        cfg = self._cfg(tmp)
        (tmp / "Tasks" / "Отчёты" / "A-01_Отчёт_2026-08-07.md").write_text(
            "# ОТЧЁТ: A-01", encoding="utf-8")
        self.assertTrue(am._ensure_report(cfg, "A-01", rc=0))
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
        from pipeline.tdl import store as tdl_store
        from pipeline.tdl._tpl import make_task
        tmp = Path(tempfile.mkdtemp(prefix="zom2_"))
        cfg = self._cfg(tmp)
        t = make_task("A-05", "_t", "Задача", "1.1", goal="ц", acceptance=["к"], commands=["b"])
        tdl_store.save_task(cfg, t)
        logs = tmp / "Tasks" / "Конвейер" / "logs"
        victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        (logs / "A-05.pid").write_text(
            f"{victim.pid}\n{int(time.time()) - 7200}", encoding="utf-8")
        aw.check_subagent_zombies(cfg, None, max_age_sec=1800)
        victim.wait()
        t2 = tdl_store.load_task(cfg, "A-05")
        self.assertTrue(any(h["action"] == "task_stalled" for h in t2["history"]))
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
