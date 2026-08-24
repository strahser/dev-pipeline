# -*- coding: utf-8 -*-
"""Git-активность проекта для панели (карточка 5.1).

Коммиты — активность, не статусы: источник истины о прогрессе остаётся файл
плана. Префиксы — по конвенции protocol.md §7.1 (включая легаси-группу);
карточка извлекается из `plan/<CARD>:` или трейлера `Refs: <CARD>` в теле.
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MAIN_PREFIXES = {
    "pipeline", "docs", "inbox", "plans",
    "feat", "fix", "chore", "ui", "server", "agents", "tdl", "tools",
    "config", "security", "dashboard", "skills", "manager", "init",
}

_PLAN_CARD_RE = re.compile(r"^plan/([A-Za-z0-9][\w.\-]*)\s*:")
_PREFIX_RE = re.compile(r"^([A-Za-z][\w]*)(?:/([\w.\-]+))?\s*:\s*")
_BODY_CARD_RE = re.compile(
    r"Refs:\s*([A-Za-z0-9][\w.\-]*(?:\s*,\s*[A-Za-z0-9][\w.\-]*)*)")


def parse_subject(subject: str, body: str = "") -> dict:
    """{'prefix': str, 'scope': str|None, 'card': str|None} по конвенции §7.1."""
    m = _PREFIX_RE.match(subject or "")
    if not m:
        return {"prefix": "", "scope": None, "card": None}
    head, scope = m.group(1), m.group(2)
    prefix = head.lower()
    card = None
    if prefix == "plan" and scope:
        mc = _PLAN_CARD_RE.match(subject)
        card = mc.group(1) if mc else scope
    elif prefix in ("project", "agent", "review") and scope:
        pass  # scope сохраняется (имя проекта/номер задачи)
    elif prefix not in _MAIN_PREFIXES:
        prefix = head.lower()
    if card is None:
        mb = _BODY_CARD_RE.search(body or "")
        if mb:
            card = mb.group(1).split(",")[0].strip()
    return {"prefix": prefix, "scope": scope, "card": card}


def project_repos(cfg) -> list[Path]:
    """Репозитории активности: корень проекта + база планов (без дублей)."""
    out: list[Path] = []
    root = getattr(cfg, "root", None)
    if root is not None and Path(root).exists():
        out.append(Path(root))
    for p in (getattr(cfg, "plan_repo", []) or []):
        pp = Path(p)
        if pp.exists() and all(pp.resolve() != q.resolve() for q in out):
            out.append(pp)
    return out


def collect(repos: list[Path], days: int = 7, limit: int = 50,
            now: float | None = None) -> list[dict]:
    """Коммиты за K дней по всем репозиториям: repo/hash/date/prefix/card/subject."""
    now = now if now is not None else datetime.now().timestamp()
    since = datetime.fromtimestamp(now) - timedelta(days=max(1, days))
    sep, rec = "\x1f", "\x1e"
    fmt = f"%H{sep}%aI{sep}%s{sep}%b{rec}"
    entries: list[dict] = []
    for repo in repos:
        if not (repo / ".git").exists():
            continue
        try:
            r = subprocess.run(
                ["git", "-C", str(repo), "log",
                 f"--since={since.isoformat(timespec='seconds')}",
                 f"--pretty=format:{fmt}", "-n", str(max(1, limit) * 3)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        for chunk in r.stdout.split(rec):
            parts = chunk.strip("\n").split(sep)
            if len(parts) < 3 or not parts[0]:
                continue
            hash_, date, subj = parts[0], parts[1], parts[2]
            body = parts[3] if len(parts) > 3 else ""
            info = parse_subject(subj, body)
            entries.append({
                "repo": repo.name, "hash": hash_[:8], "date": date,
                "subject": subj,
                "prefix": info["prefix"], "scope": info["scope"],
                "card": info["card"],
                "_ts": _ts(date),
            })
    entries.sort(key=lambda e: e["_ts"], reverse=True)
    for e in entries:
        e.pop("_ts", None)
    return entries[: max(1, limit)]


def _ts(iso_date: str) -> float:
    try:
        return datetime.fromisoformat(iso_date).timestamp()
    except ValueError:
        return 0.0


def stage_of(card: str | None) -> str:
    """Этап = префикс id листовой карточки до последней точки (как _stage_id).

    'A-1.1' -> 'A-1', 'plan/U1.2' -> 'U1', без точки/без card -> '—'.
    """
    if not card:
        return "—"
    core = card.split("-", 1)[-1]
    if "." not in core:
        return "—"
    return card.rsplit(".", 1)[0]


def group_stages(commits: list[dict]) -> list[dict]:
    """Группировка коммитов по этапу (card -> stage).

    Возвращает [{stage, commits, last_subject, last_date, feat_n, fix_n}],
    отсортировано по последней дате (свежие сверху). Коммиты без card ->
    группа '—'. feat/fix считаются по префиксу (prefix == 'feat'/'fix').
    """
    groups: dict[str, list[dict]] = {}
    for c in commits:
        st = stage_of(c.get("card"))
        groups.setdefault(st, []).append(c)
    out = []
    for st, lst in groups.items():
        lst.sort(key=lambda c: _ts(c["date"]), reverse=True)
        feat_n = sum(1 for c in lst if c.get("prefix") == "feat")
        fix_n = sum(1 for c in lst if c.get("prefix") == "fix")
        last = lst[0]
        out.append({
            "stage": st,
            "commits": len(lst),
            "last_subject": last.get("subject", ""),
            "last_date": last.get("date", ""),
            "feat_n": feat_n,
            "fix_n": fix_n,
        })
    out.sort(key=lambda g: _ts(g["last_date"]), reverse=True)
    return out
