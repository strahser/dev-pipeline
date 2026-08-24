# -*- coding: utf-8 -*-
"""Тесты роли «ОБЩИЙ менеджер» (карточка 4.1).

Проверяют:
1. ROLE_STARTERS["manager"] существует и описывает приёмку чекпоинта
   (decision.json approve/retry, actor: manager, границы «код не правишь»).
2. Manager-заглушка (фейковый клиент + pending-чекпоинт) пишет валидный
   <CARD>.decision.json с actor: manager, который wait_decision разбирает
   как approve/retry.
3. Автозадание менеджера (auto_task_manager) собирает сводку без сервера.

Запуск: python -X utf8 tests/test_manager.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import ProjectConfig  # noqa: E402


def make_cfg(tmp: Path) -> ProjectConfig:
    for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Конвейер"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    return ProjectConfig(name="proj", root=tmp, msbuild="none", sln="",
                         test_runner="none", checkpoint_stages=False,
                         checkpoint_remind_sec=600)


class ManagerRoleTest(unittest.TestCase):
    """Критерий 1: роль manager есть в ROLE_STARTERS и описывает приёмку."""

    def test_role_starter_exists_and_describes_acceptance(self):
        from agents.tui_cycle import ROLE_STARTERS, _starter
        self.assertIn("manager", ROLE_STARTERS, "роль manager должна быть в ROLE_STARTERS")
        text = _starter("manager")
        self.assertIn("/api/pulse_all", text)
        self.assertIn("decision.json", text)
        self.assertIn("approve", text)
        self.assertIn("retry", text)
        self.assertIn("actor: manager", text)
        self.assertIn("код НЕ правишь", text,
                      "граница «код не правишь» должна быть в стартовом промпте")

    def test_auto_task_manager_without_server(self):
        """Критерий 3 (частично): сводка менеджера не падает без сервера."""
        from agents.tui_cycle import auto_task_manager
        text = auto_task_manager()
        self.assertIsInstance(text, str)
        self.assertIn("ТЕКУЩЕЕ СОСТОЯНИЕ ПРОЕКТОВ", text)
        self.assertIn("ЗАДАНИЕ", text)


class ManagerStubAcceptanceTest(unittest.TestCase):
    """Критерий 4: manager-заглушка (фейковый клиент + pending-чекпоинт)
    пишет валидный decision.json, который wait_decision разбирает как approve/retry."""

    class FakeClock:
        def __init__(self, start: float = 1000.0):
            self.t = start

        def time(self) -> float:
            return self.t

        def sleep(self, sec: float) -> None:
            self.t += sec

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pman_"))
        self.cfg = make_cfg(self.tmp)
        self.events = []

    def _notify(self, ev, task="", payload=None):
        self.events.append((ev, task, dict(payload or {})))

    def _manager_stub(self, card: str, decision: str, comment: str = ""):
        """Заглушка менеджера: объявляет pending и пишет решение (actor: manager)."""
        from agents import checkpoint as cp
        cp.create_pending(self.cfg, card, reason="ждёт приёмки", title="T",
                          notify=self._notify)
        d = cp.cp_dir(self.cfg)
        (d / f"{card}.decision.json").write_text(
            json.dumps({"decision": decision, "comment": comment,
                        "actor": "manager"}),
            encoding="utf-8")
        return d

    def test_manager_approve_parsed(self):
        from agents import checkpoint as cp
        self._manager_stub("E1", "approve", "этап принят")
        action = cp.wait_decision(self.cfg, "E1", poll_sec=15,
                                  clock=self.FakeClock(), notify=self._notify)
        self.assertEqual(action, "approve")
        ev = [e for e in self.events if e[0] == "checkpoint_decided"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0][2].get("actor"), "manager",
                         "актор решения — manager")

    def test_manager_retry_parsed(self):
        from agents import checkpoint as cp
        self._manager_stub("E2", "retry", "недоработка в отчёте")
        action = cp.wait_decision(self.cfg, "E2", poll_sec=15,
                                  clock=self.FakeClock(), notify=self._notify)
        self.assertEqual(action, "retry")

    def test_manager_stub_writes_valid_json(self):
        """Файл решения от заглушки — валидный JSON с полями decision/actor."""
        from agents import checkpoint as cp
        d = self._manager_stub("E3", "approve")
        data = json.loads((d / "E3.decision.json").read_text(encoding="utf-8"))
        self.assertEqual(data["decision"], "approve")
        self.assertEqual(data["actor"], "manager")
        self.assertIn("comment", data)

    def test_pending_visible_and_decision_consumed(self):
        """До решения pending.json виден (источник панели ⏸ Чекпоинты);
        решение (decision.json) потребляется однократно (файл удаляется)."""
        from agents import checkpoint as cp
        self._manager_stub("E4", "approve")
        d = cp.cp_dir(self.cfg)
        p = d / "E4.pending.json"
        dec = d / "E4.decision.json"
        self.assertTrue(p.exists(), "pending.json виден до приёмки")
        self.assertTrue(dec.exists(), "decision.json от менеджера написан")
        action = cp.wait_decision(self.cfg, "E4", poll_sec=15,
                                  clock=self.FakeClock(), notify=self._notify)
        self.assertEqual(action, "approve")
        self.assertFalse(dec.exists(), "decision.json потреблён однократно")
        self.assertEqual([e[0] for e in self.events if e[0] == "checkpoint_pending"],
                         ["checkpoint_pending"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
