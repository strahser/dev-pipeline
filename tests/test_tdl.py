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


if __name__ == "__main__":
    unittest.main(verbosity=2)
