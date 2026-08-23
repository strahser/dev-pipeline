# -*- coding: utf-8 -*-
"""Тесты план-раннера (agents/plan_runner.py) с замоканным субагентом.

Запуск: python -X utf8 tests/test_plan_runner.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
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


def make_valid_run():
    """Фейковый субагент: создаёт валидный отчёт (>200 байт), возвращает rc=0."""
    calls = []

    def _run(cfg, tid, report, log, **kw):
        calls.append(tid)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            f"# ОТЧЁТ карточка:{tid}\n## Что было не так\n—\n"
            "## Что сделано\nправки по карточке " + tid + "\n"
            "## Доказательства\nbuild EXIT 0; tests passed\n" + "pad" * 40 + "\n",
            encoding="utf-8")
        return 0
    return _run, calls


PLAN_CP = """# План: тест чекпоинтов

## Сводная таблица СДР

| СДР | Наименование | Тип | Статус |
|---|---|---|---|
| `1` | Этап 1 | summary | Открыто |
| `1.1` | Первая | execution | Открыто |

---

### Карточка 1.1 — Первая

- **Статус**: `open`
- **Чекпоинт**: да
- **Цель**: первое дело с чекпоинтом.
- **Критерии приёмки**:
  1. Файл X существует.
- **Зависимости**: нет.
"""


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

    def test_lock_blocks_second_runner(self):
        """Инцидент 2026-08-22: два конвейера на один проект запрещены."""
        from agents import plan_runner as pr
        r1 = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, dry_run=True)
        self.assertTrue(r1._lock_acquire())
        r2 = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, dry_run=True)
        self.assertFalse(r2._lock_acquire())
        r1._lock_release()
        self.assertTrue(r2._lock_acquire())
        r2._lock_release()

    def test_run_sets_and_releases_lock(self):
        from agents import plan_runner as pr
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, dry_run=True)
        r.run()
        self.assertFalse((self.tmp / "Tasks" / "Конвейер" / "runner.lock").exists(),
                         "лок снят после нормального завершения")

    def test_second_runner_run_returns_rc5(self):
        """Карточка 1.3: второй конвейер на каталоге получает отказ rc=5."""
        from agents import plan_runner as pr
        r1 = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, dry_run=True)
        self.assertTrue(r1._lock_acquire())
        r2 = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, dry_run=True)
        self.assertEqual(r2.run(), 5, "второй старт упирается в занятый лок")
        r1._lock_release()

    def test_lock_stale_takeover_rules(self):
        """Карточка 1.3: свежий лок без pid не отдаётся; протухший перехватывается."""
        import agents.agent_manager as am
        from agents import plan_runner as pr
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, dry_run=True)
        lp = self.tmp / "Tasks" / "Конвейер" / "runner.lock"
        stale_ts = time.time() - pr.PlanRunner.LOCK_STALE_SEC - 60

        lp.write_text(json.dumps({"pid": 0, "ts": time.time()}), encoding="utf-8")
        self.assertFalse(r._lock_acquire(), "свежий лок без живого pid — отказ")

        lp.write_text(json.dumps({"pid": 0, "ts": stale_ts}), encoding="utf-8")
        self.assertTrue(r._lock_acquire(), "протухший лок без pid перехватывается")
        r._lock_release()

        lp.write_text(json.dumps({"pid": 4242424, "ts": stale_ts}), encoding="utf-8")
        with mock.patch.object(am, "_pid_alive", return_value=False):
            self.assertTrue(r._lock_acquire(), "протухший лок с мёртвым pid "
                                               "перехватывается")
        r._lock_release()

    def test_dispatch_md_contains_exact_report_path(self):
        """Хотфикс U1.2: постановка называет точный путь отчёта."""
        from agents import plan_runner as pr
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path)
        card = load_plan(self.plan_path).card("1.1")
        exact = self.cfg.abs_tasks_dir("reports") / "1.1_Отчёт_2026-08-23_101010.md"
        md = r._dispatch_md(card, report=exact)
        self.assertIn(str(exact), md.read_text(encoding="utf-8"))

    def test_report_fallback_accepts_fresh_card_report(self):
        """Инцидент U1.2: отчёт субагента без метки времени не должен
        сжигать попытки — свежий отчёт карточки принимается с фолбэком."""
        from agents import plan_runner as pr
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=1)
        reports_dir = self.cfg.abs_tasks_dir("reports")
        seen = {}

        def fake_run(cfg, tid, report, log, **kw):
            p = reports_dir / f"{tid}_Отчёт_2026-08-23.md"  # без ЧЧММСС
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# ОТЧЁТ\n## Что сделано\nработа есть\n"
                         "## Доказательства\nтесты 78/78\n" + "pad" * 40,
                         encoding="utf-8")
            return 0

        def spy_verify(runner_self, card, report_path=None):
            seen["report"] = Path(report_path) if report_path else None
            return "PASS"

        with mock.patch.object(pr, "run_subagent", fake_run), \
             mock.patch.object(pr.PlanRunner, "_verify", spy_verify):
            rc = r.run()

        self.assertEqual(rc, 0, "работа не должна сгорать из-за имени файла")
        self.assertEqual(load_plan(self.plan_path).card("1.1").status, "done")
        self.assertIsNotNone(seen["report"])
        self.assertEqual(seen["report"].name, "1.1_Отчёт_2026-08-23.md",
                         "вердикт строится по фактическому отчёту")

    def test_verdict_uses_current_attempt_report(self):
        """Карточка 1.1: в Отчётах лежат отчёты двух разных карточек — вердикт
        строится по отчёту текущей карточки/попытки (PIPELINE_EXPECT_REPORT
        установлен на время вызова и восстановлен после)."""
        import os
        from agents import plan_runner as pr
        import pipeline.cli as cli_mod

        reports_dir = self.cfg.abs_tasks_dir("reports")
        foreign = reports_dir / "9.9_Отчёт_другая_карточка.md"
        foreign.write_text("# ОТЧЁТ ЧУЖОЙ КАРТОЧКИ\n## Что сделано\nне наше\n",
                           encoding="utf-8")

        seen = {}

        def fake_verify(cfg, args):
            seen["env_during"] = os.environ.get("PIPELINE_EXPECT_REPORT")
            exp = Path(seen["env_during"]) if seen["env_during"] else None
            ok = bool(exp and exp.exists()
                      and f"карточка:{args.task}" in exp.read_text(encoding="utf-8"))
            vf = cfg.abs_tasks_dir("reports") / f"{args.task}_Вердикт_контролёра_test.md"
            vf.write_text(f"Вердикт: **{'PASS' if ok else 'FAIL'}**\n"
                          f"отчёт: {exp.name if exp else '—'}\n", encoding="utf-8")
            return 0 if ok else 2

        def fake_run(cfg, tid, report, log, **kw):
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                f"# ОТЧЁТ карточка:{tid}\n## Что было не так\n—\n## Что сделано\nправки\n"
                "## Доказательства\nbuild EXIT 0; тесты зелёные\n" + "pad" * 40 + "\n",
                encoding="utf-8")
            seen["attempt_report"] = Path(report)
            return 0

        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=0)
        with mock.patch.object(pr, "run_subagent", fake_run), \
             mock.patch.object(cli_mod, "cmd_verify", fake_verify):
            rc = r.run()

        self.assertEqual(rc, 0)
        self.assertEqual(load_plan(self.plan_path).card("1.1").status, "done")
        # env во время вызова указывал ровно на отчёт текущей попытки
        self.assertEqual(seen.get("env_during"), str(seen["attempt_report"]))
        # вердикт построен по отчёту текущей карточки, а не чужому (свежему) файлу
        vtxt = (reports_dir / "1.1_Вердикт_контролёра_test.md").read_text(encoding="utf-8")
        self.assertIn("**PASS**", vtxt)
        self.assertIn(seen["attempt_report"].name, vtxt)
        self.assertNotIn(foreign.name, vtxt)
        # env восстановлен после вызова
        self.assertIsNone(os.environ.get("PIPELINE_EXPECT_REPORT"))

    def test_missing_own_report_with_foreign_present_fails(self):
        """Карточка 1.1: отчёта своей карточки нет (субагент не создал), чужой
        отчёт другой карточки лежит рядом — вердикт FAIL, PASS невозможен."""
        from agents import plan_runner as pr
        reports_dir = self.cfg.abs_tasks_dir("reports")
        (reports_dir / "9.9_Отчёт_чужой.md").write_text(
            "# ОТЧЁТ ЧУЖОЙ\n## Что сделано\nвсё отлично\n## Доказательства\nPASS\n",
            encoding="utf-8")

        card = load_plan(self.plan_path).card("1.1")
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=0)
        r._dispatch_md(card)  # постановка есть, своего отчёта нет

        def silent_run(cfg, tid, report, log, **kw):
            return 0  # rc=0, но файл отчёта не создан

        with mock.patch.object(pr, "run_subagent", silent_run):
            res = r._verify(card, report_path=reports_dir / "нет_такого_отчёта.md")
        self.assertEqual(res, "FAIL")


class CheckpointRetryTest(unittest.TestCase):
    """Карточка 1.2: рестарт чекпоинта владельцем не расходует бюджет попыток."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prunner_cp_"))
        self.cfg = make_cfg(self.tmp)
        self.plan_path = self.tmp / "plan_cp.md"
        self.plan_path.write_text(PLAN_CP, encoding="utf-8")

    def _fake_client(self):
        events = []

        class FakeClient:
            def notify(self, ev_type, to="", task="", payload=None):
                events.append((ev_type, task, dict(payload or {})))

        return FakeClient(), events

    def test_checkpoint_retry_gives_full_budget(self):
        from agents import plan_runner as pr
        client, _ = self._fake_client()
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=1,
                          client=client)
        fake_run, calls = make_valid_run()
        # волна 1: FAIL→PASS; рестарт; волна 2: FAIL→PASS; рестарт; волна 3: PASS
        with mock.patch.object(pr, "run_subagent", fake_run), \
             mock.patch.object(pr.PlanRunner, "_verify",
                               side_effect=["FAIL", "PASS", "FAIL", "PASS", "PASS"]), \
             mock.patch.object(pr.PlanRunner, "_wait_decision",
                               side_effect=["retry", "retry", "approve"]):
            rc = r.run()
        self.assertEqual(rc, 0, "рестарты владельцем не должны приводить к rc=3")
        plan = load_plan(self.plan_path)
        self.assertEqual(plan.card("1.1").status, "done")
        self.assertEqual(len(calls), 5,
                         "5 попыток: 2 волны по (FAIL,PASS) + финальный PASS; "
                         "разделяемый бюджет дал бы rc=3 раньше")

    def test_card_retried_by_owner_event_with_wave_number(self):
        from agents import plan_runner as pr
        client, events = self._fake_client()
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=1,
                          client=client)
        fake_run, _ = make_valid_run()
        with mock.patch.object(pr, "run_subagent", fake_run), \
             mock.patch.object(pr.PlanRunner, "_verify",
                               side_effect=["FAIL", "PASS", "FAIL", "PASS", "PASS"]), \
             mock.patch.object(pr.PlanRunner, "_wait_decision",
                               side_effect=["retry", "retry", "approve"]):
            rc = r.run()
        self.assertEqual(rc, 0)
        retried = [(t, p.get("wave")) for (ev, t, p) in events
                   if ev == "card_retried_by_owner"]
        self.assertEqual(retried, [("1.1", 1), ("1.1", 2)],
                         "событие card_retried_by_owner с нарастающим номером волны")


