# -*- coding: utf-8 -*-
"""Тесты TDL: валидаторы, store, миграция, правила закрытия.

Запуск: python -X utf8 tests/test_tdl.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.tdl import store, validate      # noqa: E402
from pipeline.tdl._tpl import make_report, make_task, make_verdict  # noqa: E402
from pipeline.tdl.migrate import migrate_project, migrate_task_file  # noqa: E402
from pipeline.config import ProjectConfig      # noqa: E402


def _cfg(tmp) -> ProjectConfig:
    return ProjectConfig(name="_tdl", root=Path(tmp))


class TestValidateTask(unittest.TestCase):
    def test_valid_task(self):
        t = make_task("A-01", "P", "Задача", "1.1", goal="цель", acceptance=["к1"], commands=["build"])
        self.assertEqual(validate.validate_task(t), [])

    def test_missing_goal(self):
        t = make_task("A-01", "P", "Задача", "1.1", goal="", acceptance=["к1"], commands=["build"])
        errs = validate.validate_task(t)
        codes = {e["code"] for e in errs}
        self.assertIn("missing_goal", codes)

    def test_done_without_evidence_not_validate_closed(self):
        t = make_task("A-01", "P", "Задача", "1.1", goal="ц", acceptance=["к1"], commands=["b"])
        t["status"] = "done"
        t["workflow_state"] = "verified"
        errs = validate.validate_task(t)
        # статус done допустим по схеме (остальное проверяет validate_project/can_close)
        self.assertEqual([e for e in errs if e["code"] == "bad_status"], [])

    def test_wbs_depth(self):
        t = make_task("A-01", "P", "З", "1.1.1.1.1", goal="ц", acceptance=["к"], commands=["b"])
        codes = {e["code"] for e in validate.validate_task(t)}
        self.assertIn("wbs_depth", codes)

    def test_bad_entity(self):
        t = make_task("A-01", "P", "З", "1.1", goal="ц", acceptance=["к"], commands=["b"])
        t["entity_type"] = "tdl.report"
        codes = {e["code"] for e in validate.validate_task(t)}
        self.assertIn("bad_entity", codes)


class TestValidateReport(unittest.TestCase):
    def test_valid_report(self):
        r = make_report("A-01", "1.1")
        r["work_done"] = ["сделано"]
        r["problem"] = "проблема"
        r["evidence"] = [{"evidence_id": "E1", "type": "build_log", "result": "pass", "exit_code": 0}]
        r["verification_commands"] = ["dotnet build"]
        r["report_status"] = "final"
        self.assertEqual(validate.validate_report(r), [])

    def test_empty_evidence(self):
        r = make_report("A-01", "1.1")
        r["work_done"] = ["сделано"]
        codes = {e["code"] for e in validate.validate_report(r)}
        self.assertIn("empty_evidence", codes)

    def test_bad_exit_code_without_rule(self):
        r = make_report("A-01", "1.1")
        r["work_done"] = ["сделано"]
        r["evidence"] = [{"evidence_id": "E1", "type": "test_report", "exit_code": 1}]
        codes = {e["code"] for e in validate.validate_report(r)}
        self.assertIn("evidence_bad_exit", codes)


class TestValidateVerdict(unittest.TestCase):
    def test_pass_verdict(self):
        v = make_verdict("A-01", "A-01_2026-08-06", result="pass")
        v["checks"] = [{"check_id": "c1", "name": "сборка", "status": "pass",
                        "expected": "0", "actual": "0", "critical": True}]
        self.assertEqual(validate.validate_verdict(v), [])

    def test_fail_without_fixes(self):
        v = make_verdict("A-01", "A-01_2026-08-06", result="fail")
        v["checks"] = [{"check_id": "c1", "name": "сборка", "status": "fail",
                        "expected": "0", "actual": "1"}]
        codes = {e["code"] for e in validate.validate_verdict(v)}
        self.assertIn("fail_no_fixes", codes)

    def test_pass_with_critical_fail(self):
        v = make_verdict("A-01", "A-01_2026-08-06", result="pass")
        v["checks"] = [{"check_id": "c1", "name": "сборка", "status": "fail",
                        "expected": "0", "actual": "1", "critical": True}]
        codes = {e["code"] for e in validate.validate_verdict(v)}
        self.assertIn("pass_with_critical_fail", codes)


class TestCanClose(unittest.TestCase):
    def test_cannot_without_report(self):
        t = make_task("A-01", "P", "З", "1.1", goal="ц", acceptance=["к"], commands=["b"])
        ok, why = validate.can_close_task(t, None, None)
        self.assertFalse(ok)
        self.assertIn("отчёт", why)

    def test_cannot_without_verdict(self):
        t = make_task("A-01", "P", "З", "1.1", goal="ц", acceptance=["к"], commands=["b"])
        r = make_report("A-01", "1.1")
        r["evidence"] = [{"evidence_id": "E1", "type": "build_log", "result": "pass"}]
        ok, why = validate.can_close_task(t, r, None)
        self.assertFalse(ok)
        self.assertIn("вердикт", why)

    def test_pass(self):
        t = make_task("A-01", "P", "З", "1.1", goal="ц", acceptance=["к"], commands=["b"])
        r = make_report("A-01", "1.1")
        r["evidence"] = [{"evidence_id": "E1", "type": "build_log", "result": "pass"}]
        v = make_verdict("A-01", "A-01_2026-08-06", result="pass")
        v["can_move_forward"] = True
        ok, why = validate.can_close_task(t, r, v)
        self.assertTrue(ok)


class TestStore(unittest.TestCase):
    def test_save_load_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp)
            t = make_task("A-01", "P", "З", "1.1", goal="ц", acceptance=["к"], commands=["b"])
            store.save_task(cfg, t)
            loaded = store.load_task(cfg, "A-01")
            self.assertEqual(loaded["task_id"], "A-01")
            self.assertEqual(loaded["goal"], "ц")

    def test_next_task_id_from_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp)
            # legacy markdown в корне Tasks
            (Path(tmp) / "Tasks" / "Активные").mkdir(parents=True)
            (Path(tmp) / "Tasks" / "Активные" / "A-03_x.md").write_text("---\nid: A-03\n---\n", encoding="utf-8")
            self.assertEqual(store.next_task_id(cfg), "A-04")

    def test_rebuild_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp)
            t = make_task("A-01", "P", "З", "1.1", goal="ц", acceptance=["к"], commands=["b"])
            store.save_task(cfg, t)
            store.rebuild_index(cfg)
            idx = store.load_index(cfg)
            self.assertEqual(len(idx["tasks"]), 1)
            self.assertEqual(idx["tasks"][0]["task_id"], "A-01")


class TestCloseSummaries(unittest.TestCase):
    """tdl-close-summaries: summary закрывается, когда все execution-потомки done."""

    def _mk_tree(self, root, leaf_done=True):
        """Миссия(2) -> этап(2.1) -> класс(2.1.1) -> лист(2.1.1.1)."""
        cfg = ProjectConfig(name="_tdl", root=Path(root))
        m = make_task("A-31", "_tdl", "Миссия", "2", is_summary=True, goal="ц", source="план")
        e = make_task("A-32", "_tdl", "Этап", "2.1", is_summary=True, goal="ц", source="план")
        k = make_task("A-33", "_tdl", "Класс", "2.1.1", is_summary=True, goal="ц", source="план")
        lf = make_task("A-34", "_tdl", "Лист", "2.1.1.1", goal="ц", acceptance=["к"], commands=["b"])
        if leaf_done:
            lf["status"] = "done"
            lf["workflow_state"] = "verified"
        for t in (m, e, k, lf):
            store.save_task(cfg, t)
        store.rebuild_index(cfg)
        return cfg

    def test_all_children_done_closes_summaries(self):
        import argparse
        from pipeline.tdl import cli as tdl_cli
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._mk_tree(tmp, leaf_done=True)
            rc = tdl_cli.tdl_close_summaries(cfg, argparse.Namespace())
            self.assertEqual(rc, 0)
            for tid in ("A-31", "A-32", "A-33"):
                t = store.load_task(cfg, tid)
                self.assertEqual(t["status"], "done", f"{tid} должна закрыться")
                self.assertEqual(t["workflow_state"], "verified")

    def test_leaf_open_keeps_summaries(self):
        import argparse
        from pipeline.tdl import cli as tdl_cli
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._mk_tree(tmp, leaf_done=False)
            tdl_cli.tdl_close_summaries(cfg, argparse.Namespace())
            for tid in ("A-31", "A-32", "A-33"):
                t = store.load_task(cfg, tid)
                self.assertEqual(t["status"], "open", f"{tid} не должна закрыться при open-листе")


class TestMigrate(unittest.TestCase):
    def test_migrate_conservative(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Tasks" / "Архив").mkdir(parents=True)
            task_md = root / "Tasks" / "Архив" / "A-01_Тест.md"
            task_md.write_text(
                "---\nid: A-01\nстатус: verified\nприоритет: высокий\n---\n"
                "# ЗАДАЧА: Тест\n\n## Требования (критерии приёмки)\nСделать X.\n\n"
                "## Границы (что НЕ делать)\nНе делать Y.\n", encoding="utf-8")
            cfg = ProjectConfig(name="_tdl", root=root)
            res = migrate_task_file(cfg, task_md)
            t = store.load_task(cfg, "A-01")
            # консервативно: legacy verified не даёт done без JSON-доказательств
            self.assertEqual(t["status"], "open")
            self.assertEqual(t["legacy_status"], "verified")
            self.assertEqual(t["workflow_state"], "pending_verdict")
            self.assertEqual(t["goal"], "Сделать X.")
            self.assertIn("Не делать Y.", t["constraints"][0])


class TestHierarchy(unittest.TestCase):
    """Иерархия миссии: level, is_summary, module/class/layer, render_tree."""

    def test_make_task_level_and_meta(self):
        t = make_task("A-31", "P", "Миссия", "2", is_summary=True,
                      goal="цель", source="план")
        self.assertTrue(t["is_summary"])
        self.assertEqual(t["task_kind"], "group")
        self.assertEqual(t["level"], 1)
        self.assertEqual(t["parent_wbs"], "")

    def test_make_task_leaf_meta(self):
        t = make_task("A-34", "P", "Лист", "2.1.1.1", goal="ц",
                      acceptance=["к"], commands=["b"],
                      module="MainAppHeatLoss", class_name="ViewModel", layer="ui")
        self.assertEqual(t["level"], 4)
        self.assertEqual(t["parent_wbs"], "2.1.1")
        self.assertEqual(t["module"], "MainAppHeatLoss")
        self.assertEqual(t["class_name"], "ViewModel")
        self.assertEqual(t["layer"], "ui")

    def test_validate_level_mismatch(self):
        t = make_task("A-01", "P", "З", "1.1", goal="ц", acceptance=["к"], commands=["b"])
        t["level"] = 3
        codes = {e["code"] for e in validate.validate_task(t)}
        self.assertIn("level_mismatch", codes)

    def test_render_tree_order(self):
        from pipeline.tdl import render
        tasks = [
            {"wbs_code": "2", "name": "Миссия", "is_summary": True, "status": "open", "task_id": "A-31"},
            {"wbs_code": "2.1", "name": "Этап", "is_summary": True, "status": "open", "task_id": "A-32"},
            {"wbs_code": "2.1.1", "name": "Класс", "is_summary": True, "status": "open", "task_id": "A-33", "class_name": "X", "layer": "core"},
            {"wbs_code": "2.1.1.1", "name": "Лист", "is_summary": False, "status": "done", "workflow_state": "verified", "task_id": "A-34"},
        ]
        md = render.render_tree(tasks)
        self.assertIn("Миссия", md)
        self.assertIn("2.1.1", md)
        # лист с class/layer
        self.assertIn("X", md)
        self.assertIn("core", md)
        # суммарная и листовая задачи отмечены по-разному
        self.assertIn("- 2", md)
        self.assertIn("• 2.1.1.1", md)


class TestDurations(unittest.TestCase):
    def test_make_task_estimate(self):
        t = make_task("A-50", "P", "Задача", "2.1", goal="ц", acceptance=["к"],
                      commands=["b"], estimate_sec=7200)
        self.assertEqual(t["dates"]["estimate_sec"], 7200)
        self.assertIsNone(t["dates"]["duration_sec"])

    def test_make_task_estimate_negative_not_set(self):
        t = make_task("A-50", "P", "Задача", "2.1", goal="ц", acceptance=["к"],
                      commands=["b"], estimate_sec=0)
        self.assertIsNone(t["dates"]["estimate_sec"])

    def test_estimate_sec_parse_hours_float(self):
        from pipeline.tdl.cli import _estimate_sec
        self.assertEqual(_estimate_sec(2), 7200)        # 2 -> 2 часа
        self.assertEqual(_estimate_sec(0.5), 1800)      # 0.5 -> 30 мин
        self.assertEqual(_estimate_sec(3600), 3600)     # >= 3600 -> сек как есть
        self.assertEqual(_estimate_sec("2ч 30м"), 9000)
        self.assertEqual(_estimate_sec("3.5h"), 12600)
        self.assertEqual(_estimate_sec("45м"), 2700)
        self.assertEqual(_estimate_sec("1д"), 86400)
        self.assertIsNone(_estimate_sec(""))
        self.assertIsNone(_estimate_sec(None))
        self.assertIsNone(_estimate_sec("abc"))

    def test_duration_sec_dates(self):
        from pipeline.tdl.cli import _duration_sec
        self.assertEqual(_duration_sec("2026-08-06", "2026-08-07"), 86400)
        self.assertEqual(_duration_sec("2026-08-06T10:00:00Z", "2026-08-06T12:30:00Z"), 9000)
        self.assertIsNone(_duration_sec(None, "2026-08-07"))
        self.assertIsNone(_duration_sec("2026-08-06", "не дата"))

    def test_render_task_card_durations(self):
        from pipeline.tdl import render
        t = make_task("A-50", "P", "Задача", "2.1", goal="ц", acceptance=["к"],
                      commands=["b"], estimate_sec=7200)
        t["dates"]["start"] = "2026-08-06"
        t["dates"]["finish"] = "2026-08-07"
        t["dates"]["duration_sec"] = 86400
        md = render.render_task_card(t)
        self.assertIn("Оценка (план): 2 ч", md)
        self.assertIn("Факт (длительность): 1 дн", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
