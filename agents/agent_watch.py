# -*- coding: utf-8 -*-
"""Агент-1 (контролёр): автомат dispatch + verify через сервер.

Цикл:
  1. Подписка на SSE-канал 'controller'.
  2. На событие report_done/blocked — запустить verify A-NN (механическая проверка
     + сборка + тесты + grep-проверки -> вердикт). Публикует событие verdict.
  3. Файлы из Входящие\\ -> dispatch (если watch_dispatch=True).

Фолбэк: сервер недоступен -> файловый поллинг (как v1 pipeline.py watch).

Запуск: python -m agents.agent_watch --project HeatLossRevit2
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.client import Client                 # noqa: E402
from pipeline.config import load_config             # noqa: E402
from pipeline.models import Task                    # noqa: E402
from pipeline.cli import cmd_dispatch, cmd_verify   # noqa: E402
from agents.agent_manager import _kill_tree, _pid_alive  # noqa: E402
import argparse as _ap                              # noqa: E402

_STALL_TIMEOUT = int(os.environ.get("TASK_STALL_TIMEOUT_SEC", "10800"))
# Сирота-субагент: PID-файл старше этого возраста при живом процессе.
# Менеджер сам убивает субагента через SUBAGENT_TIMEOUT (1800 с), но если
# менеджер убит/завис — PID-файл остаётся, а процесс-сирота висит.
_SUBAGENT_MAX_AGE = int(os.environ.get("SUBAGENT_MAX_AGE_SEC", "3600"))


def _verify_task(cfg, tid: str):
    """verify: отчёт есть, вердикта нет -> механическая проверка (Markdown)."""
    reports = cfg.abs_tasks_dir("reports")
    has_report = bool(glob.glob(str(reports / (tid + "_Отчёт_*"))))
    has_verdict = bool(glob.glob(str(reports / (tid + "_Вердикт_*"))))
    if has_report and not has_verdict:
        cmd_verify(cfg, _ap.Namespace(task=tid))
        return True
    return False


def stalled_dir(cfg) -> Path:
    return cfg.root / "Tasks" / "Конвейер" / "stalled"


def _is_stalled_marked(cfg, tid: str) -> bool:
    return (stalled_dir(cfg) / f"{tid}.txt").exists()


def _stalled_marker(cfg, tid: str, details: str):
    """Однократный маркер зависания: Tasks\\Конвейер\\stalled\\<tid>.txt."""
    try:
        if _is_stalled_marked(cfg, tid):
            return
        import datetime
        stalled_dir(cfg).mkdir(parents=True, exist_ok=True)
        (stalled_dir(cfg) / f"{tid}.txt").write_text(
            f"{datetime.datetime.now().isoformat(timespec='seconds')}\n{details}\n",
            encoding="utf-8")
        print(f"[watch] {tid}: пометка task_stalled — {details}")
    except Exception as e:
        print(f"[watch] stalled-пометка {tid} не сохранена: {e}")


def check_stalled(cfg, client, timeout_sec: int = 10800) -> int:
    """Найти зависшие задачи: in_progress дольше timeout_sec без отчёта.

    Каждая задача помечается однократно маркером
    Tasks\\Конвейер\\stalled\\<tid>.txt и публикуется событие task_stalled.
    Возвращает количество вновь помеченных задач."""
    import datetime
    active = cfg.abs_tasks_dir("active")
    if not active.is_dir():
        return 0
    now = time.time()
    stalled = []
    for f in sorted(active.glob("A-*.md")):
        try:
            t = Task.from_file(f)
        except Exception:
            continue
        if t.status != "in_progress":
            continue
        tid = t.id
        if glob.glob(str(cfg.abs_tasks_dir("reports") / (tid + "_Отчёт_*"))):
            continue  # отчёт есть — не завис
        if _is_stalled_marked(cfg, tid):
            continue  # уже помечена
        age = now - f.stat().st_mtime
        if age < timeout_sec:
            continue
        _stalled_marker(cfg, tid,
                        f"Задача в работе {int(age // 3600)} ч без отчёта/прогресса "
                        f"(порог {timeout_sec // 3600} ч).")
        stalled.append(tid)
    for tid in stalled:
        print(f"[watch] ЗАВИСАНИЕ: {tid} — нет отчёта дольше {timeout_sec // 3600} ч")
        if client is not None:
            try:
                client.notify("task_stalled", to="controller", task=tid,
                              payload={"reason": "no report/progress",
                                       "timeout_sec": timeout_sec})
            except Exception:
                pass
    return len(stalled)


def clear_stalled(cfg, tid: str):
    """Снять пометку зависания (после редиспатча)."""
    try:
        (stalled_dir(cfg) / f"{tid}.txt").unlink(missing_ok=True)
    except Exception:
        pass


def check_subagent_zombies(cfg, client, max_age_sec: int = 3600) -> int:
    """Найти и убить зависшие процессы субагентов (PID-файлы менеджера).

    Менеджер пишет Tasks\\Конвейер\\logs\\{task_id}.pid (строка 1 — PID,
    строка 2 — время старта, unix). Если файл старше max_age_sec и процесс
    жив — это сирота (менеджер убит/завис, субагент продолжает висеть):
    убиваем дерево процесса, удаляем файл, помечаем задачу task_stalled.
    Мёртвые PID-файлы просто удаляются. Возвращает количество убитых."""
    logs_dir = cfg.root / "Tasks" / "Конвейер" / "logs"
    if not logs_dir.is_dir():
        return 0
    killed = 0
    for pf in sorted(logs_dir.glob("*.pid")):
        tid = pf.stem
        try:
            lines = pf.read_text(encoding="utf-8").strip().splitlines()
            pid = int(lines[0].strip())
            start = int(lines[1].strip()) if len(lines) > 1 else pf.stat().st_mtime
        except Exception:
            continue
        if time.time() - start < max_age_sec:
            continue  # ещё в пределах таймаута менеджера (1800 с) — он сам убьёт
        if not _pid_alive(pid):
            try:
                pf.unlink()  # мусор от убитого/завершённого субагента
            except Exception:
                pass
            continue
        _kill_tree(pid)
        killed += 1
        print(f"[watch] СИРОТА-СУБАГЕНТ: {tid} (PID {pid}) старше {max_age_sec} с — убит")
        try:
            pf.unlink()
        except Exception:
            pass
        _stalled_marker(cfg, tid,
                        f"субагент-сирота (PID {pid}) убит сторожем "
                        f"после {max_age_sec} с без результата")
        if client is not None:
            try:
                client.notify("task_stalled", to="controller", task=tid,
                              payload={"reason": "subagent zombie killed", "pid": pid})
            except Exception:
                pass
    return killed


def _stall_poll(cfg, client, stop, timeout_sec: int):
    """Фоновый детектор зависших задач и сирот-субагентов (в SSE-режиме)."""
    while not (stop and stop.is_set()):
        try:
            check_stalled(cfg, client, timeout_sec)
            check_subagent_zombies(cfg, client, _SUBAGENT_MAX_AGE)
        except Exception as e:
            print(f"[watch] stall-поллинг ошибка: {e}")
        time.sleep(60)


def file_polling_loop(cfg, client, stop):
    """Фолбэк: поллинг Входящие (dispatch) и отчётов без вердиктов (verify)."""
    while not (stop and stop.is_set()):
        try:
            for f in sorted(glob.glob(str(cfg.abs_tasks_dir("inbox") / "*"))):
                p = Path(f)
                if p.name.startswith("_"):
                    continue
                try:
                    cmd_dispatch(cfg, _ap.Namespace(file=str(p), title=None, priority=None,
                                                    requirements=None, result=None,
                                                    remark=None, id=None))
                except Exception as e:
                    print(f"[watch] dispatch {p.name} ошибка: {e}")
            # Markdown-задачи: отчёт без вердикта -> verify
            for f in sorted(glob.glob(str(cfg.abs_tasks_dir("active") / "A-*.md"))):
                tid = os.path.basename(f).split("_")[0]
                try:
                    _verify_task(cfg, tid)
                except Exception as e:
                    print(f"[watch] verify {tid} ошибка: {e}")
            # зависшие in_progress-задачи и сироты-субагенты
            check_stalled(cfg, client, _STALL_TIMEOUT)
            check_subagent_zombies(cfg, client, _SUBAGENT_MAX_AGE)
        except Exception as e:
            print(f"[watch] поллинг ошибка: {e}")
        time.sleep(30)


def sse_loop(cfg, client, watch_dispatch):
    stop = threading.Event()

    def on_event(ev):
        etype = ev.get("type", "")
        if etype in ("report_done", "blocked"):
            tid = ev.get("task", "")
            if tid:
                print(f"[watch] отчёт по {tid} — запускаю verify")
                try:
                    _verify_task(cfg, tid)
                    client.notify("verdict", to="executor", task=tid,
                                  payload={"action": "check verdict file"})
                except Exception as e:
                    print(f"[watch] verify {tid} ошибка: {e}")
        elif etype == "agent_offline":
            print(f"[watch] зомби-агент: {ev.get('payload', {})}")
        elif etype == "message":
            # команда из чата dashboard -> ответ статусом
            text = (ev.get("text") or "").strip()
            src = ev.get("from") or "dashboard"
            print(f"[watch] команда от {src}: {text[:120]}")
            reply = f"[watch] принято: {text[:200]}"
            low = text.lower()
            if "статус" in low or "отчёт" in low or "ping" in low:
                try:
                    active = cfg.abs_tasks_dir("active")
                    tasks = sorted(active.glob("A-*.md")) if active.is_dir() else []
                    from pipeline.models import Task as _T
                    inprog = []
                    for f in tasks:
                        try:
                            if _T.from_file(Path(f)).status == "in_progress":
                                inprog.append(os.path.basename(f).split("_")[0])
                        except Exception:
                            continue
                    sd = stalled_dir(cfg)
                    stalled = [p.stem for p in sorted(sd.glob("*.txt"))] if sd.is_dir() else []
                    done = len(glob.glob(str(cfg.abs_tasks_dir("archive") / "A-*.md")))
                    reply += (f"\nСтатус {cfg.name}: активных={len(tasks)}, "
                              f"в работе={inprog or '—'}, зависших={stalled or '—'}, "
                              f"в архиве={done}")
                except Exception as e:
                    reply += f"\n(статистика недоступна: {e})"
            client.send_message(src, reply)
            client.ack(ev.get("id")) if ev.get("id") else None
        client.ack(ev.get("id")) if ev.get("id") else None

    client.heartbeat()
    if watch_dispatch:
        # фоновый поллинг Входящие (диспатч) даже при работающем сервере
        threading.Thread(target=_dispatch_poll, args=(cfg, stop), daemon=True).start()
    # фоновый детектор зависших задач (in_progress без отчёта)
    threading.Thread(target=_stall_poll, args=(cfg, client, stop, _STALL_TIMEOUT),
                     daemon=True).start()
    client.subscribe(on_event, stop_event=stop)


def _dispatch_poll(cfg, stop):
    while not (stop and stop.is_set()):
        try:
            for f in sorted(glob.glob(str(cfg.abs_tasks_dir("inbox") / "*"))):
                p = Path(f)
                if p.name.startswith("_"):
                    continue
                try:
                    cmd_dispatch(cfg, _ap.Namespace(file=str(p), title=None, priority=None,
                                                    requirements=None, result=None,
                                                    remark=None, id=None))
                except Exception as e:
                    print(f"[watch] dispatch {p.name} ошибка: {e}")
        except Exception as e:
            print(f"[watch] dispatch-поллинг ошибка: {e}")
        time.sleep(30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="heatlossrevit2")
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--watch-dispatch", action="store_true",
                    help="автоматически оформлять Входящие в задачи")
    ap.add_argument("--stall-timeout", type=int,
                    default=int(os.environ.get("TASK_STALL_TIMEOUT_SEC", "10800")),
                    help="порог зависания in_progress-задачи, сек (default 10800 = 3 ч)")
    ap.add_argument("--polling-only", action="store_true")
    a = ap.parse_args()

    global _STALL_TIMEOUT
    _STALL_TIMEOUT = a.stall_timeout

    cfg = load_config(a.project)
    client = Client("controller", project=cfg.name, base_url=a.url,
                    notif_dir=str(cfg.resolve(cfg.notif)))

    if a.polling_only or not client.server_alive():
        print(f"[watch] сервер недоступен — файловый поллинг ({cfg.root})")
        stop = threading.Event()
        file_polling_loop(cfg, client, stop)
    else:
        print(f"[watch] SSE-подписка ({a.url})")
        sse_loop(cfg, client, watch_dispatch=a.watch_dispatch)


if __name__ == "__main__":
    main()
