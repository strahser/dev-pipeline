# -*- coding: utf-8 -*-
"""Запуск всех тестов dev-pipeline.

Использование: python -X utf8 tests/run_all.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODULES = [
    "test_framework",
    "test_cli_smoke",
    "test_server",
    "test_client",
    "test_agents",
    "test_qwen_bridge",
    "test_session_e2e",
    "test_plans",
    "test_plan_runner",
    "test_checkpoint",
    "test_crew",
]


def main() -> int:
    suite = unittest.TestSuite()
    for m in MODULES:
        try:
            mod = __import__(m)
            suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(mod))
        except Exception as e:
            print(f"[run_all] НЕ УДАЛОСЬ загрузить {m}: {e}")
            return 1
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
