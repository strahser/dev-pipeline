# -*- coding: utf-8 -*-
"""Project Brief — авто-дайджест контекста проекта для субагента (Уровень 1).

Собирается локально из файлов+git (сервер не обязателен): статус плана вокруг
карточки, свежие коммиты, последние отчёты/вердикты, открытые вопросы,
состояние конвейера. Раннер вставляет бриф в промпт карточки секцией
«КОНТЕКСТ ПРОЕКТА» — субагент стартует осведомлённым без ручной передачи.
"""
from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path


def _git_log(root: Path, n: int = 10) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "log", "--oneline", f"-n{n}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15)
        out = (r.stdout or "").strip()
        return out if r.returncode == 0 and out else "(git недоступен или пусто)"
    except Exception as e:
        return f"(git ошибка: {e})"


def _stage_of(cid: str) -> str:
    core = cid.split("-", 1)[-1]
    return cid.rsplit(".", 1)[0] if "." in core else ""


def _plan_section(cfg, card) -> list[str]:
    from pipeline.plans import load as load_plan
    parts = []
    pf = cfg.find_plan_file()
    if pf is None:
        return ["- файл плана не найден (plan.repo/subdir/file в pipeline.yaml)"]
    plan = load_plan(pf)
    prog = plan.progress()
    parts.append(f"- прогресс листовых карточек: {prog['done']}/{prog['total']} выполнено")
    stage = _stage_of(card.id) if card else ""
    sibs = [c for c in plan.execution_cards()
            if not stage or _stage_of(c.id) == stage] if card else []
    if sibs:
        done = [f"{c.id}" for c in sibs if c.status == "done"]
        open_ = [f"{c.id} — {c.title[:60]}" for c in sibs if c.status != "done"]
        if done:
            parts.append(f"- закрыты на этом этапе: {', '.join(done)}")
        if open_:
            parts.append("- открыты на этом этапе:")
            parts += [f"  • {s}" for s in open_[:6]]
        # критерии текущей карточки уже в постановке; тут только соседи
    return parts


def _artifacts_section(cfg) -> list[str]:
    rd = cfg.abs_tasks_dir("reports")
    if not rd.is_dir():
        return ["- отчётов пока нет"]
    files = sorted(rd.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:6]
    out = []
    for f in files:
        try:
            head = next((l for l in f.read_text(encoding="utf-8",
                                                 errors="replace").splitlines()
                         if l.strip()), "")[:100]
        except OSError:
            head = ""
        age_h = int((datetime.datetime.now().timestamp() - f.stat().st_mtime) / 3600)
        out.append(f"- {f.name} ({age_h} ч назад): {head}")
    return out or ["- отчётов нет"]


def _questions_section(cfg) -> list[str]:
    qd = cfg.questions_dir()
    if not qd.is_dir():
        return ["- открытых вопросов нет"]
    open_q = []
    for f in sorted(qd.glob("*.md")):
        txt = f.read_text(encoding="utf-8", errors="replace")
        tail = txt.split("## Ответы", 1)[1].strip() if "## Ответы" in txt else ""
        if not tail:
            title = next((l.lstrip('# ') for l in txt.splitlines()
                          if l.startswith('#')), f.stem)
            open_q.append(f"- {f.stem}: {title[:90]}")
    return [f"- ОТКРЫТЫХ ВОПРОСОВ: {len(open_q)}"] + open_q[:6] \
        if open_q else ["- открытых вопросов нет"]


def _state_section(cfg) -> list[str]:
    out = []
    sf = cfg.conveyor_dir() / "runner_state.json"
    if sf.exists():
        try:
            st = json.loads(sf.read_text(encoding="utf-8"))
            out.append(f"- раннер: фаза={st.get('phase')}, карточка={st.get('card')}, "
                       f"попытка={st.get('attempt')}")
        except Exception:
            pass
    sd = cfg.conveyor_dir() / "stalled"
    if sd.is_dir():
        marks = [p.stem for p in sorted(sd.glob("*.txt"))]
        if marks:
            out.append(f"- ЗАВИСШИЕ задачи: {', '.join(marks)}")
    cp = cfg.conveyor_dir() / "checkpoints"
    if cp.is_dir() and list(cp.glob("*.pending.json")):
        out.append("- есть ожидающие вашего одобрения чекпоинты")
    return out or ["- конвейер простаивает"]


def build_brief(cfg, card=None) -> str:
    lines = [f"# КОНТЕКСТ ПРОЕКТА {cfg.name} "
             f"(авто-дайджест; сомнительное проверь по коду сам)", ""]

    lines.append("## План и этап")
    lines += _plan_section(cfg, card)

    lines.append("")
    lines.append("## Последние коммиты проекта")
    lines.append("```")
    lines.append(_git_log(cfg.root, 10))
    lines.append("```")

    lines.append("")
    lines.append("## Свежие артефакты конвейера (отчёты/вердикты)")
    lines += _artifacts_section(cfg)

    lines.append("")
    lines.append("## Вопросы к владельцу (grill)")
    lines += _questions_section(cfg)

    lines.append("")
    lines.append("## Состояние конвейера")
    lines += _state_section(cfg)

    return "\n".join(lines)
