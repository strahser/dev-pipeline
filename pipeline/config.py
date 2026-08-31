# -*- coding: utf-8 -*-
"""Р—Р°РіСЂСѓР·РєР° РєРѕРЅС„РёРіСѓСЂР°С†РёРё РїСЂРѕРµРєС‚Р° (pipeline.yaml) РґР»СЏ dev-pipeline.

РЎС…РµРјР° РєРѕРЅС„РёРіР° (СЃРј. examples\\heatlossrevit2\\pipeline.yaml):
    project: { name, root, branch }
    tasks:   { inbox, active, reports, archive, protocol, status, conveyor }
    build:   { msbuild, sln, configuration, platform, extra_args }
    tests:   { runner, vstest, dll, baseline_passed, baseline_total }
    checks:  СЃРїРёСЃРѕРє РїСЂРѕРІРµСЂРѕРє (СЃРј. pipeline.checks)
    layer_rules: СЃРїРёСЃРѕРє grep-РїСЂР°РІРёР» СЃР»РѕС‘РІ
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

    # РџР°РїРєРё Tasks (РѕС‚РЅРѕСЃРёС‚РµР»СЊРЅРѕ root)
    inbox: str = "Tasks\\Р’С…РѕРґСЏС‰РёРµ"
    active: str = "Tasks\\РђРєС‚РёРІРЅС‹Рµ"
    reports: str = "Tasks\\РћС‚С‡С‘С‚С‹"
    archive: str = "Tasks\\РђСЂС…РёРІ"
    protocol: str = "Tasks\\00_РџСЂРѕС‚РѕРєРѕР»_Р°РіРµРЅС‚РѕРІ.md"
    status: str = "Tasks\\РЎС‚Р°С‚СѓСЃ_РєРѕРЅРІРµР№РµСЂР°.md"
    conveyor: str = "Tasks\\РљРѕРЅРІРµР№РµСЂ"
    notif: str = "Tasks\\РљРѕРЅРІРµР№РµСЂ\\РЈРІРµРґРѕРјР»РµРЅРёСЏ"

    # РЎР±РѕСЂРєР°
    msbuild: str = ""
    sln: str = ""
    configuration: str = "Debug"
    platform: str = "Any CPU"
    build_extra: list = field(default_factory=list)

    # РўРµСЃС‚С‹
    test_runner: str = "vstest"
    vstest: str = ""
    test_dll: str = ""
    test_filter: str = ""            # dotnet test --filter (С‚РѕС‡РµС‡РЅС‹Р№/Р±С‹СЃС‚СЂС‹Р№ РЅР°Р±РѕСЂ)
    baseline_passed: int | None = None
    baseline_total: int | None = None

    # РџСЂРѕРІРµСЂРєРё Рё РїСЂР°РІРёР»Р° СЃР»РѕС‘РІ (РґРµРєР»Р°СЂР°С‚РёРІРЅРѕ)
    checks: list = field(default_factory=list)
    layer_rules: list = field(default_factory=list)
    audit_dirs: list = field(default_factory=lambda: ["Test", "Core.Tests"])

    # РџР»Р°РЅ (СЂРµРїРѕР·РёС‚РѕСЂРёР№ ProjectsPalns): РёСЃС‚РѕС‡РЅРёРє РєР°СЂС‚РѕС‡РµРє РґР»СЏ plan_runner
    plan_repo: list = field(default_factory=list)   # РєР°РЅРґРёРґР°С‚С‹ РєРѕСЂРЅСЏ ProjectsPalns
    plan_subdir: str = ""                            # <РџСЂРѕРµРєС‚> РІРЅСѓС‚СЂРё ProjectsPalns
    plan_file: str = ""                              # РєРѕРЅРєСЂРµС‚РЅС‹Р№ С„Р°Р№Р» (РёРЅР°С‡Рµ вЂ” РЅРѕРІРµР№С€РёР№ _current/*.md)

    # РџР»Р°РЅ-СЂР°РЅРЅРµСЂ
    runner_model: str = "opencode/big-pickle"
    runner_retries: int = 2                          # СЂРµС‚СЂР°РµРІ РєР°СЂС‚РѕС‡РєРё СЃ Р»РѕРіРѕРј РѕС€РёР±РєРё
    question_timeout_sec: int = 1200                 # С‚РёС€РёРЅР° РїРѕ РІРѕРїСЂРѕСЃСѓ -> СЂР°Р±РѕС‚Р° РїРѕ РґРѕРїСѓС‰РµРЅРёСЏРј
    checkpoint_stages: bool = True                   # РїР°СѓР·Р° РїРѕСЃР»Рµ Р·Р°РєСЂС‹С‚РёСЏ СЌС‚Р°РїР° (summary)
    stage_approver: str = "owner"                    # owner | reviewer вЂ” РєС‚Рѕ РѕРґРѕР±СЂСЏРµС‚ СЌС‚Р°Рї
    checkpoint_remind_sec: int = 600                 # РЅР°РїРѕРјРёРЅР°РЅРёРµ checkpoint_waiting РєР°Р¶РґС‹Рµ N СЃРµРє РѕР¶РёРґР°РЅРёСЏ
    semantic_review: bool = False                    # РЅРµР·Р°РІРёСЃРёРјР°СЏ reviewer-С„Р°Р·Р° РїРѕСЃР»Рµ PASS (РєР°СЂС‚РѕС‡РєР° 4.2)

    # Crew: Р°РІС‚РѕРЅРѕРјРЅС‹Рµ СЃРµСЃСЃРёРё РїСЂРѕРµРєС‚Р° (РєР°СЂС‚РѕС‡РєР° 6.2)
    crew_roles: list = field(default_factory=lambda: ["executor"])
    crew_model: str = ""                             # РїСѓСЃС‚Рѕ = РґРµС„РѕР»С‚ СЃРµСЂРІРµСЂР°
    crew_permissions: str = "write"                  # read | write вЂ” РїСЂРѕС„РёР»СЊ opencode
    restart_max: int = 3                             # РїРµСЂРµР·Р°РїСѓСЃРєРѕРІ СЃРµСЃСЃРёРё РЅР° РїРѕСЂС†РёСЋ
    restart_cooldown_sec: int = 300                  # РїР°СѓР·Р° РјРµР¶РґСѓ РїРµСЂРµР·Р°РїСѓСЃРєР°РјРё

    # РЎР»СѓР¶РµР±РЅС‹Рµ РЅР°СЃС‚СЂРѕР№РєРё
    skip_dirs: list = field(default_factory=lambda: [
        "bin", "obj", ".git", ".idea", ".opencode", "packages",
        "TestResults", "__pycache__", "РђСЂС…РёРІ",
    ])

    def resolve(self, rel: str) -> Path:
        return self.root / rel

    def abs_tasks_dir(self, folder_key: str) -> Path:
        return self.resolve(getattr(self, folder_key))

    def questions_dir(self) -> Path:
        """РџР°РїРєР° РІРѕРїСЂРѕСЃРѕРІ Р°РіРµРЅС‚РѕРІ (grill-С„Р°Р·Р°): Tasks\\Р’РѕРїСЂРѕСЃС‹."""
        return self.root / "Tasks" / "Р’РѕРїСЂРѕСЃС‹"

    def conveyor_dir(self) -> Path:
        return self.root / "Tasks" / "РљРѕРЅРІРµР№РµСЂ"

    def plan_dir(self):
        """РљР°С‚Р°Р»РѕРі РїР»Р°РЅРѕРІ РїСЂРѕРµРєС‚Р° РІ ProjectsPalns: <repo>\\<subdir|name>\\_current.
        Р’РѕР·РІСЂР°С‰Р°РµС‚ Path РёР»Рё None, РµСЃР»Рё СЂРµРїРѕР·РёС‚РѕСЂРёР№ РїР»Р°РЅРѕРІ РЅРµ РЅР°Р№РґРµРЅ."""
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
        """РђРєС‚СѓР°Р»СЊРЅС‹Р№ С„Р°Р№Р» РїР»Р°РЅР°: СЏРІРЅС‹Р№ plan.file РёР»Рё РЅРѕРІРµР№С€РёР№ _current/*.md."""
        d = self.plan_dir()
        if d is None:
            return None
        if self.plan_file:
            p = d / self.plan_file
            return p if p.exists() else None
        md = sorted(d.glob("*.md"), key=lambda p: -p.stat().st_mtime)
        return md[0] if md else None

    # РљРѕСЂРѕС‚РєРёРµ РїРѕРјРѕС‰РЅРёРєРё РґР»СЏ РєРѕРјР°РЅРґ СЃР±РѕСЂРєРё/С‚РµСЃС‚РѕРІ
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
        raise ConfigError(f"РќРµРёР·РІРµСЃС‚РЅС‹Р№ test_runner: {self.test_runner}")


def _require(d: dict, key: str, where: str):
    if key not in d:
        raise ConfigError(f"Р’ pipeline.yaml РѕС‚СЃСѓС‚СЃС‚РІСѓРµС‚ СЃРµРєС†РёСЏ/РїРѕР»Рµ '{key}' ({where})")
    return d[key]


def _root_candidates(project_name: str, raw_project: dict) -> list[Path]:
    """РЎРїРёСЃРѕРє РєР°РЅРґРёРґР°С‚РѕРІ РєРѕСЂРЅСЏ РїСЂРѕРµРєС‚Р° (РїРµСЂРІС‹Р№ СЃСѓС‰РµСЃС‚РІСѓСЋС‰РёР№ Р±СѓРґРµС‚ РІС‹Р±СЂР°РЅ).

    РџРѕСЂСЏРґРѕРє РїСЂРёРѕСЂРёС‚РµС‚Р°:
      1. РџРµСЂРµРјРµРЅРЅР°СЏ РѕРєСЂСѓР¶РµРЅРёСЏ DEV_PIPELINE_PROJECTS_DIR (Р±Р°Р·РѕРІР°СЏ РїР°РїРєР° РїСЂРѕРµРєС‚РѕРІ РЅР° РџРљ);
      2. project.root РёР· YAML вЂ” СЃС‚СЂРѕРєР° РР›Р СЃРїРёСЃРѕРє РїСѓС‚РµР№ (РЅРµСЃРєРѕР»СЊРєРѕ СЂР°Р±РѕС‡РёС… РјРµСЃС‚);
      3. project.roots РёР· YAML (СЃРёРЅРѕРЅРёРј СЃРїРёСЃРєР°).
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
    """Р—Р°РіСЂСѓР·РёС‚СЊ examples/<project>/pipeline.yaml."""
    cfg_path = EXAMPLES_DIR / project_name / "pipeline.yaml"
    if not cfg_path.exists():
        raise ConfigError(
            f"РљРѕРЅС„РёРі РЅРµ РЅР°Р№РґРµРЅ: {cfg_path}. "
            f"Р”РѕСЃС‚СѓРїРЅС‹Рµ РїСЂРѕРµРєС‚С‹: {[p.name for p in EXAMPLES_DIR.iterdir() if p.is_dir()]}")
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
            f"project.root РЅРµ СЃСѓС‰РµСЃС‚РІСѓРµС‚ РЅРё РІ РѕРґРЅРѕРј РёР· РїСѓС‚РµР№ ({len(tried)}):\n"
            + "\n".join(f"  - {pth}" for pth in tried)
            + "\nР—Р°РґР°Р№С‚Рµ DEV_PIPELINE_PROJECTS_DIR (Р±Р°Р·РѕРІР°СЏ РїР°РїРєР° РїСЂРѕРµРєС‚РѕРІ) РёР»Рё "
              "СѓРєР°Р¶РёС‚Рµ СЃСѓС‰РµСЃС‚РІСѓСЋС‰РёР№ РїСѓС‚СЊ РІ project.root/roots.")

    return ProjectConfig(
        name=project_name,
        root=root,
        branch=p.get("branch", ""),
        inbox=t.get("inbox", "Tasks\\Р’С…РѕРґСЏС‰РёРµ"),
        active=t.get("active", "Tasks\\РђРєС‚РёРІРЅС‹Рµ"),
        reports=t.get("reports", "Tasks\\РћС‚С‡С‘С‚С‹"),
        archive=t.get("archive", "Tasks\\РђСЂС…РёРІ"),
        protocol=t.get("protocol", "Tasks\\00_РџСЂРѕС‚РѕРєРѕР»_Р°РіРµРЅС‚РѕРІ.md"),
        status=t.get("status", "Tasks\\РЎС‚Р°С‚СѓСЃ_РєРѕРЅРІРµР№РµСЂР°.md"),
        conveyor=t.get("conveyor", "Tasks\\РљРѕРЅРІРµР№РµСЂ"),
        notif=t.get("notif", "Tasks\\РљРѕРЅРІРµР№РµСЂ\\РЈРІРµРґРѕРјР»РµРЅРёСЏ"),
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
        checks=c,
        layer_rules=lr,
        audit_dirs=ad,
        plan_repo=[Path(r) for r in ([pl["repo"]] if isinstance(pl.get("repo"), str)
                                     else (pl.get("repo") or [])) if r],
        plan_subdir=pl.get("subdir", ""),
        plan_file=pl.get("file", ""),
        runner_model=rn.get("model", "opencode/big-pickle"),
        runner_retries=int(rn.get("retries", 2)),
        question_timeout_sec=int(rn.get("question_timeout_sec", 1200)),
        checkpoint_stages=bool(rn.get("checkpoint_stages", True)),
        stage_approver=str(rn.get("stage_approver", "owner")),
        checkpoint_remind_sec=int(rn.get("checkpoint_remind_sec", 600)),
        semantic_review=bool(rn.get("semantic_review", False)),
        crew_roles=[str(r) for r in ((raw.get("crew") or {}).get("roles")
                                     or ["executor"])],
        crew_model=str((raw.get("crew") or {}).get("model", "")),
        crew_permissions=str((raw.get("crew") or {}).get("permissions", "write")),
        restart_max=int((raw.get("restart_policy") or {}).get("max_restarts", 3)),
        restart_cooldown_sec=int((raw.get("restart_policy") or {})
                                 .get("cooldown_sec", 300)),
    )


def list_projects() -> list[str]:
    if not EXAMPLES_DIR.exists():
        return []
    return sorted(p.name for p in EXAMPLES_DIR.iterdir()
                  if p.is_dir() and (p / "pipeline.yaml").exists())


def check_env() -> None:
    """РџСЂРѕРІРµСЂРёС‚СЊ СЃСѓС‰РµСЃС‚РІРѕРІР°РЅРёРµ РїСѓС‚РµР№ СЃР±РѕСЂРєРё/С‚РµСЃС‚РѕРІ РёР· РІСЃРµС… РєРѕРЅС„РёРіРѕРІ (РґР»СЏ diagnostics)."""
    for name in list_projects():
        try:
            cfg = load_config(name)
        except ConfigError as e:
            print(f"  {name}: РћРЁРР‘РљРђ {e}")
            continue
        problems = []
        if cfg.msbuild.lower() != "dotnet" and cfg.msbuild and not os.path.exists(cfg.msbuild):
            problems.append(f"msbuild РЅРµ РЅР°Р№РґРµРЅ: {cfg.msbuild}")
        if cfg.vstest and not os.path.exists(cfg.vstest):
            problems.append(f"vstest РЅРµ РЅР°Р№РґРµРЅ: {cfg.vstest}")
        if cfg.sln and not (cfg.root / cfg.sln).exists():
            problems.append(f"sln/РїСЂРѕРµРєС‚ РЅРµ РЅР°Р№РґРµРЅ: {cfg.root / cfg.sln}")
        if problems:
            print(f"  {name}: " + "; ".join(problems))
        else:
            print(f"  {name}: РѕРє")

