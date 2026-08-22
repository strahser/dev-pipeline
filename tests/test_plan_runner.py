# -*- coding: utf-8 -*-
"""Тесты план-раннера (agents/plan_runner.py) с замоканным субагентом.

Запуск: python -X utf8 tests/test_plan_runner.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import ProjectConfig   # noqa: E402
from pipeline.plans import load as load_plan  # noqa: E402

PLAN = """# План: тест раннера

## Сводная таблица СДР

| СДР | Наименование | Тип | Статус |
|---|---|---|---|
| `1` | Этап 1 | summary | Открыто |
| `1.1` | Первая | execution | Открыто |
| `1.2` | Вторая | execution | Открыто |

---

### Карточка 1.1 — Первая

- **Статус**: `open`
- **Цель**: первое дело.
- **Критерии приёмки**:
  1. Файл X существует.
- **Зависимости**: нет.

### Карточка 1.2 — Вторая

- **Статус**: `open`
- **Цель**: второе дело.
- **Критерии приёмки**:
  1. Тест Y зелёный.
- **Зависимости**: 1.1.
"""


def make_cfg(tmp: Path) -> ProjectConfig:
    for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив", "Tasks/Конвейер"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    return ProjectConfig(name="_runner", root=tmp, msbuild="dotnet",
                         sln="X.csproj", test_runner="dotnet",
                         checkpoint_stages=False)


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prunner_"))
        self.cfg = make_cfg(self.tmp)
        self.plan_path = self.tmp / "plan.md"
        self.plan_path.write_text(PLAN, encoding="utf-8")

    def _fake_run(self):
        """Фейковый субагент: создаёт валидный отчёт (>200 байт), возвращает rc=0."""
        calls = []

        def _run(cfg, tid, report, log, **kw):
            calls.append(tid)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                f"# ОТЧЁТ: {tid}\n## Что было не так\n—\n"
                "## Что сделано\nправки по карточке " + tid + "\n"
                "## Доказательства\nbuild EXIT 0; tests passed 90/90; "
                "rg pattern → 0 совпадений\n" + "pad" * 40 + "\n",
                encoding="utf-8")
            return 0
        return _run, calls

    def test_once_executes_first_ready_card_and_marks_done(self):
        from agents import plan_runner as pr
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=0)
        fake_run, _ = self._fake_run()
        with mock.patch.object(pr, "run_subagent", fake_run), \
             mock.patch.object(pr.PlanRunner, "_verify", return_value="PASS"):
            rc = r.run()
        self.assertEqual(rc, 0)
        plan = load_plan(self.plan_path)
        self.assertEqual(plan.card("1.1").status, "done")
        self.assertEqual(plan.card("1.2").status, "open")
        # постановка создана в Активные
        self.assertTrue(list((self.tmp / "Tasks" / "Активные").glob("1.1_*.md")))
        # состояние раннера записано
        state = (self.tmp / "Tasks" / "Конвейер" / "runner_state.json")
        self.assertTrue(state.exists())

    def test_verify_fail_then_retry_then_pass(self):
        from agents import plan_runner as pr
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=1)
        fake_run, calls = self._fake_run()
        with mock.patch.object(pr, "run_subagent", fake_run), \
             mock.patch.object(pr.PlanRunner, "_verify",
                               side_effect=["FAIL", "PASS"]):
            rc = r.run()
        self.assertEqual(rc, 0)
        plan = load_plan(self.plan_path)
        self.assertEqual(plan.card("1.1").status, "done")
        self.assertEqual(len(calls), 2, "один ретрай после FAIL")

    def test_verify_fail_exhausted_stops_with_code_3(self):
        from agents import plan_runner as pr
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=1)

        def fail_run(cfg, tid, report, log, **kw):
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("# ОТЧЁТ\n## Что сделано\nничего\n"
                              "## Доказательства\nFAIL тесты\n", encoding="utf-8")
            return 1
        with mock.patch.object(pr, "run_subagent", fail_run), \
             mock.patch.object(pr.PlanRunner, "_verify", return_value="FAIL"):
            rc = r.run()
        self.assertEqual(rc, 3)
        plan = load_plan(self.plan_path)
        self.assertEqual(plan.card("1.1").status, "open", "карточка не закрыта при провале")
        self.assertEqual(len(list((self.tmp / "Tasks" / "Активные").glob("1.1_*.md"))), 1,
                         "постановка не дублируется между попытками")

    def test_dispatch_md_reuses_existing(self):
        from agents import plan_runner as pr
        plan = load_plan(self.plan_path)
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path)
        card = plan.card("1.1")
        p1 = r._dispatch_md(card)
        p2 = r._dispatch_md(card)
        self.assertEqual(p1, p2)

    def test_stage_complete_after(self):
        from agents import plan_runner as pr
        plan = load_plan(self.plan_path)
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path)
        c11, c12 = plan.card("1.1"), plan.card("1.2")
        self.assertFalse(r._stage_complete_after(plan, c11))   # сиблинг ещё открыт
        plans_set_done = load_plan(self.plan_path)
        from pipeline.plans import set_card_status
        set_card_status(self.plan_path, "1.2", "done") if c12.status != "done" else None
        plan2 = load_plan(self.plan_path)
        self.assertTrue(r._stage_complete_after(plan2, c11))

    def test_dry_run_does_not_touch_plan(self):
        from agents import plan_runner as pr
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, dry_run=True)
        rc = r.run()
        self.assertEqual(rc, 0)
        plan = load_plan(self.plan_path)
        self.assertEqual(plan.card("1.1").status, "open")


if __name__ == "__main__":
    unittest.main(verbosity=2)
