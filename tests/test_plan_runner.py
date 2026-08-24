# -*- coding: utf-8 -*-
"""Тесты план-раннера (agents/plan_runner.py) с замоканным субагентом.

Запуск: python -X utf8 tests/test_plan_runner.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
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


def make_cfg(tmp: Path, **extra) -> ProjectConfig:
    for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Архив", "Tasks/Конвейер"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    base = dict(name="_runner", root=tmp, msbuild="dotnet",
                sln="X.csproj", test_runner="dotnet", checkpoint_stages=False)
    base.update(extra)
    return ProjectConfig(**base)


def _git(repo: Path, *args: str) -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True, env=env)


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


class CheckpointApproverTest(unittest.TestCase):
    """Карточка 6.3: stage_approver=reviewer — этап принимает ревьюер."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prunner_appr_"))
        _git(self.tmp, "init", "-q")
        (self.tmp / "README.md").write_text("проект", encoding="utf-8")
        _git(self.tmp, "add", "-A")
        _git(self.tmp, "commit", "-q", "-m", "init")
        for p in Path(tempfile.gettempdir()).glob("semrev_1.1_*"):
            shutil.rmtree(p, ignore_errors=True)
        self.cfg = make_cfg(self.tmp, stage_approver="reviewer")
        self.plan_path = self.tmp / "plan_cp.md"
        self.plan_path.write_text(PLAN_CP, encoding="utf-8")

    def _worktree_count(self) -> int:
        out = subprocess.run(
            ["git", "-C", str(self.tmp), "worktree", "list", "--porcelain"],
            capture_output=True, text=True).stdout or ""
        return out.count("worktree ")

    def _decision_path(self) -> Path:
        from agents.checkpoint import cp_dir
        return cp_dir(self.cfg) / "1.1.decision.json"

    def test_reviewer_approves_stage(self):
        from agents import plan_runner as pr
        from agents.checkpoint import cp_dir
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True,
                          retries=0)
        calls = []
        prompts = []

        def dispatcher(cfg, tid, report, log, **kw):
            calls.append(tid)
            prompts.append(kw.get("prompt_override", ""))
            if tid.endswith("-review"):
                d = cp_dir(cfg)
                d.mkdir(parents=True, exist_ok=True)
                (d / "1.1.decision.json").write_text(
                    json.dumps({"decision": "approve"}), encoding="utf-8")
            else:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("# ОТЧЁТ\n## Что сделано\nработа есть\n"
                                  "## Доказательства\nтесты зелёные\n" + "pad" * 60,
                                  encoding="utf-8")
            return 0

        with mock.patch.object(pr, "run_subagent", dispatcher), \
             mock.patch.object(pr.PlanRunner, "_verify", return_value="PASS"):
            rc = r.run()
        self.assertEqual(rc, 0)
        self.assertTrue(any(t.endswith("-review") for t in calls),
                        "ревьюер запускается отдельной сессией")
        self.assertEqual(load_plan(self.plan_path).card("1.1").status, "done")
        # ревьюер работает во временном worktree: промпт указывает на wt_root,
        # а не на живой корень проекта
        rv_prompt = [p for t, p in zip(calls, prompts) if t.endswith("-review")]
        self.assertEqual(len(rv_prompt), 1, "ровно одна ревью-сессия")
        self.assertIn("git worktree", rv_prompt[0],
                      "промпт говорит о временном worktree")
        self.assertNotIn(f'"{self.tmp}"', rv_prompt[0].replace("git worktree", ""),
                         "ревьюер не работает на живом корне")
        self.assertIn("ТОЛЬКО в ней", rv_prompt[0],
                      "правки вне worktree запрещены промптом")
        self.assertEqual(self._worktree_count(), 1,
                         "временный worktree снят после решения")

    def test_git_worktree_refusal_is_soft_mode(self):
        """Мягкий режим: git worktree отказал (нет коммитов/не репозиторий) —
        предупреждение, этап ждёт владельца, карточка не блокируется."""
        from agents import plan_runner as pr
        tmp2 = Path(tempfile.mkdtemp(prefix="prunner_nowt_"))
        try:
            _git(tmp2, "init", "-q")  # репозиторий есть, но нет ни одного коммита
            cfg2 = make_cfg(tmp2, stage_approver="reviewer")
            plan_path = tmp2 / "plan_cp.md"
            plan_path.write_text(PLAN_CP, encoding="utf-8")
            r = pr.PlanRunner(cfg2, plan_path=plan_path, once=True, retries=0)
            calls = []

            def dispatcher(cfg, tid, report, log, **kw):
                calls.append(tid)
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("# ОТЧЁТ\n## Что сделано\nработа есть\n"
                                  "## Доказательства\nтесты зелёные\n" + "pad" * 60,
                                  encoding="utf-8")
                return 0

            with mock.patch.object(pr, "run_subagent", dispatcher), \
                 mock.patch.object(pr.PlanRunner, "_verify",
                                   return_value="PASS"), \
                 mock.patch.object(pr.PlanRunner, "_wait_decision",
                                   return_value="approve") as wd:
                rc = r.run()
            self.assertEqual(rc, 0, "отказ git worktree не блокирует карточку")
            self.assertFalse(any(t.endswith("-review") for t in calls),
                             "ревьюер не запускается без worktree")
            wd.assert_called_once()
            self.assertEqual(load_plan(plan_path).card("1.1").status, "done")
            self.assertFalse(list(Path(tempfile.gettempdir()).glob("semrev_1.1_*")),
                             "нет временных каталогов после мягкого пропуска")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

    def test_reviewer_failure_falls_back_to_owner_wait(self):
        """Мягкий режим: ревьюер не смог — этап ждёт владельца, не блокируется."""
        from agents import plan_runner as pr
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True,
                          retries=0)

        def dispatcher(cfg, tid, report, log, **kw):
            if tid.endswith("-review"):
                return 1
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("# ОТЧЁТ\n## Что сделано\nработа есть\n"
                              "## Доказательства\nтесты зелёные\n" + "pad" * 60,
                              encoding="utf-8")
            return 0

        with mock.patch.object(pr, "run_subagent", dispatcher), \
             mock.patch.object(pr.PlanRunner, "_verify", return_value="PASS"), \
             mock.patch.object(pr.PlanRunner, "_wait_decision",
                               return_value="approve") as wd:
            rc = r.run()
        self.assertEqual(rc, 0)
        wd.assert_called_once()


