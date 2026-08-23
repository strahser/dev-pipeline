# -*- coding: utf-8 -*-
"""Автономный терминал агента: цикл порций с /new-эквивалентом (карточка 2.1).

opencode не умеет нажимать /new в ЧУЖОМ работающем TUI (`opencode session` —
только list/delete), но TUI запускается сразу с промптом:
    opencode <root> --prompt "..." --auto
Поэтому автономность делается циклом в ОДНОМ видимом окне:

  порция = свежая сессия opencode (чистый контекст, как после /new)
    -> агент дописал порцию и записал handoff-файл
       Tasks\\Конвейер\\handoff\\<метка>.md (формат 6.2)
    -> цикл подхватывает handoff и запускает СЛЕДУЮЩУЮ чистую сессию
       с базовым промптом + хвостом handoff
  нет handoff / падение сессии / лимит restart_policy -> цикл завершён.

Права write разворачиваются перед стартом (crew.ensure_permissions).
Запуск из панели: кнопка «🖥 Терминал» -> POST /api/chat/agents/terminal
(новое окно консоли, промпт через env PIPELINE_TUI_PROMPT). Вручную:
    python -X utf8 agents/tui_cycle.py --project <p> [--role executor] [--prompt "..."]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HANDOFF_TAIL_LIMIT = 6000

ROLE_STARTERS = {
    "executor": (
        "Ты АВТОНОМНЫЙ ИСПОЛНИТЕЛЬ проекта {project}. Работай по правилам AGENTS.md "
        "репозитория конвейера и цели проекта ниже. Порция = законченная правка: "
        "код -> сборка/тесты -> коммит (явные пути, префиксы из протокола). "
        "В КОНЦЕ каждой порции запиши handoff-файл "
        "Tasks\\Конвейер\\handoff\\<метка времени>.md (секции: ГОТОВО, ЗАДАЧА дальше, "
        "Грабли) и заверши работу — по нему начнётся следующая чистая сессия."),
    "controller": (
        "Ты КОНТРОЛЁР проекта {project}: ведёшь план ProjectsPalns, запускаешь "
        "карточки план-раннером, принимаешь вердикты, отвечаешь на вопросы агентов. "
        "Каждую порцию завершай handoff-файлом Tasks\\Конвейер\\handoff\\<метка>.md."),
}


def build_base_prompt(cfg, role: str = "executor", user_prompt: str = "") -> str:
    """Базовый промпт каждой порции: роль + цель проекта + задание владельца."""
    starter = ROLE_STARTERS.get(role, ROLE_STARTERS["executor"])
    parts = [starter.format(project=cfg.name)]
    try:
        from pipeline.brief import goal_section
        goal = goal_section(cfg)
        if goal:
            parts.append("ЦЕЛЬ ПРОЕКТА:\n" + goal)
    except Exception:
        pass
    if user_prompt:
        parts.append("ЗАДАНИЕ ВЛАДЕЛЬЦА:\n" + user_prompt)
    return "\n\n".join(parts)


def newest_handoff(cfg, after_ts: float) -> Path | None:
    """Новейший handoff-файл, появившийся ПОСЛЕ старта порции."""
    d = cfg.conveyor_dir() / "handoff"
    if not d.is_dir():
        return None
    fresh = [p for p in d.glob("*.md") if p.stat().st_mtime >= after_ts]
    return max(fresh, key=lambda p: p.stat().st_mtime) if fresh else None


def default_runner(cfg, role: str):
    """Реальный запуск: видимая сессия opencode TUI в текущей консоли."""
    from agents.session_worker import _opencode_base

    def _run(c, prompt: str) -> int:
        import subprocess

        from pipeline.proc import no_window_flags
        cmd = _opencode_base() + [str(c.root), "--prompt", prompt]
        print(f"[tui] opencode-сессия запущена ({c.name}, {len(prompt)} символов "
              f"промпта) — работайте в окне; выход завершит порцию")
        return subprocess.call(cmd, cwd=str(c.root),
                               creationflags=no_window_flags())
    return _run


def run_cycle(cfg, *, role: str = "executor", user_prompt: str = "",
              runner=None, log=print) -> int:
    """Цикл порций. Возвращает число выполненных сессий.

    Стоп: нет handoff после порции (агент решил, что продолжать нечего),
    rc != 0 (падение сессии), лимит restart_policy.max_restarts + 1."""
    if runner is None:
        runner = default_runner(cfg, role)
    base = build_base_prompt(cfg, role, user_prompt)
    limit = int(getattr(cfg, "restart_max", 3)) + 1
    prompt = base
    done = 0
    for i in range(limit):
        started = time.time()
        rc = runner(cfg, prompt)
        done += 1
        if rc != 0:
            log(f"[tui] порция {i + 1}: сессия завершилась с rc={rc} — цикл окончен")
            break
        h = newest_handoff(cfg, started - 1)
        if h is None:
            log(f"[tui] порция {i + 1}: handoff нет — агент считает работу "
                f"завершённой, цикл окончен")
            break
        body = h.read_text(encoding="utf-8", errors="replace")[-HANDOFF_TAIL_LIMIT:]
        prompt = (base + "\n\nHANDOFF ПРЕДЫДУЩЕЙ СЕССИИ (продолжи с этого места):\n"
                  + body)
        log(f"[tui] порция {i + 1} готова, handoff: {h.name} — новая чистая "
            f"сессия (/new)")
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agents.tui_cycle",
                                 description="Автономный терминал агента "
                                             "(цикл порций, /new-эквивалент)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--role", default="executor")
    ap.add_argument("--prompt", default="",
                    help="стартовое задание (иначе env PIPELINE_TUI_PROMPT)")
    a = ap.parse_args(argv)

    from pipeline.config import ConfigError, load_config
    try:
        cfg = load_config(a.project)
    except ConfigError as e:
        print(f"[tui] проект не найден: {e}")
        return 2
    try:
        from pipeline.crew import ensure_permissions
        perm = ensure_permissions(cfg)
        print("[tui] права opencode: "
              + (f"шаблон создан: {perm}" if perm else f"уже есть ({cfg.crew_permissions})"))
    except Exception as e:
        print(f"[tui] профиль прав не проверён: {e}")

    prompt = a.prompt or __import__("os").environ.get("PIPELINE_TUI_PROMPT", "")
    n = run_cycle(cfg, role=a.role, user_prompt=prompt)
    print(f"[tui] цикл завершён: сессий {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
