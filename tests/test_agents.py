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


if __name__ == "__main__":
    unittest.main(verbosity=2)