class CheckpointWaitTest(unittest.TestCase):
    """Карточки 1.4/6.1: напоминания checkpoint_waiting при ожидании чекпоинта."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prunner_wait_"))
        self.cfg = make_cfg(self.tmp)

    def test_checkpoint_wait_sends_reminders(self):
        from agents import checkpoint as cp
        events = []
        dec_path = cp.cp_dir(self.cfg) / "1.1.decision.json"
        t0 = 1000.0

        class FakeClock:
            def __init__(self):
                self.t = t0

            def time(self):
                return self.t

            def sleep(self, sec):
                self.t += sec
                # решение владельца приходит на 25-й минуте ожидания
                if self.t - t0 >= 1500 and not dec_path.exists():
                    dec_path.parent.mkdir(parents=True, exist_ok=True)
                    dec_path.write_text(json.dumps({"decision": "approved"}),
                                        encoding="utf-8")

        clock = FakeClock()
        action = cp.wait_decision(self.cfg, "1.1", poll_sec=15, remind_sec=600,
                                  clock=clock,
                                  notify=lambda ev, task="", payload=None:
                                  events.append((ev, dict(payload or {}))))

        self.assertEqual(action, "approve", "действие строго из decision.json")
        waiting = [p for (ev, p) in events if ev == "checkpoint_waiting"]
        self.assertGreaterEqual(len(waiting), 2,
                                "за 25 минут минимум два напоминания")
        waited_sec = [p.get("waiting_sec") for p in waiting]
        self.assertEqual(waited_sec, sorted(waited_sec),
                         "время ожидания в напоминаниях нарастает")
        self.assertTrue(all(p.get("waiting_sec") >= 600 for p in waiting),
                        "напоминания не раньше checkpoint_remind_sec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
