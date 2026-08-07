# -*- coding: utf-8 -*-
"""Загрузка конфигурации проекта (pipeline.yaml) для dev-pipeline.

Схема конфига (см. examples\\heatlossrevit2\\pipeline.yaml):
    project: { name, root, branch }
    tasks:   { inbox, active, reports, archive, protocol, status, conveyor }
    build:   { msbuild, sln, configuration, platform, extra_args }
    tests:   { runner, vstest, dll, baseline_passed, baseline_total }
    checks:  список проверок (см. pipeline.checks)
    layer_rules: список grep-правил слоёв
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


class ConfigError(Exception):
    pass


@dataclass
class ProjectConfig:
    name: str
    root: Path
    branch: str = ""

    # Папки Tasks (относительно root)
    inbox: str = "Tasks\\Входящие"
    active: str = "Tasks\\Активные"
    reports: str = "Tasks\\Отчёты"
    archive: str = "Tasks\\Архив"
    protocol: str = "Tasks\\00_Протокол_агентов.md"
    status: str = "Tasks\\Статус_конвейера.md"
    conveyor: str = "Tasks\\Конвейер"
    notif: str = "Tasks\\Конвейер\\Уведомления"

    # Сборка
    msbuild: str = ""
    sln: str = ""
    configuration: str = "Debug"
    platform: str = "Any CPU"
    build_extra: list = field(default_factory=list)

    # Тесты
    test_runner: str = "vstest"
    vstest: str = ""
    test_dll: str = ""
    baseline_passed: int | None = None
    baseline_total: int | None = None
    # Известные падения тестов (подстроки имён) — не блокируют verify
    known_failures: list = field(default_factory=list)

    # Проверки и правила слоёв (декларативно)
    checks: list = field(default_factory=list)
    layer_rules: list = field(default_factory=list)
    audit_dirs: list = field(default_factory=lambda: ["Test", "Core.Tests"])

    # TDL (JSON как источник истины)
    tdl_enabled: bool = True
    tdl_root: str = "Tasks\\JSON"
    tdl_active: str = "Tasks\\JSON\\Active"
    tdl_reports: str = "Tasks\\JSON\\Reports"
    tdl_verdicts: str = "Tasks\\JSON\\Verdicts"
    tdl_index: str = "Tasks\\JSON\\Index\\tdl.index.json"
    tdl_markdown_mirror: bool = True

    # Служебные настройки
    skip_dirs: list = field(default_factory=lambda: [
        "bin", "obj", ".git", ".idea", ".opencode", "packages",
        "TestResults", "__pycache__", "Архив",
    ])

    @property
    def path(self, key: str) -> Path:
        """Абсолютный путь к папке/файлу Tasks."""
        return self.root / getattr(self, key)

    def resolve(self, rel: str) -> Path:
        return self.root / rel

    def abs_tasks_dir(self, folder_key: str) -> Path:
        return self.resolve(getattr(self, folder_key))

    # Короткие помощники для команд сборки/тестов
    def msbuild_cmd(self) -> list[str]:
        if self.msbuild.lower() == "dotnet":
            args = ["dotnet", "build", str(self.root / self.sln)]
            args += self.build_extra
            return args
        args = [self.msbuild, str(self.root / self.sln), "/t:Restore,Build",
                f"/p:Configuration={self.configuration}",
                f"/p:Platform={self.platform}"]
        args += self.build_extra
        return args

    def test_cmd(self) -> list[str]:
        if self.test_runner == "vstest":
            return [self.vstest, str(self.root / self.test_dll)]
        if self.test_runner == "dotnet":
            args = ["dotnet", "test", str(self.root / self.test_dll)]
            args += self.build_extra
            return args
        raise ConfigError(f"Неизвестный test_runner: {self.test_runner}")


def _require(d: dict, key: str, where: str):
    if key not in d:
        raise ConfigError(f"В pipeline.yaml отсутствует секция/поле '{key}' ({where})")
    return d[key]


def _root_candidates(project_name: str, raw_project: dict) -> list[Path]:
    """Список кандидатов корня проекта (первый существующий будет выбран).

    Порядок приоритета:
      1. Переменная окружения DEV_PIPELINE_PROJECTS_DIR (базовая папка проектов на ПК);
      2. project.root из YAML — строка ИЛИ список путей (несколько рабочих мест);
      3. project.roots из YAML (синоним списка).
    """
    candidates: list[Path] = []

    env_dir = os.environ.get("DEV_PIPELINE_PROJECTS_DIR")
    if env_dir:
        candidates.append(Path(env_dir) / project_name)

    raw_root = raw_project.get("root")
    if isinstance(raw_root, str):
        candidates.append(Path(raw_root))
    elif isinstance(raw_root, list):
        candidates.extend(Path(r) for r in raw_root)

    for r in raw_project.get("roots", []) or []:
        candidates.append(Path(r))

    return [c for c in candidates if str(c)]


def load_config(project_name: str) -> ProjectConfig:
    """Загрузить examples/<project>/pipeline.yaml."""
    cfg_path = EXAMPLES_DIR / project_name / "pipeline.yaml"
    if not cfg_path.exists():
        raise ConfigError(
            f"Конфиг не найден: {cfg_path}. "
            f"Доступные проекты: {[p.name for p in EXAMPLES_DIR.iterdir() if p.is_dir()]}")
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    p = _require(raw, "project", "project")
    t = raw.get("tasks", {})
    b = raw.get("build", {})
    te = raw.get("tests", {})
    c = raw.get("checks", [])
    lr = raw.get("layer_rules", [])
    ad = raw.get("audit_dirs", ["Test", "Core.Tests"])
    td = raw.get("tdl", {})

    root = None
    tried: list[str] = []
    for cand in _root_candidates(project_name, p):
        tried.append(str(cand))
        if cand.exists():
            root = cand.resolve()
            break
    if root is None:
        raise ConfigError(
            f"project.root не существует ни в одном из путей ({len(tried)}):\n"
            + "\n".join(f"  - {pth}" for pth in tried)
            + "\nЗадайте DEV_PIPELINE_PROJECTS_DIR (базовая папка проектов) или "
              "укажите существующий путь в project.root/roots.")

    return ProjectConfig(
        name=project_name,
        root=root,
        branch=p.get("branch", ""),
        inbox=t.get("inbox", "Tasks\\Входящие"),
        active=t.get("active", "Tasks\\Активные"),
        reports=t.get("reports", "Tasks\\Отчёты"),
        archive=t.get("archive", "Tasks\\Архив"),
        protocol=t.get("protocol", "Tasks\\00_Протокол_агентов.md"),
        status=t.get("status", "Tasks\\Статус_конвейера.md"),
        conveyor=t.get("conveyor", "Tasks\\Конвейер"),
        notif=t.get("notif", "Tasks\\Конвейер\\Уведомления"),
        msbuild=_require(b, "msbuild", "build"),
        sln=_require(b, "sln", "build"),
        configuration=b.get("configuration", "Debug"),
        platform=b.get("platform", "Any CPU"),
        build_extra=b.get("extra_args", []),
        test_runner=te.get("runner", "vstest"),
        vstest=te.get("vstest", ""),
        test_dll=te.get("dll", ""),
        baseline_passed=te.get("baseline_passed"),
        baseline_total=te.get("baseline_total"),
        known_failures=[str(x) for x in (te.get("known_failures") or [])],
        checks=c,
        layer_rules=lr,
        audit_dirs=ad,
        tdl_enabled=td.get("enabled", True),
        tdl_root=td.get("root", "Tasks\\JSON"),
        tdl_active=td.get("active", "Tasks\\JSON\\Active"),
        tdl_reports=td.get("reports", "Tasks\\JSON\\Reports"),
        tdl_verdicts=td.get("verdicts", "Tasks\\JSON\\Verdicts"),
        tdl_index=td.get("index", "Tasks\\JSON\\Index\\tdl.index.json"),
        tdl_markdown_mirror=td.get("markdown_mirror", True),
    )


def list_projects() -> list[str]:
    if not EXAMPLES_DIR.exists():
        return []
    return sorted(p.name for p in EXAMPLES_DIR.iterdir()
                  if p.is_dir() and (p / "pipeline.yaml").exists())


def check_env() -> None:
    """Проверить существование путей сборки/тестов из всех конфигов (для diagnostics)."""
    for name in list_projects():
        try:
            cfg = load_config(name)
        except ConfigError as e:
            print(f"  {name}: ОШИБКА {e}")
            continue
        problems = []
        if cfg.msbuild.lower() != "dotnet" and cfg.msbuild and not os.path.exists(cfg.msbuild):
            problems.append(f"msbuild не найден: {cfg.msbuild}")
        if cfg.vstest and not os.path.exists(cfg.vstest):
            problems.append(f"vstest не найден: {cfg.vstest}")
        if cfg.sln and not (cfg.root / cfg.sln).exists():
            problems.append(f"sln/проект не найден: {cfg.root / cfg.sln}")
        if problems:
            print(f"  {name}: " + "; ".join(problems))
        else:
            print(f"  {name}: ок")
