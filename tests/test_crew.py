# -*- coding: utf-8 -*-
"""Тесты crew-супервизора (pipeline/crew.py, карточка 6.2).

Запуск: python -X utf8 tests/test_crew.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import ProjectConfig  # noqa: E402


def make_cfg(tmp: Path, **kw) -> ProjectConfig:
    for sub in ("Tasks/Конвейер/handoff", ".opencode"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    base = dict(name="proj", root=tmp, msbuild="none", sln="",
                test_runner="none", checkpoint_stages=False,
                crew_roles=["executor"], crew_model="",
                crew_permissions="write", restart_max=2, restart_cooldown_sec=0)
    base.update(kw)
    return ProjectConfig(**base)


class FakeClient:
    def __init__(self, sessions):
        self.sessions = sessions
        self.created = []
        self.notified = []

    def list_sessions(self, project=""):
        return [s for s in self.sessions
                if not project or s.get("project") == project]

    def get_session(self, sid):
        return next((s for s in self.sessions if s["id"] == sid), None)

    def create_session(self, **kw):
        nid = "S-" + str(100 + len(self.created))
        s = {"id": nid, "status": "created", "note": "", "project": kw.get("project")}
        s.update(kw)
        self.created.append(s)
        self.sessions.append(s)
        return s

    def notify(self, type_, to="", task="", payload=None):
        self.notified.append((type_, task, dict(payload or {})))


class CrewConfigTest(unittest.TestCase):
    def test_load_crew_normalizes(self):
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_"))
        cfg = make_cfg(tmp)
        c = __import__("pipeline.crew", fromlist=["load_crew"]).load_crew(cfg)
        self.assertEqual(c["roles"], ["executor"])
        self.assertEqual(c["permissions"], "write")
        self.assertEqual(c["policy"], {"max_restarts": 2, "cooldown_sec": 0})

    def test_ensure_permissions_write_profile(self):
        from pipeline import crew
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_p_"))
        cfg = make_cfg(tmp)
        p = crew.ensure_permissions(cfg)
        self.assertIsNotNone(p)
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        self.assertEqual(data["permissions"]["edit"], "allow")
        self.assertIsNone(crew.ensure_permissions(cfg),
                          "существующий профиль не перезаписывается")


class PlanRestartsTest(unittest.TestCase):
    P = {"max_restarts": 2, "cooldown_sec": 300}

    def test_done_without_handoff_skipped(self):
        from pipeline.crew import plan_restarts
        s = [{"id": "S1", "status": "done", "note": "отчёт готов", "task": "A-1"}]
        self.assertEqual(plan_restarts(s, self.P, {}, now=1000.0), [])

    def test_running_skipped_failed_restarted(self):
        from pipeline.crew import plan_restarts
        s = [{"id": "S1", "status": "running", "note": "", "task": "A-1"},
             {"id": "S2", "status": "failed", "note": "", "task": "A-1"}]
        out = plan_restarts(s, self.P, {}, now=1000.0)
        self.assertEqual([d["action"] for d in out], ["restart"])
        self.assertEqual(out[0]["sid"], "S2")

    def test_handoff_note_restarts_with_path(self):
        from pipeline.crew import HANDOFF_MARK, plan_restarts
        s = [{"id": "S9", "status": "done",
              "note": HANDOFF_MARK + "Tasks/Конвейер/handoff/S9.md",
              "task": "A-2"}]
        out = plan_restarts(s, self.P, {}, now=1000.0)
        self.assertEqual(out[0]["action"], "restart")
        self.assertEqual(out[0]["handoff"], "Tasks/Конвейер/handoff/S9.md")

    def test_cooldown_then_exhausted(self):
        from pipeline.crew import plan_restarts
        s = [{"id": "S1", "status": "failed", "note": "", "task": "A-1"}]
        counters = {"S1": {"count": 1, "last_ts": 1000.0}}
        out = plan_restarts(s, self.P, counters, now=1100.0)
        self.assertEqual(out[0]["action"], "cooldown")
        out = plan_restarts(s, self.P, counters, now=2000.0)
        self.assertEqual(out[0]["action"], "restart")
        counters["S1"]["count"] = 2
        out = plan_restarts(s, self.P, counters, now=9999.0)
        self.assertEqual(out[0]["action"], "exhausted")


class SuperviseOnceTest(unittest.TestCase):
    def test_restart_spawns_and_counts(self):
        from pipeline.crew import supervise_once
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_s_"))
        cfg = make_cfg(tmp)
        spawned = []
        client = FakeClient([
            {"id": "S1", "project": "proj", "status": "failed", "note": "",
             "task": "A-1", "role": "executor", "model": "m1",
             "instruction": {"prompt": "P"}}])
        counters: dict = {}
        out = supervise_once(cfg, client, counters, now=1000.0,
                             spawn=lambda s: spawned.append(s))
        self.assertEqual(len(spawned), 1)
        self.assertEqual(counters["S1"]["count"], 1)
        self.assertTrue(out[0]["new_sid"].startswith("S-"))
        self.assertEqual(client.created[0]["instruction"]["prompt"], "P")

    def test_exhausted_notifies_crew_exhausted(self):
        from pipeline.crew import supervise_once
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_e_"))
        cfg = make_cfg(tmp)
        client = FakeClient([
            {"id": "S1", "project": "proj", "status": "failed", "note": "",
             "task": "A-1", "role": "executor", "model": "", "instruction": {}}])
        counters = {"S1": {"count": 2, "last_ts": 0.0}}
        out = supervise_once(cfg, client, counters, now=1000.0,
                             spawn=lambda s: None)
        self.assertEqual(out[0]["action"], "exhausted")
        self.assertIn(("crew_exhausted", "A-1",
                       {"session_id": "S1", "reason": "max_restarts"}),
                      client.notified)


class WorkerHandoffTest(unittest.TestCase):
    def test_write_handoff_sections(self):
        from agents.session_worker import write_handoff
        tmp = Path(tempfile.mkdtemp(prefix="pcrew_w_"))
        p = write_handoff(str(tmp), "S-77", task_id="U1.2",
                          report="", rc=1, error_tail="boom")
        self.assertIsNotNone(p)
        txt = Path(p).read_text(encoding="utf-8")
        for sec in ("## Репозитории и коммиты", "## Контекст", "## ГОТОВО",
                    "## ЗАДАЧА", "## Цикл работы", "## Грабли"):
            self.assertIn(sec, txt)
        self.assertIn("U1.2", txt)
        self.assertIn("boom", txt)


class TuiCycleTest(unittest.TestCase):
    """Карточка 2.1: автономный терминал — цикл порций, каждая следующая
    сессия получает свежий контекст + handoff предыдущей (/new-эквивалент)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pcrew_tui_"))
        self.cfg = make_cfg(self.tmp)

    @staticmethod
    def _fake_runner(handoff_markers, prompts, rc=0):
        state = {"n": 0}

        def _run(cfg, prompt):
            state["n"] += 1
            prompts.append(prompt)
            if len(handoff_markers) >= state["n"]:
                d = cfg.conveyor_dir() / "handoff"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"S-{state['n']}.md").write_text(
                    f"ПОРЦИЯ-{handoff_markers[state['n'] - 1]}", encoding="utf-8")
            time.sleep(0.01)   # mtime handoff строго больше старта итерации
            return rc
        return _run

    def test_cycle_feeds_handforward_and_stops_on_limit(self):
        from agents import tui_cycle
        prompts = []
        # порции 1 и 2 пишут handoff, порция 3 — нет; лимит restart_max+1 = 3
        runner = self._fake_runner(["один", "два"], prompts)
        n = tui_cycle.run_cycle(self.cfg, role="executor", user_prompt="ДЕЛО-X",
                                runner=runner)
        self.assertEqual(n, 3, f"лимит {self.cfg.restart_max + 1} порций")
        self.assertEqual(len(prompts), 3)
        self.assertIn("ДЕЛО-X", prompts[0])
        self.assertIn("ПОРЦИЯ-один", prompts[1],
                      "вторая сессия получает handoff первой")
        self.assertIn("ПОРЦИЯ-два", prompts[2],
                      "третья сессия получает handoff второй")

    def test_cycle_stops_after_first_portion_without_handoff(self):
        from agents import tui_cycle
        prompts = []
        runner = self._fake_runner([], prompts)
        n = tui_cycle.run_cycle(self.cfg, role="executor", runner=runner)
        self.assertEqual(n, 1, "нет handoff — агент решил, что продолжать нечего")

    def test_nonzero_rc_ends_cycle(self):
        from agents import tui_cycle
        prompts = []
        runner = self._fake_runner(["раз"], prompts, rc=3)
        n = tui_cycle.run_cycle(self.cfg, runner=runner)
        self.assertEqual(n, 1, "падение сессии завершает цикл")

    def test_base_prompt_contains_role_and_user_text(self):
        from agents import tui_cycle
        p = tui_cycle.build_base_prompt(self.cfg, "executor", "почини фильтр")
        self.assertIn("ИСПОЛНИТЕЛ", p)
        self.assertIn("proj", p)
        self.assertIn("почини фильтр", p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
