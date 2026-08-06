# -*- coding: utf-8 -*-
"""Механические проверки конвейера (build/tests/grep/аудит) — декларативно из pipeline.yaml.

История: вынесено из Tasks\\Конвейер\\pipeline.py (HeatLossRevit2), чтобы verify был
одним вызовом, а проверки — конфигурируемыми под любой проект (рекомендация v1 §5.3 п.3).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .config import ProjectConfig
from .models import parse_tests_vstest

# ---------------------------------------------------------------------------
# Низкоуровневые помощники
# ---------------------------------------------------------------------------

def sh(cmd, timeout=1800, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=cwd)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return -1, f"КОМАНДА НЕ НАЙДЕНА: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "ТАЙМАУТ " + " ".join(cmd)


def git(root: Path, msg: str):
    sh(["git", "-C", str(root), "add", "-A"])
    return sh(["git", "-C", str(root), "commit", "-m", msg])


def _grep_count(cfg: ProjectConfig, rel_dir: str, pattern: str) -> tuple:
    """Число .cs-файлов в root\\rel_dir (без skip_dirs), содержащих pattern (regex).
    Возвращает (n, first_hits)."""
    n = 0
    hits = []
    pat = re.compile(pattern)
    base = cfg.root / rel_dir
    if not base.exists():
        return 0, []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in cfg.skip_dirs]
        for fn in filenames:
            if not fn.endswith(".cs"):
                continue
            p = Path(dirpath) / fn
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in txt.splitlines():
                if pat.search(line) and not line.strip().startswith("//"):
                    n += 1
                    hits.append(str(p.relative_to(cfg.root)).replace("\\", "/"))
                    break
    return n, hits[:10]


# ---------------------------------------------------------------------------
# Сборка и тесты
# ---------------------------------------------------------------------------

def build_sln(cfg: ProjectConfig):
    return sh(cfg.msbuild_cmd(), timeout=2400)


def run_tests(cfg: ProjectConfig):
    if cfg.test_runner == "vstest":
        return sh(cfg.test_cmd(), timeout=1200)
    if cfg.test_runner == "dotnet":
        return sh(cfg.test_cmd(), timeout=1800)
    return (-1, f"Нет обработчика test_runner={cfg.test_runner}")


# ---------------------------------------------------------------------------
# Аудит тестов (заглушки / глупые тесты)
# ---------------------------------------------------------------------------

def test_audit(cfg: ProjectConfig) -> tuple:
    """Аудит тестов на заглушки/глупые тесты. Возвращает (ok, detail)."""
    dirs = cfg.audit_dirs
    trivial = [(r"Assert\.True\s*\(\s*true\s*\)", "True(true)"),
               (r"Assert\.False\s*\(\s*false\s*\)", "False(false)"),
               (r"Assert\.AreEqual\s*\(\s*(?:null|0|1)\s*,\s*(?:null|0|1)\s*\)", "AreEqual(константа)")]
    issues = []
    nfiles = 0
    for d in dirs:
        base = cfg.root / d
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in cfg.skip_dirs]
            for fn in filenames:
                if not fn.endswith(".cs"):
                    continue
                nfiles += 1
                p = Path(dirpath) / fn
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = str(p.relative_to(cfg.root)).replace("\\", "/")
                for pat, name in trivial:
                    if re.search(pat, txt, re.I):
                        issues.append(f"{rel}: {name}")
                # NotImplementedException — проблема только в файлах-тестах
                # (с атрибутами [Test]/[TestCase]). В инфраструктуре харнесса
                # (Infra/) это может быть мёртвый метод-сериализатор — не заглушка теста.
                if "NotImplementedException" in txt and ("[Test" in txt or "[TestCase" in txt):
                    issues.append(f"{rel}: NotImplementedException")
    ok = len(issues) == 0
    return ok, (f"файлов {nfiles}, заглушек 0" if ok else "; ".join(issues[:5]))


# ---------------------------------------------------------------------------
# Декларативные проверки задач (registry kinds)
# ---------------------------------------------------------------------------

def _kind_build_grep(cfg, c, b_out):
    """Проверка в логе сборки: count(pattern) == expect (по умолчанию 0).
    Если лог сборки пуст — не может быть достоверной, помечается как SKIP."""
    if not b_out.strip():
        return False, "SKIP — лог сборки пуст (проверка build_grep требует реальной сборки)"
    expect = c.get("expect", 0)
    n = len(re.findall(c["pattern"], b_out))
    return (n == expect), f"вхождений в логе sln={n} (ожидалось {expect})"


def _kind_csproj_no_ref(cfg, c, b_out=""):
    """Проект в c['dir'] не ссылается ProjectReference на c['forbidden'].
    Проверяет все *.csproj на верхнем уровне каталога проекта."""
    base = cfg.resolve(c["dir"])
    forbidden = c["forbidden"]
    if not base.is_dir():
        return False, f"папки проекта НЕТ: {c['dir']}"
    found = []
    for csproj in sorted(base.glob("*.csproj")):
        txt = csproj.read_text(encoding="utf-8", errors="replace")
        for line in txt.splitlines():
            if "ProjectReference" in line and forbidden in line:
                found.append(f"{csproj.name}: {line.strip()}")
    ok = not found
    return ok, ("ссылок ProjectReference нет" if ok else "; ".join(found[:4]))


def _kind_grep_dir(cfg, c, b_out=""):
    """Проверка grep по .cs в каталоге: count(pattern) == expect."""
    expect = c.get("expect", 0)
    n, hits = _grep_count(cfg, c["dir"], c["pattern"])
    return (n == expect), (f"вхождений {n} (ожидалось {expect})" if n == expect
                           else f"найдено {n}: " + "; ".join(hits))


def _kind_dir_exists(cfg, c, b_out=""):
    p = cfg.resolve(c["dir"])
    ok = p.is_dir()
    return ok, ("папка есть" if ok else f"папки НЕТ: {c['dir']}")


def _kind_dir_exists_and_not(cfg, c, b_out=""):
    np = cfg.resolve(c["dir"]).is_dir()
    old = cfg.resolve(c["not_dir"]).is_dir()
    return (np and not old), f"новый={np}, старый={old}"


def _kind_class_location(cfg, c, b_out=""):
    """Класс определён ТОЛЬКО в указанных каталогах (для guard-слоёв)."""
    allow = set(c["dirs"])
    n_allow = 0
    n_out = 0
    pat = re.compile(r"class\s+" + re.escape(c["class"]) + r"\b")
    for dirpath, dirnames, filenames in os.walk(cfg.root):
        dirnames[:] = [d for d in dirnames if d not in cfg.skip_dirs]
        rel = str(Path(dirpath).relative_to(cfg.root)).replace("\\", "/")
        inside = any(rel == d or rel.startswith(d + "/") for d in allow)
        for fn in filenames:
            if not fn.endswith(".cs"):
                continue
            p = Path(dirpath) / fn
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if pat.search(txt):
                if inside:
                    n_allow += 1
                else:
                    n_out += 1
    return (n_allow >= 1 and n_out == 0), f"в {','.join(c['dirs'])}={n_allow}, вне={n_out}"


def _kind_file_small(cfg, c, b_out=""):
    """Файл существует и число строк < max_lines (тонкий composition root)."""
    f = cfg.resolve(c["file"])
    if not f.is_file():
        return False, f"файла НЕТ: {c['file']}"
    n = sum(1 for _ in open(f, encoding="utf-8", errors="replace"))
    return (n < c.get("max_lines", 200)), f"строк {n} (лимит {c.get('max_lines', 200)})"


def _kind_layer_rules(cfg, c, b_out=""):
    """grep-правила слоёв из layer_rules: для каждого dir count(pattern) == 0."""
    problems = []
    for rule in c.get("rules", []):
        expect = rule.get("expect", 0)
        for d in rule["dirs"]:
            n, hits = _grep_count(cfg, d, rule["pattern"])
            if n != expect:
                problems.append(f"{d}: {n} вхождений " + "; ".join(hits[:4]))
    ok = not problems
    return ok, ("все правила слоёв чисты" if ok else "; ".join(problems[:4]))


CHECK_KINDS = {
    "build_grep": _kind_build_grep,
    "grep_dir": _kind_grep_dir,
    "dir_exists": _kind_dir_exists,
    "dir_exists_and_not": _kind_dir_exists_and_not,
    "class_location": _kind_class_location,
    "file_small": _kind_file_small,
    "layer_rules": _kind_layer_rules,
    "csproj_no_ref": _kind_csproj_no_ref,
}


def verify_checks(cfg: ProjectConfig, task_id: str, b_out: str = "") -> list:
    """Список (label, result_str) для задачи из конфига checks."""
    rows = []
    for c in cfg.checks:
        ids = c.get("ids") or [c.get("id")]
        if task_id not in ids:
            continue
        kind = c.get("kind")
        fn = CHECK_KINDS.get(kind)
        if fn is None:
            rows.append((c.get("label", kind), f"ОШИБКА: неизвестный kind '{kind}'"))
            continue
        try:
            ok, detail = fn(cfg, c, b_out)
            if detail.startswith("SKIP"):
                rows.append((c.get("label", kind), detail))
            else:
                rows.append((c.get("label", kind), ("OK — " if ok else "FAIL — ") + detail))
        except Exception as e:
            rows.append((c.get("label", kind), "ОШИБКА ПРОВЕРКИ — " + str(e)[:200]))
    return rows


def layer_rule_rows(cfg: ProjectConfig) -> list:
    """Проверки правил слоёв (layer_rules) — для каждой verify.
    Правило может быть grep-правилом (dirs+pattern) либо структурным (kind, напр. csproj_no_ref)."""
    rows = []
    for c in cfg.layer_rules:
        try:
            kind = c.get("kind", "grep")
            if kind == "grep":
                ok, detail = _kind_layer_rules(cfg, {"rules": [c]})
            else:
                fn = CHECK_KINDS.get(kind)
                if fn is None:
                    ok, detail = False, f"ОШИБКА: неизвестный kind '{kind}'"
                else:
                    ok, detail = fn(cfg, c)
            rows.append((c.get("label", "правило слоёв"), ("OK — " if ok else "FAIL — ") + detail))
        except Exception as e:
            rows.append((c.get("label", "правило слоёв"), "ОШИБКА ПРОВЕРКИ — " + str(e)[:200]))
    return rows
