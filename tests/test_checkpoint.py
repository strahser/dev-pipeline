# -*- coding: utf-8 -*-
"""Тесты контракта пауз (agents/checkpoint.py) и orphan-детектора watchdog.

Запуск: python -X utf8 tests/test_checkpoint.py -v
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


def make_cfg(tmp: Path, remind_sec: int = 600) -> ProjectConfig:
    for sub in ("Tasks/Активные", "Tasks/Отчёты", "Tasks/Конвейер"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    return ProjectConfig(name="proj", root=tmp, msbuild="none", sln="",
                         test_runner="none", checkpoint_stages=False,
                         checkpoint_remind_sec=remind_sec)


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def time(self) -> float:
        return self.t

    def sleep(self, sec: float) -> None:
        self.t += sec


class CheckpointHelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pcp_"))
        self.cfg = make_cfg(self.tmp)
        self.events = []

    def _notify(self, ev, task="", payload=None):
        self.events.append((ev, task, dict(payload or {})))

    def test_create_pending_visible_by_convention(self):
        """Карточка 6.1: create пишет pending.json — источник панели ❓/⏸."""
        from agents import checkpoint as cp
        p = cp.create_pending(self.cfg, "U1.2", reason="ждёт решения",
                              title="Т", notify=self._notify)
        self.assertTrue(p.exists())
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data["card"], "U1.2")
        self.assertEqual(
            [e for e in self.events if e[0] == "checkpoint_pending"],
            [("checkpoint_pending", "U1.2",
              {"reason": "ждёт решения", "checkpoint": "U1.2"})])

    def test_take_decision_parses_and_consumes(self):
        from agents import checkpoint as cp
        d = cp.cp_dir(self.cfg)
        d.mkdir(parents=True, exist_ok=True)
        (d / "U1.2.decision.json").write_text(
            json.dumps({"decision": "retry", "comment": "переделай"}),
            encoding="utf-8")
        got = cp.take_decision(self.cfg, "U1.2")
        self.assertEqual(got, ("retry", "переделай"))
        self.assertFalse((d / "U1.2.decision.json").exists(),
                         "решение потребляется однократно")

    def test_wait_returns_none_on_timeout(self):
        from agents import checkpoint as cp
        res = cp.wait_decision(self.cfg, "X", poll_sec=15, timeout=100,
                               clock=FakeClock(), notify=self._notify)
        self.assertIsNone(res, "таймаут — решения нет, автопродолжения нет")

    def test_handoff_emits_event_without_client(self):
        """Карточка 6.1: передача карточки адресату видна в ленте даже
        без сервера; владелец не курьер."""
        from agents import checkpoint as cp
        ok = cp.handoff(self.cfg, "TIT-9", to="main-agent",
                        text="оформил TIT-9: симптом в плане",
                        client=None, notify=self._notify)
        self.assertFalse(ok, "без сервера доставка сообщения не состоялась")
        self.assertEqual([(e[0], e[1]) for e in self.events],
                         [("task_handoff", "TIT-9")])


class OrphanDetectorTest(unittest.TestCase):
    """Карточка 6.1: phase=checkpoint без pending-файла дольше 2x напоминания."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="porphan_"))
        self.cfg = make_cfg(self.tmp)
        self.now = time.time()

    def _state(self, updated_iso: str):
        cd = self.cfg.conveyor_dir() / "checkpoints"
        cd.mkdir(parents=True, exist_ok=True)
        (self.cfg.conveyor_dir() / "runner_state.json").write_text(
            json.dumps({"phase": "checkpoint", "updated": updated_iso,
                        "card": "U1.2"}),
            encoding="utf-8")
        return cd

    def _find(self):
        from server.heartbeat import find_checkpoint_orphans
        return find_checkpoint_orphans(lambda name: self.cfg, ["proj"], now=self.now)

    def test_orphan_detected_when_pending_missing(self):
        import datetime
        old = datetime.datetime.fromtimestamp(
            self.now - 60 * 60).isoformat(timespec="seconds")
        self._state(old)
        found = self._find()
        self.assertEqual([f[0] for f in found], ["proj"])
        self.assertGreaterEqual(found[0][2], 1200,
                                "порог = 2x checkpoint_remind_sec")

    def test_fresh_or_with_pending_not_orphan(self):
        import datetime
        fresh = datetime.datetime.fromtimestamp(
            self.now - 30).isoformat(timespec="seconds")
        old = datetime.datetime.fromtimestamp(
            self.now - 60 * 60).isoformat(timespec="seconds")
        cd = self._state(fresh)
        self.assertEqual(self._find(), [], "свежее ожидание — не сирота")
        self._state(old)
        (cd / "U1.2.pending.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self._find(), [],
                         "старый state + живой pending = легитимное ожидание")
        (cd / "U1.2.pending.json").unlink()
        found = self._find()
        self.assertEqual([f[0] for f in found], ["proj"],
                         "pending пропал при старом ожидании — сирота")


if __name__ == "__main__":
    unittest.main(verbosity=2)
