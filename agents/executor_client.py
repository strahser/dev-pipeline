# -*- coding: utf-8 -*-
"""Агент-2 (исполнитель): тонкий клиент сервера.

Цикл:
  1. Подписка на SSE-канал 'executor'.
  2. На событие task_assigned/instruction/fix_request — запустить opencode run
     с задачей из файла Tasks\\Активные\\A-NN_*.md.
  3. После выполнения — событие report_done контролёру + ACK исходного события.

Фолбэк: сервер недоступен -> обычный файловый поллинг Tasks\\Активные\\ (как v1).

Запуск: python -m agents.executor_client --project HeatLossRevit2
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.client import Client                 # noqa: E402
from pipeline.config import load_config             # noqa: E402
from pipeline.models import Task                    # noqa: E402
from pipeline.proc import no_window_flags           # noqa: E402

OPENCODE = os.environ.get("OPENCODE_CMD") or "opencode"


def run_opencode(cfg, task: Task) -> str:
    """Запускает opencode run для задачи; возвращает вывод."""
    prompt = (
        "Ты — исполнитель конвейера. Выполни задачу из файла: "
        f"{task.file}\n"
        "Протокол: Tasks\\00_Протокол_агентов.md. Отчёт — в Tasks\\Отчёты\\"
        f"{task.id}_Отчёт_<дата>.md по шаблону протокола.\n"
        "Доказательства — реальные выводы сборки/тестов/grep. Не выдумывай факты.\n"
        "В шапке задачи замени 'статус: in_progress' на 'статус: done_report'.\n"
        "Коммит: git commit -m 'agent/{task.id}: отчёт исполнителя'."
    )
    try:
        r = subprocess.run([OPENCODE, "run", prompt], cwd=str(cfg.root),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=7200,
                           creationflags=no_window_flags())
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return f"opencode run упал: {e}"


def take_task(cfg, task_id: str):
    """Взять задачу: open -> in_progress, вернуть Task.
    Если TDL-задача есть (JSON) со status open/workflow issued — берём её (переводим in_progress)."""
    # TDL JSON-задача (источник истины)
    if getattr(cfg, "tdl_enabled", True):
        try:
            from pipeline.tdl import store as tdl_store
            t = tdl_store.load_task(cfg, task_id)
            if t is not None and t.get("status") == "open":
                t["workflow_state"] = "in_progress"
                t["status_display"] = "Открыто"
                tdl_store.save_task(cfg, t)
                tdl_store.rebuild_index(cfg)
        except Exception as e:
            print(f"[executor] TDL-задача {task_id}: {e}")
    # legacy Markdown
    task_path = None
    for f in glob.glob(str(cfg.abs_tasks_dir("active") / (task_id + "_*.md"))):
        task_path = Path(f)
        break
    if not task_path:
        return None
    t = Task.from_file(task_path)
    if t.status != "open":
        return None
    t.set_status("in_progress")
    return t


def _current_tdl_task(cfg) -> str:
    """Кратко: какая TDL-задача сейчас в работе (workflow_state=in_progress)."""
    if not getattr(cfg, "tdl_enabled", True):
        return ""
    try:
        from pipeline.tdl import store as tdl_store
        ad = tdl_store.active_dir(cfg)
        if not ad.is_dir():
            return ""
        import json
        cur = []
        for f in sorted(ad.glob("*.task.json")):
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if t.get("workflow_state") == "in_progress":
                cur.append(f"{t.get('task_id')} {t.get('name', '')[:40]}")
        return "; ".join(cur) if cur else "нет задач в работе"
    except Exception:
        return ""


def _tdl_report_if_needed(cfg, task_id: str, md_report: Path):
    """Создать TDL JSON-отчёт из Markdown-отчёта (если TDL-задача есть)."""
    if not getattr(cfg, "tdl_enabled", True):
        return
    try:
        from pipeline.tdl import store as tdl_store, cli as tdl_cli
        import argparse as _ap
        if tdl_store.load_task(cfg, task_id) and md_report.exists():
            tdl_cli.tdl_report(cfg, _ap.Namespace(task=task_id, final=False,
                                                   from_md=str(md_report)))
    except Exception as e:
        print(f"[executor] TDL-отчёт {task_id}: {e}")


def file_polling_loop(cfg, client, stop):
    """Фолбэк: файловый поллинг задач со статусом open (сервер недоступен)."""
    while not (stop and stop.is_set()):
        try:
            for f in sorted(glob.glob(str(cfg.abs_tasks_dir("active") / "A-*.md"))):
                t = take_task(cfg, os.path.basename(f).split("_")[0])
                if t is None:
                    continue
                handle_task(cfg, client, t, via_event=False)
        except Exception as e:
            print(f"[executor] поллинг ошибка: {e}")
        time.sleep(30)


def handle_task(cfg, client, task: Task, via_event: bool, ack_id=None):
    print(f"[executor] выполняю {task.id} ({task.file.name})")
    out = run_opencode(cfg, task)
    report = cfg.abs_tasks_dir("reports") / f"{task.id}_Отчёт_{time.strftime('%Y-%m-%d')}.md"
    if report.exists() and report.stat().st_size > 200:
        print(f"[executor] отчёт готов: {report}")
        _tdl_report_if_needed(cfg, task.id, report)
        client.notify("report_done", to="controller", task=task.id,
                      payload={"report": report.name, "status": "done_report"})
    else:
        print(f"[executor] отчёт {task.id} не создан; возвращаю в open")
        task.set_status("open")
        client.notify("blocked", to="controller", task=task.id,
                      payload={"note": "отчёт не создан", "log_tail": out[-500:]})
    if ack_id is not None:
        client.ack(ack_id)


def sse_loop(cfg, client):
    """Основной цикл: SSE-подписка; при обрыве — reconnect (в subscribe)."""
    stop = threading.Event()

    def on_event(ev):
        etype = ev.get("type", "")
        task_id = ev.get("task", "")
        if etype in ("task_assigned", "instruction", "fix_request") and task_id:
            t = take_task(cfg, task_id)
            if t is None:
                print(f"[executor] {task_id} не взята (нет или статус != open)")
                client.ack(ev.get("id"))  # всё равно подтвердим доставку
                return
            handle_task(cfg, client, t, via_event=True, ack_id=ev.get("id"))
        elif etype == "message":
            # команда из чата dashboard -> ответим статусом
            text = (ev.get("text") or "").strip()
            src = ev.get("from") or "dashboard"
            print(f"[executor] команда от {src}: {text[:120]}")
            reply = f"[executor] принято: {text[:200]}"
            cur = _current_tdl_task(cfg)
            if cur:
                reply += f"\nСейчас: {cur}"
            client.send_message(src, reply)
            client.ack(ev.get("id")) if ev.get("id") else None

    # heartbeat в фоне
    client.heartbeat()
    client.subscribe(on_event, stop_event=stop)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="heatlossrevit2")
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--polling-only", action="store_true",
                    help="только файловый поллинг (без сервера)")
    a = ap.parse_args()

    cfg = load_config(a.project)
    client = Client("executor", project=cfg.name, base_url=a.url,
                    notif_dir=str(cfg.resolve(cfg.notif)))

    if a.polling_only or not client.server_alive():
        print(f"[executor] сервер недоступен — файловый поллинг ({cfg.root})")
        stop = threading.Event()
        client.heartbeat() if client.server_alive() else None
        file_polling_loop(cfg, client, stop)
    else:
        print(f"[executor] SSE-подписка ({a.url})")
        sse_loop(cfg, client)


if __name__ == "__main__":
    main()
