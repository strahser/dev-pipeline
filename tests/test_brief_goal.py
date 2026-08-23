# -*- coding: utf-8 -*-
"""Тесты GOAL.md в брифе и промпте (карточка 4.1).

Запуск: python -X utf8 tests/test_brief_goal.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import ProjectConfig  # noqa: E402
from pipeline.plans import load as load_plan  # noqa: E402

PLAN = """# План: тест цели

## Сводная таблица СДР

| СДР | Наименование | Тип | Статус |
|---|---|---|---|
| `1` | Этап 1 | summary | Открыто |
| `1.1` | Первая | execution | Открыто |

---

### Карточка 1.1 — Первая

- **Статус**: `open`
- **Цель**: первое дело.
- **Критерии приёмки**:
  1. Файл X существует.
- **Зависимости**: нет.
"""

GOAL = """# GOAL — Тестовый проект

## Цель
Довести конвейер до автономности.

## Результат
Панель и ревьюер принимают этапы.

## Не делать
Не трогать чужие планы.
"""


def make_cfg(tmp: Path) -> ProjectConfig:
    for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив",
                "Tasks/Конвейер", "_goalt/_current"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    (tmp / "_goalt" / "_current" / "plan.md").write_text(PLAN, encoding="utf-8")
    return ProjectConfig(name="_goalt", root=tmp, msbuild="none", sln="",
                         test_runner="none", checkpoint_stages=False,
                         plan_repo=[tmp])


class BriefGoalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pgoal_"))
        self.cfg = make_cfg(self.tmp)

    def test_goal_in_brief_when_file_exists(self):
        from pipeline.brief import build_brief, goal_path, goal_section
        (self.cfg.plan_dir().parent / "GOAL.md").write_text(GOAL, encoding="utf-8")
        self.assertTrue(goal_path(self.cfg).is_file())
        self.assertIn("автономности", goal_section(self.cfg))
        brief = build_brief(self.cfg)
        self.assertIn("## Цель проекта", brief)
        self.assertIn("Не делать", brief)

    def test_no_goal_no_crash(self):
        from pipeline.brief import build_brief, goal_section
        self.assertEqual(goal_section(self.cfg), "")
        brief = build_brief(self.cfg)
        self.assertNotIn("## Цель проекта", brief)


class PromptGoalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pgoal_run_"))
        self.cfg = make_cfg(self.tmp)
        (self.cfg.plan_dir().parent / "GOAL.md").write_text(GOAL, encoding="utf-8")

    def test_runner_prompt_contains_goal(self):
        from agents import plan_runner as pr
        captured = {}

        def fake_run(cfg, tid, report, log, *, prompt_override="", **kw):
            captured["prompt"] = prompt_override or ""
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("# ОТЧЁТ\n## Что сделано\nработа\n"
                              "## Доказательства\nтесты зелёные\n" + "pad" * 60,
                              encoding="utf-8")
            return 0

        r = pr.PlanRunner(self.cfg, once=True, retries=0)
        with mock.patch.object(pr, "run_subagent", fake_run), \
             mock.patch.object(pr.PlanRunner, "_verify", return_value="PASS"):
            rc = r.run()
        self.assertEqual(rc, 0)
        self.assertIn("Цель проекта", captured["prompt"])
        self.assertIn("автономности", captured["prompt"],
                      "содержимое GOAL.md вставлено в промпт карточки")


if __name__ == "__main__":
    unittest.main(verbosity=2)
