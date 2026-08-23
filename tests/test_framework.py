# -*- coding: utf-8 -*-
"""Самопроверка фреймворка (без msbuild/vstest): config, models, templates, checks.

Запуск: python -m unittest tests/test_framework.py -v   (или python tests/test_framework.py)
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import checks, templates            # noqa: E402
from pipeline.config import list_projects, load_config  # noqa: E402
from pipeline.models import Task, parse_tests_vstest, failed_test_names_vstest   # noqa: E402


class TestConfig(unittest.TestCase):
    def test_heatloss_config_loads(self):
        cfg = load_config("heatlossrevit2")
        self.assertTrue(cfg.root.exists())
        self.assertTrue(cfg.msbuild)
        self.assertTrue(cfg.sln == "HeatLossRevit.sln")
        self.assertTrue(cfg.checks)

    def test_list_projects(self):
        self.assertIn("heatlossrevit2", list_projects())


class TestSmartDecode(unittest.TestCase):
    """Карточка 3.2: вывод дочерних процессов без кракозябр (utf-8/OEM/ANSI)."""

    def test_cp866_cyrillic(self):
        from pipeline.proc import smart_decode
        raw = "Заголовок отчёта: rc=1; ошибка сборки".encode("cp866")
        out = smart_decode(raw)
        self.assertIn("Заголовок", out)
        self.assertIn("ошибка сборки", out)
        self.assertNotIn("\ufffd", out)

    def test_valid_utf8_untouched(self):
        from pipeline.proc import smart_decode
        raw = "привёт ✅ utf-8".encode("utf-8")
        self.assertEqual(smart_decode(raw), "привёт ✅ utf-8")

    def test_cp1251_fallback(self):
        from pipeline.proc import smart_decode
        raw = "Ошибка пути D:\\Проекты".encode("cp1251")
        self.assertIn("Ошибка", smart_decode(raw))

    def test_garbage_no_exception(self):
        from pipeline.proc import smart_decode
        out = smart_decode(b"\xff\xfe\x00\x81")
        self.assertIsInstance(out, str)

    def test_empty_and_str_passthrough(self):
        from pipeline.proc import smart_decode
        self.assertEqual(smart_decode(b""), "")
        self.assertEqual(smart_decode("уже строка"), "уже строка")


class TestModels(unittest.TestCase):
    def test_frontmatter(self):
        text = "---\nid: A-99\nстатус: open\nприоритет: высокий\n---\n# Т\nТело"
        meta = Task.parse_frontmatter(text)
        self.assertEqual(meta["id"], "A-99")
        self.assertEqual(meta["статус"], "open")

    def test_set_status(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "A-99_x.md"
            p.write_text("---\nid: A-99\nстатус: open\n---\n", encoding="utf-8")
            t = Task.from_file(p)
            self.assertEqual(t.status, "open")
            t.set_status("in_progress")
            self.assertIn("статус: in_progress", p.read_text(encoding="utf-8"))


class TestParseTests(unittest.TestCase):
    def test_vstest_ok(self):
        out = ("Прогнано тестов: 109\nПройдено: 109\nНе пройдено: 0\n"
               "Total tests: 109\nPassed: 109\nFailed: 0")
        self.assertEqual(parse_tests_vstest(out), (109, 109, 0))

    def test_vstest_fail(self):
        out = "Total tests: 109\nPassed: 106\nFailed: 3"
        self.assertEqual(parse_tests_vstest(out), (106, 109, 3))

    def test_failed_test_names_vstest(self):
        out = ("  Не пройден Tests.DependencyInjection.DependencyInjectionSmokeTests."
               "AddAppServices_AllRegistrationsResolvable [509 ms]\n"
               "  Пройден Tests.Infiltration.InfiltrationVmFilterServiceTests."
               "NormalizeLevel_LevelPresentInList_ReturnsSameLevel [1 ms]\n"
               "Всего тестов: 120\nПройдено: 119\nНе пройдено: 1")
        names = failed_test_names_vstest(out)
        self.assertEqual(len(names), 1)
        self.assertIn("AddAppServices_AllRegistrationsResolvable", names[0])

    def test_failed_test_names_vstest_english(self):
        out = ("Failed Tests.Foo.Bar_Test [5 ms]\n"
               "Total tests: 2\nPassed: 1\nFailed: 1")
        self.assertEqual(failed_test_names_vstest(out), ["Tests.Foo.Bar_Test"])


class TestTemplates(unittest.TestCase):
    def test_task_content(self):
        t = templates.task_content("A-99", "Тест", "высокий", "src.txt", "r1", "Контекст")
        self.assertIn("id: A-99", t)
        self.assertIn("статус: open", t)
        self.assertIn("Контекст", t)


class _Fixture(unittest.TestCase):
    """Фикстура: временный проект с fake .cs-файлами и pipeline.yaml."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="pipeline_test_"))
        (cls.tmp / "Core").mkdir()
        (cls.tmp / "Core").joinpath("GsopCalculator.cs").write_text(
            "namespace Core { class GsopCalculator { } }", encoding="utf-8")
        (cls.tmp / "RevitServices").mkdir()
        (cls.tmp / "RevitServices").joinpath("Bad.cs").write_text(
            "using MainAppHeatLoss;", encoding="utf-8")
        (cls.tmp / "MainApp.Projects").mkdir()
        (cls.tmp / "MainApp.Projects").joinpath("MainApp.Projects.csproj").write_text(
            "<Project><ItemGroup><ProjectReference Include=\"..\\MainApp\\MainApp.csproj\" /></ItemGroup></Project>",
            encoding="utf-8")
        (cls.tmp / "Tasks").mkdir()
        cfg_dir = Path(__file__).resolve().parent.parent / "examples" / "_test"
        cfg_dir.mkdir(exist_ok=True)
        cfg_dir.joinpath("pipeline.yaml").write_text(f"""
project:
  name: _test
  root: {cls.tmp.as_posix()}
tasks:
  active: Tasks\\Активные
  reports: Tasks\\Отчёты
  archive: Tasks\\Архив
build:
  msbuild: ""
  sln: ""
tests:
  runner: vstest
checks:
  - id: A-01
    label: GsopCalculator только в Core
    kind: class_location
    class: GsopCalculator
    dirs: [Core]
  - id: A-01
    label: Projects не ссылается на MainApp (цикл)
    kind: csproj_no_ref
    dir: MainApp.Projects
    forbidden: MainApp.csproj
layer_rules:
  - label: RevitServices без MainApp
    pattern: using\\s+MainAppHeatLoss
    expect: 0
    dirs: [RevitServices]
""".strip(), encoding="utf-8")
        cls.cfg = load_config("_test")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        shutil.rmtree(Path(__file__).resolve().parent.parent / "examples" / "_test", ignore_errors=True)


class TestChecks(_Fixture):
    def test_class_location_ok(self):
        rows = checks.verify_checks(self.cfg, "A-01")
        labels = {l: s for l, s in rows}
        self.assertTrue(labels["GsopCalculator только в Core"].startswith("OK"))

    def test_csproj_no_ref_fail(self):
        rows = checks.verify_checks(self.cfg, "A-01")
        labels = {l: s for l, s in rows}
        self.assertTrue(labels["Projects не ссылается на MainApp (цикл)"].startswith("FAIL"))

    def test_layer_rule_fail(self):
        rows = checks.layer_rule_rows(self.cfg)
        labels = {l: s for l, s in rows}
        self.assertTrue(labels["RevitServices без MainApp"].startswith("FAIL"))

    def test_build_grep_skip(self):
        c = {"label": "x", "kind": "build_grep", "pattern": "ERR"}
        ok, detail = checks._kind_build_grep(self.cfg, c, "")
        self.assertIn("SKIP", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
