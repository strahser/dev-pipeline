# -*- coding: utf-8 -*-
"""ОБЩИЙ менеджер: видимая терминальная сессия НА ВСЕ проекты (вместо
локального контролёра; обратная связь владельца 2026-08-23).

Что делает в цикле (TICK_SEC):
1. ВОССТАНОВЛЕНИЕ: опрашивает сервер — упавшие (failed/stalled) сессии проектов
   перезапускает, порции с handoff продолжает (pipeline.crew.supervise_once,
   лимит restart_policy + cooldown). Это «разрешение восстанавливать сессию,
   если упала».
2. ПРИЁМКА РАБОТЫ: задачи Tasks\Активные со статусом done_report и отчётом,
   но БЕЗ вердикта, проходят механический verify (pipeline.cli.cmd_verify) —
   принимает работу, когда контролёр-агент не запущен.

Сами агенты (tui_cycle) перезагружают себя сами: порция завершена -> handoff ->
свежая сессия opencode (/new). Менеджер только страхует падения и принимает.

Запуск из панели: кнопка «🛡 Менеджер» в шапке -> POST /api/chat/agents/terminal
(role=manager, без проекта = все проекты). Вручную:
    python -X utf8 agents/project_manager.py                # все проекты
    python -X utf8 agents/project_manager.py --project <p>  # один проект
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TICK_SEC_DEFAULT = 30


def _task_status(head: str) -> str:
    for line in head.splitlines():
        s = line.strip()
        if s.startswith("статус:"):
            return s.split(":", 1)[1].strip()
    return ""


def accept_pending_work(cfg, log=print) -> list[str]:
    """Механический вердикт для задач done_report без вердикта. Возвращает id."""
    import glob

    reports_dir = cfg.abs_tasks_dir("reports")
    active = cfg.abs_tasks_dir("active")
    accepted: list[str] = []
    if not active.is_dir():
        return accepted
    for tf in sorted(active.glob("*_*.md")):
        tid = tf.name.split("_", 1)[0]
        if not tid:                       # пропустить служебные файлы
            continue
        try:
            head = tf.read_text(encoding="utf-8", errors="replace")[:800]
        except OSError:
            continue
        if _task_status(head) != "done_report":
            continue
        if glob.glob(str(reports_dir / f"{tid}_Вердикт_*")):
            continue                      # уже принят/отклонён
        if not glob.glob(str(reports_dir / f"{tid}_Отчёт_*")):
            continue                      # статуса нет — вердикт строить не по чему
        log(f"[manager] {tid}: работа без вердикта — принимаю (механический verify)")
        try:
            from pipeline.cli import cmd_verify
            import argparse as _ap
            rc = cmd_verify(cfg, _ap.Namespace(task=tid))
            log(f"[manager] {tid}: вердикт записан "
                f"({'PASS' if rc == 0 else 'FAIL/PARTIAL'})")
            accepted.append(tid)
        except SystemExit as e:           # cmd_verify может завершиться кодом
            log(f"[manager] {tid}: verify завершился ({e})")
            accepted.append(tid)
        except Exception as e:
            log(f"[manager] {tid}: ошибка verify: {e}")
    return accepted


def _spawner(cfg):
    """Поднимает session_worker для восстановленной сессии."""
    def spawn(session):
        import subprocess

        from pipeline.proc import no_window_flags
        worker = Path(__file__).resolve().parent.parent / "agents" / \
            "session_worker.py"
        subprocess.Popen([sys.executable, "-X", "utf8", str(worker),
                          "--session", session["id"], "--project", cfg.name],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=no_window_flags())
    return spawn


def manage_once(cfg, client, counters, *, spawner=None, log=print):
    """Один проход менеджера. Возвращает (восстановления[], принятые[])."""
    restored: list[str] = []
    if client is not None:
        try:
            from pipeline.crew import supervise_once
            decisions = supervise_once(cfg, client, counters,
                                       spawn=spawner or _spawner(cfg))
            for d in decisions:
                if d["action"] == "restart":
                    restored.append(d.get("task") or d["sid"])
                    log(f"[manager] восстановление сессии {d['sid']} "
                        f"({d.get('task') or '—'}) -> {d.get('new_sid', '')}")
                elif d["action"] == "exhausted":
                    log(f"[manager] {d['sid']}: лимит рестартов исчерпан "
                        f"(нужно вмешательство)")
                elif d["action"] == "cooldown":
                    log(f"[manager] {d['sid']}: cooldown до следующего прохода")
        except Exception as e:
            log(f"[manager] supervise недоступен: {e}")
    else:
        log("[manager] сервер недоступен — восстановление сессий пропущено, "
            "работает только приёмка")
    accepted = accept_pending_work(cfg, log=log)
    return restored, accepted


def run_manager(projects, client, counters, *, spawner=None, log=print):
    """Один проход менеджера по СПИСКУ проектов (общий менеджер на все проекты).
    Возвращает [(имя проекта, восстановления[], принятые[])]."""
    out = []
    for cfg in projects:
        try:
            restored, accepted = manage_once(cfg, client, counters,
                                             spawner=spawner, log=log)
            if restored or accepted:
                out.append((cfg.name, restored, accepted))
        except Exception as e:
            log(f"[manager] {cfg.name}: ошибка прохода: {e}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agents.project_manager",
                                 description="ОБЩИЙ менеджер: восстановление сессий + "
                                             "приёмка работы (по всем проектам или одному)")
    ap.add_argument("--project", default="",
                    help="один проект (пусто = ВСЕ проекты из examples/)")
    ap.add_argument("--interval", type=int, default=TICK_SEC_DEFAULT,
                    help="период цикла, сек")
    a = ap.parse_args(argv)

    from pipeline.config import ConfigError, list_projects, load_config
    names = [a.project] if a.project else list_projects()
    cfgs = []
    for name in names:
        try:
            cfgs.append(load_config(name))
        except ConfigError as e:
            print(f"[manager] {name} пропущен: {e}")
    if not cfgs:
        print("[manager] нет ни одного валидного проекта — нечего вести")
        return 2

    for cfg in cfgs:
        try:
            from pipeline.crew import ensure_permissions
            ensure_permissions(cfg)
        except Exception:
            pass

    from pipeline.client import Client
    client = Client("manager", project="")
    if not client.server_alive():
        print("[manager] сервер не отвечает — восстановление сессий недоступно, "
              "работает только приёмка; Ctrl+C — выход")
        client = None

    counters: dict = {}
    scope = a.project or f"все проекты ({len(cfgs)})"
    print(f"[manager] ОБЩИЙ менеджер запущен: {scope}; приёмка работы + "
          f"восстановление сессий каждые {a.interval} с. Ctrl+C — стоп.")
    try:
        while True:
            run_manager(cfgs, client, counters)
            time.sleep(max(10, a.interval))
    except KeyboardInterrupt:
        print("\n[manager] остановлен владельцем")
        return 0


if __name__ == "__main__":
    sys.exit(main())
