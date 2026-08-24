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
    "brief", "verify",
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


def base_card(card: str) -> str:
    """Базовый id карточки без служебного суффикса постановки.

    '6.1-review' -> '6.1', 'A-1.1' -> 'A-1.1' (суффикс срезается только если
    база содержит точку, а суффикс — нет)."""
    parts = card.rsplit("-", 1)
    if len(parts) == 2 and "." in parts[0] and "." not in parts[1]:
        return parts[0]
    return card


def touched_cards(commits: list[dict]) -> set[str]:
    """Множество карточек, затронутых коммитами (card + базовый id).

    Коммиты с review-постановками ('<id>-review') засчитываются базовой
    карточке — авто-прогресс этапа не должен требовать статуса done."""
    out: set[str] = set()
    for c in commits:
        card = c.get("card")
        if card:
            out.add(card)
            out.add(base_card(card))
    return out


def within_days(commits: list[dict], days: int,
                now: float | None = None) -> list[dict]:
    """Фильтр коммитов: только последние `days` дней (для одного прохода
    git log за широкий окно с последующей нарезкой)."""
    now = now if now is not None else datetime.now().timestamp()
    cutoff = now - days * 86400
    return [c for c in commits if _ts(c.get("date", "")) >= cutoff]


def merge_auto_progress(stages: list[dict], open_by_stage: dict[str, list[str]],
                        commits: list[dict]) -> None:
    """Авто-прогресс этапов по git-коммитам (in place).

    stage['auto'] — открытые карточки этапа, затронутые коммитами
    (работа идёт, статус в плане ещё не обновлён); stage['commits'] —
    число коммитов этапа за окно. Прогресс-бар панели растёт автоматически:
    solid = статусы плана, штриховка = авто по коммитам."""
    touched = touched_cards(commits)
    for st in stages:
        open_ids = open_by_stage.get(st["stage"], [])
        st["auto"] = sum(1 for cid in open_ids if cid in touched)
        st["commits"] = sum(1 for c in commits
                            if c.get("card") and stage_of(c["card"]) == st["stage"])


def stage_of(card: str | None) -> str:
    """Этап = префикс id листовой карточки до последней точки (как _stage_id).

    'A-1.1' -> 'A-1', 'plan/U1.2' -> 'U1', без точки/без card -> '—'.
    Служебные суффиксы постановок срезаются: '6.1-review' -> этап '6'."""
    if not card:
        return "—"
    base = base_card(card)
    core = base.split("-", 1)[-1]
    if "." not in core:
        return "—"
    return base.rsplit(".", 1)[0]


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