class SemanticReviewTest(unittest.TestCase):
    """Карточка 4.2: независимая reviewer-фаза ПОСЛЕ механического PASS
    (runner.semantic_review). Ревьюер-заглушка вместо opencode; реальный git
    worktree целевого проекта."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prunner_semrev_"))
        _git(self.tmp, "init", "-q")
        (self.tmp / "README.md").write_text("проект", encoding="utf-8")
        _git(self.tmp, "add", "-A")
        _git(self.tmp, "commit", "-q", "-m", "init")
        for p in Path(tempfile.gettempdir()).glob("semrev_1.1_*"):
            shutil.rmtree(p, ignore_errors=True)
        self.cfg = make_cfg(self.tmp, semantic_review=True)
        self.plan_path = self.tmp / "plan.md"
        self.plan_path.write_text(PLAN, encoding="utf-8")

    def _fixture_verdict(self) -> Path:
        """Механический вердикт (реальный cmd_verify замокан вместе с _verify)."""
        vd = self.cfg.abs_tasks_dir("reports") / "1.1_Вердикт_контролёра_fixture.md"
        vd.write_text("Вердикт: **PASS**\n| сборка | PASS |\n", encoding="utf-8")
        return vd

    @staticmethod
    def _executor_report(report: Path, tid: str) -> None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("# ОТЧЁТ: " + tid + "\n## Что сделано\nправки по карточке\n"
                          "## Доказательства\nтесты зелёные\n" + "pad" * 60 + "\n",
                          encoding="utf-8")

    def _worktree_count(self) -> int:
        out = subprocess.run(
            ["git", "-C", str(self.tmp), "worktree", "list", "--porcelain"],
            capture_output=True, text=True).stdout or ""
        return out.count("worktree ")

    def test_pass_path_appends_section_and_removes_worktree(self):
        from agents import plan_runner as pr
        self._fixture_verdict()
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=0)
        calls = []

        def dispatcher(cfg, tid, report, log, **kw):
            calls.append(tid)
            if tid.endswith("-semrev"):
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("# НЕЗАВИСИМОЕ РЕВЬЮ 1.1\n## Вердикт\n**PASS**\n"
                                  "## Goal alignment\nсоответствует\n"
                                  "## Правки вне задачи\nнет\n",
                                  encoding="utf-8")
                return 0
            self._executor_report(report, tid)
            return 0

        with mock.patch.object(pr, "run_subagent", dispatcher), \
             mock.patch.object(pr.PlanRunner, "_verify", return_value="PASS"):
            rc = r.run()

        self.assertEqual(rc, 0)
        self.assertEqual(load_plan(self.plan_path).card("1.1").status, "done")
        self.assertIn("1.1-semrev", calls, "после PASS запускается reviewer-сессия")
        # постановка ревьюера видима в Активные (run_subagent ищет файл задачи)
        self.assertTrue(list((self.tmp / "Tasks" / "Активные").glob("1.1-semrev_*.md")))
        # заключение дописано в Вердикт секцией «Независимое ревью»
        vtxt = (self.cfg.abs_tasks_dir("reports") /
                "1.1_Вердикт_контролёра_fixture.md").read_text(encoding="utf-8")
        tail = vtxt.split("Независимое ревью")[-1]
        self.assertIn("**PASS**", tail)
        self.assertIn("_Ревью_", tail)
        # worktree удалён после ревью
        self.assertEqual(self._worktree_count(), 1,
                         "остался только основной checkout")
        self.assertFalse(list(Path(tempfile.gettempdir()).glob("semrev_1.1_*")),
                         "временный каталог worktree удалён")

    def test_fail_review_triggers_retry_with_instructions_then_done(self):
        from agents import plan_runner as pr
        self._fixture_verdict()
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=1)
        calls, prompts = [], []
        state = {"rv": "FAIL"}

        def dispatcher(cfg, tid, report, log, **kw):
            calls.append(tid)
            prompts.append(kw.get("prompt_override", ""))
            report.parent.mkdir(parents=True, exist_ok=True)
            if tid.endswith("-semrev"):
                rv = state["rv"]
                state["rv"] = "PASS"
                report.write_text(f"# НЕЗАВИСИМОЕ РЕВЬЮ 1.1\n## Вердикт\n**{rv}**\n"
                                  "## Инструкции при retry\nисправить X\n",
                                  encoding="utf-8")
                return 0
            self._executor_report(report, tid)
            return 0

        with mock.patch.object(pr, "run_subagent", dispatcher), \
             mock.patch.object(pr.PlanRunner, "_verify",
                               side_effect=["PASS", "PASS"]):
            rc = r.run()

        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["1.1", "1.1-semrev", "1.1", "1.1-semrev"],
                         "FAIL ревьюера запускает штатный ретрай карточки")
        self.assertIn("исправить X", prompts[2],
                      "инструкции ревьюера попадают в хвост ошибки новой попытки")
        self.assertEqual(load_plan(self.plan_path).card("1.1").status, "done")
        vtxt = (self.cfg.abs_tasks_dir("reports") /
                "1.1_Вердикт_контролёра_fixture.md").read_text(encoding="utf-8")
        parts = vtxt.split("Независимое ревью")
        self.assertIn("**FAIL**", parts[1],
                      "первая попытка: FAIL ревьюера зафиксирован в Вердикте")
        self.assertIn("**PASS**", parts[-1])
        self.assertEqual(self._worktree_count(), 1)

    def test_reviewer_unavailable_is_soft_mode(self):
        from agents import plan_runner as pr
        self._fixture_verdict()
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=0)
        calls = []

        def dispatcher(cfg, tid, report, log, **kw):
            calls.append(tid)
            if tid.endswith("-semrev"):
                return 1  # сессия ревьюера упала, заключения нет
            self._executor_report(report, tid)
            return 0

        with mock.patch.object(pr, "run_subagent", dispatcher), \
             mock.patch.object(pr.PlanRunner, "_verify", return_value="PASS"):
            rc = r.run()

        self.assertEqual(rc, 0, "недоступность ревьюера не блокирует карточку")
        self.assertEqual(load_plan(self.plan_path).card("1.1").status, "done")
        vtxt = (self.cfg.abs_tasks_dir("reports") /
                "1.1_Вердикт_контролёра_fixture.md").read_text(encoding="utf-8")
        tail = vtxt.split("Независимое ревью")[-1]
        self.assertIn("ПРОПУЩЕНО", tail)
        self.assertNotIn("**PASS**", tail)
        self.assertEqual(self._worktree_count(), 1, "worktree снят даже при сбое")

    def test_disabled_by_default_no_review_session(self):
        from agents import plan_runner as pr
        cfg = make_cfg(self.tmp)  # semantic_review не задан -> False
        plan_path = self.tmp / "plan.md"
        r = pr.PlanRunner(cfg, plan_path=plan_path, once=True, retries=0)
        calls = []

        def dispatcher(cfg, tid, report, log, **kw):
            calls.append(tid)
            self._executor_report(report, tid)
            return 0

        with mock.patch.object(pr, "run_subagent", dispatcher), \
             mock.patch.object(pr.PlanRunner, "_verify", return_value="PASS"):
            rc = r.run()
        self.assertEqual(rc, 0)
        self.assertFalse(any(t.endswith("-semrev") for t in calls),
                         "флаг выключен — reviewer-фаза не запускается")

    def test_non_git_root_soft_skip_without_temp_litter(self):
        from agents import plan_runner as pr
        tmp2 = Path(tempfile.mkdtemp(prefix="prunner_nogit_"))
        try:
            cfg2 = make_cfg(tmp2, semantic_review=True)
            r = pr.PlanRunner(cfg2, plan_path=None)
            card = mock.Mock(id="1.1", title="x")
            res = r._semantic_review(card)
            self.assertEqual(res, "PASS", "нет git/коммитов — мягкий пропуск")
            self.assertFalse(list(Path(tempfile.gettempdir()).glob("semrev_1.1_*")))
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)


class StageTagAndInputTest(unittest.TestCase):
    """Карточка 2.3: stage_approver=reviewer в конфиге, полный вход ревьюера
    (список отчётов/вердиктов ВСЕХ листовых карточек этапа) и тегирование
    stage/N-done (создание, идемпотентность, порядок «коммит → тег»)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prunner_tag_"))
        _git(self.tmp, "init", "-q")
        (self.tmp / "README.md").write_text("проект", encoding="utf-8")
        _git(self.tmp, "add", "-A")
        _git(self.tmp, "commit", "-q", "-m", "init")
        self.cfg = make_cfg(self.tmp, stage_approver="reviewer")
        self.plan_path = self.tmp / "plan.md"
        self.plan_path.write_text(PLAN, encoding="utf-8")

    def test_tag_stage_done_creates_and_is_idempotent(self):
        """Тег создаётся; повторный вызов не падает и возвращает имя."""
        from agents import plan_runner as pr
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path)
        name1 = r._tag_stage_done("1")
        self.assertEqual(name1, "stage/1-done")
        tags = subprocess.run(["git", "-C", str(self.tmp), "tag", "-l", "stage/1-done"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(tags, "stage/1-done")
        # повторный вызов на существующем теге — безопасен, возвращает имя
        name2 = r._tag_stage_done("1")
        self.assertEqual(name2, "stage/1-done")

    def test_tag_after_plan_commit_anchors_final_state(self):
        """Порядок «коммит плана → тег»: якорь указывает на коммит, содержащий
        статус done (финальное состояние плана)."""
        from agents import plan_runner as pr
        from pipeline.plans import set_card_status
        # симулируем финал карточки: статус done в плане + коммит + тег
        set_card_status(self.plan_path, "1.1", "done")
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path)
        commit = r._git_commit(self.plan_path.parent, "plan/1.1: тест")
        r._tag_stage_done("1")
        tag_commit = subprocess.run(
            ["git", "-C", str(self.tmp), "rev-parse", "stage/1-done"],
            capture_output=True, text=True).stdout.strip()
        head = subprocess.run(
            ["git", "-C", str(self.tmp), "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()
        self.assertTrue(commit)
        self.assertEqual(tag_commit, head,
                         "тег ставится после коммита — якорь включает финальное состояние")
        # финальное состояние (статус done) реально в этом коммите
        blob = subprocess.run(
            ["git", "-C", str(self.tmp), "show", f"{head}:plan.md"],
            capture_output=True, text=True).stdout
        self.assertIn("`1.1` |", blob)  # таблица СДР
        self.assertIn("done", blob)

    def test_stage_review_files_lists_all_leaf_cards(self):
        """Список входных файлов ревьюера содержит отчёты и вердикты ВСЕХ
        листовых карточек этапа (glob <этап>.*_Отчёт_*.md / _Вердикт_*.md)."""
        from agents import plan_runner as pr
        reports = self.cfg.abs_tasks_dir("reports")
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "1.1_Отчёт_2026-08-24_101010.md").write_text("r", encoding="utf-8")
        (reports / "1.1_Вердикт_2026-08-24_101010.md").write_text("v", encoding="utf-8")
        (reports / "1.2_Отчёт_2026-08-24_101010.md").write_text("r", encoding="utf-8")
        (reports / "1.2_Вердикт_2026-08-24_101010.md").write_text("v", encoding="utf-8")
        (reports / "9.9_Отчёт_чужая_карточка.md").write_text("x", encoding="utf-8")
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path)
        card = mock.Mock(id="1.1", title="x")
        listing = r._stage_review_files(card)
        self.assertIn("1.1_Отчёт_2026-08-24_101010.md", listing)
        self.assertIn("1.1_Вердикт_2026-08-24_101010.md", listing)
        self.assertIn("1.2_Отчёт_2026-08-24_101010.md", listing)
        self.assertIn("1.2_Вердикт_2026-08-24_101010.md", listing)
        self.assertNotIn("9.9_Отчёт_чужая_карточка.md", listing,
                         "файлы вне этапа не попадают в вход ревьюера")

    def test_dry_run_shows_approver(self):
        """dry-run выводит строку про approver — критерий приёмки 2.3/1."""
        import io
        from contextlib import redirect_stdout
        from agents import plan_runner as pr
        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, dry_run=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = r.run()
        self.assertEqual(rc, 0)
        self.assertIn("approver этапа: reviewer", buf.getvalue())

    def test_reviewer_prompt_contains_full_stage_file_list(self):
        """Промпт ревьюера (временный worktree) содержит явный список файлов
        отчётов/вердиктов всех листовых карточек этапа."""
        from agents import plan_runner as pr
        from agents.checkpoint import cp_dir
        # карточка с «Чекпоинт: да» — иначе reviewer-фаза не запустится
        self.plan_path.write_text(PLAN_CP, encoding="utf-8")
        reports = self.cfg.abs_tasks_dir("reports")
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "1.1_Отчёт_2026-08-24_101010.md").write_text("r", encoding="utf-8")
        (reports / "1.2_Вердикт_2026-08-24_101010.md").write_text("v", encoding="utf-8")
        prompts = []
        calls = []

        def dispatcher(cfg, tid, report, log, **kw):
            calls.append(tid)
            prompts.append(kw.get("prompt_override", ""))
            if tid.endswith("-review"):
                d = cp_dir(cfg)
                d.mkdir(parents=True, exist_ok=True)
                (d / "1.1.decision.json").write_text(
                    json.dumps({"decision": "approve"}), encoding="utf-8")
            else:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("# ОТЧЁТ\n## Что сделано\nработа есть\n"
                                  "## Доказательства\nтесты зелёные\n" + "pad" * 60,
                                  encoding="utf-8")
            return 0

        r = pr.PlanRunner(self.cfg, plan_path=self.plan_path, once=True, retries=0)
        with mock.patch.object(pr, "run_subagent", dispatcher), \
             mock.patch.object(pr.PlanRunner, "_verify", return_value="PASS"):
            rc = r.run()
        self.assertEqual(rc, 0)
        rv = [p for t, p in zip(calls, prompts) if t.endswith("-review")]
        self.assertEqual(len(rv), 1)
        self.assertIn("1.1_Отчёт_2026-08-24_101010.md", rv[0])
        self.assertIn("1.2_Вердикт_2026-08-24_101010.md", rv[0])
        self.assertIn("ВСЕХ листовых карточек этапа", rv[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
