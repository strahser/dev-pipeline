# -*- coding: utf-8 -*-
"""Тесты qwen_bridge: парсинг FILE-блоков, TDL-задача, strip-префикса, полнота.

Без живого Qwen (только чистые функции).
Запуск: python -X utf8 tests/test_qwen_bridge.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import qwen_bridge as qb   # noqa: E402


class TestStripPrefix(unittest.TestCase):
    def test_strip_textscenario(self):
        r = "**Вопрос:** что-то\n**Ответ:**\n```FILE: a.txt\nhi\n```"
        self.assertEqual(qb._strip_question_prefix(r).strip(), "```FILE: a.txt\nhi\n```")

    def test_no_prefix(self):
        self.assertEqual(qb._strip_question_prefix("просто текст"), "просто текст")


class TestApplyFiles(unittest.TestCase):
    def test_apply_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resp = "**Ответ:**\n```FILE: src/a.cs\nclass A {}\n```\nEND OF RESPONSE"
            applied = qb._apply_files(resp, root)
            self.assertEqual(applied[0]["action"], "written")
            self.assertTrue((root / "src" / "a.cs").exists())
            self.assertEqual((root / "src" / "a.cs").read_text(encoding="utf-8"), "class A {}")

    def test_skip_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            applied = qb._apply_files("```FILE: x.txt\n   \n```", Path(tmp))
            self.assertEqual(applied[0]["action"], "skipped_empty")

    def test_no_force_skips_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.txt").write_text("old", encoding="utf-8")
            applied = qb._apply_files("```FILE: x.txt\nnew\n```", root, force=False)
            self.assertEqual(applied[0]["action"], "skipped_exists")
            self.assertEqual((root / "x.txt").read_text(encoding="utf-8"), "old")

    def test_force_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.txt").write_text("old", encoding="utf-8")
            applied = qb._apply_files("```FILE: x.txt\nnew\n```", root, force=True)
            self.assertEqual(applied[0]["action"], "written")
            self.assertEqual((root / "x.txt").read_text(encoding="utf-8"), "new")


class TestIsComplete(unittest.TestCase):
    def test_with_file_block(self):
        self.assertTrue(qb._is_complete("```FILE: a.cs\ncode\n```"))

    def test_with_end_marker(self):
        self.assertTrue(qb._is_complete("анализ...\nEND OF RESPONSE"))

    def test_incomplete(self):
        self.assertFalse(qb._is_complete("просто начало ответа без маркеров"))


class TestTaskJsonHuman(unittest.TestCase):
    def test_tdl_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "A-01.task.json"
            p.write_text(json.dumps({
                "task_id": "A-01", "name": "Фикс пола",
                "goal": "Пол по зонам строится", "status": "open",
                "workflow_state": "issued", "module": "Floor",
                "acceptance_criteria": ["Строится пол"], "layer": "core",
                "verification": {"commands": ["dotnet build"]},
            }, ensure_ascii=False), encoding="utf-8")
            txt = qb._task_json_human(p)
            self.assertIn("Фикс пола", txt)
            self.assertIn("Пол по зонам строится", txt)
            self.assertIn("dotnet build", txt)

    def test_bad_json_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "A-02.task.json"
            p.write_text("не json", encoding="utf-8")
            txt = qb._task_json_human(p)
            self.assertIn("A-02", txt)


class TestBuildQuestion(unittest.TestCase):
    def test_includes_task_and_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.md"
            task.write_text("# ЗАДАЧА\nсделать X", encoding="utf-8")
            ctx = Path(tmp) / "ctx.md"
            ctx.write_text("контекст", encoding="utf-8")
            q = qb._build_question(str(task), [str(ctx)], "инструкция")
            self.assertIn("сделать X", q)
            self.assertIn("контекст", q)
            self.assertIn("инструкция", q)
            self.assertIn("FILE:", q)


if __name__ == "__main__":
    unittest.main(verbosity=2)
