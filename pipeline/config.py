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
    test_filter: str = ""            # dotnet test --filter (точечный/быстрый набор)
    baseline_passed: int | None = None
    baseline_total: int | None = None
    # Известные падения тестов (подстроки имён) — не блокируют verify
    known_failures: list = field(default_factory=list)

    # Проверки и правила слоёв (декларативно)
    checks: list = field(default_factory=list)
    layer_rules: list = field(default_factory=list)
    audit_dirs: list = field(default_factory=lambda: ["Test", "Core.Tests"])

    # План (репозиторий ProjectsPalns): источник карточек для plan_runner
    plan_repo: list = field(default_factory=list)   # кандидаты корня ProjectsPalns
    plan_subdir: str = ""                            # <Проект> внутри ProjectsPalns
    plan_file: str = ""                              # конкретный файл (иначе — новейший _current/*.md)

    # План-раннер
    runner_model: str = "opencode-go/deepseek-v4-flash"
    runner_parallel: int = 1
    runner_retries: int = 2                          # ретраев карточки с логом ошибки
    question_timeout_sec: int = 1200                 # тишина по вопросу -> работа по допущениям
    checkpoint_stages: bool = True                   # пауза после закрытия этапа (summary)

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

    def questions_dir(self) -> Path:
        """Папка вопросов агентов (grill-фаза): Tasks\\Вопросы."""
        return self.root / "Tasks" / "Вопросы"

    def conveyor_dir(self) -> Path:
        return self.root / "Tasks" / "Конвейер"

    def plan_dir(self):
        """Каталог планов проекта в ProjectsPalns: <repo>\\<subdir|name>\\_current.
        Возвращает Path или None, если репозиторий планов не найден."""
        import os
        name = self.plan_subdir or self.name
        candidates = []
        env = os.environ.get("DEV_PIPELINE_PLANS_DIR")
        if env:
            candidates.append(Path(env) / name / "_current")
            candidates.append(Path(env) / name)
        for r in self.plan_repo:
            candidates.append(Path(r) / name / "_current")
            candidates.append(Path(r) / name)
        for cand in candidates:
            if cand.is_dir():
                return cand
        return None

    def find_plan_file(self):
        """Актуальный файл плана: явный plan.file или новейший _current/*.md."""
        d = self.plan_dir()
        if d is None:
            return None
        if self.plan_file:
            p = d / self.plan_file
            return p if p.exists() else None
        md = sorted(d.glob("*.md"), key=lambda p: -p.stat().st_mtime)
        return md[0] if md else None

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
        if self.test_runner == "none":
            return []
        if self.test_runner == "pytest":
            return ["python", "-X", "utf8", "-m", "pytest",
                    str(self.root / self.test_dll), "-q"]
        if self.test_runner == "vstest":
            args = [self.vstest, str(self.root / self.test_dll)]
            if self.test_filter:
                args += ["/TestCaseFilter:" + self.test_filter]
            return args
        if self.test_runner == "dotnet":
            args = ["dotnet", "test", str(self.root / self.test_dll)]
            args += self.build_extra
            if self.test_filter:
                args += ["--filter", self.test_filter]
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
    pl = raw.get("plan", {}) or {}
    rn = raw.get("runner", {}) or {}

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
        test_filter=str(te.get("filter", "") or ""),
        baseline_passed=te.get("baseline_passed"),
        baseline_total=te.get("baseline_total"),
        known_failures=[str(x) for x in (te.get("known_failures") or [])],
        checks=c,
        layer_rules=lr,
        audit_dirs=ad,
        plan_repo=[Path(r) for r in ([pl["repo"]] if isinstance(pl.get("repo"), str)
                                     else (pl.get("repo") or [])) if r],
        plan_subdir=pl.get("subdir", ""),
        plan_file=pl.get("file", ""),
        runner_model=rn.get("model", "opencode-go/deepseek-v4-flash"),
        runner_parallel=int(rn.get("parallel", 1)),
        runner_retries=int(rn.get("retries", 2)),
        question_timeout_sec=int(rn.get("question_timeout_sec", 1200)),
        checkpoint_stages=bool(rn.get("checkpoint_stages", True)),
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
