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
from pipeline.cli import cmd_dispatch, cmd_verify   # noqa: E402
import argparse as _ap                              # noqa: E402

_STALL_TIMEOUT = int(os.environ.get("TASK_STALL_TIMEOUT_SEC", "10800"))


def _verify_tdl_or_legacy(cfg, tid: str):
    """verify: TDL (если включён и есть JSON-отчёт) или legacy Markdown."""
    if getattr(cfg, "tdl_enabled", True):
        try:
            from pipeline.tdl import store as tdl_store
            from pipeline.tdl import cli as tdl_cli
            task = tdl_store.load_task(cfg, tid)
            report = tdl_store.latest_report_path(cfg, tid)
            verdict = tdl_store.latest_verdict_path(cfg, tid)
            if task is not None and report is not None and verdict is None:
                tdl_cli.tdl_verify(cfg, _ap.Namespace(task=tid))
                return True
        except Exception as e:
            print(f"[watch] tdl-verify {tid} ошибка: {e}")
    # legacy Markdown
    reports = cfg.abs_tasks_dir("reports")
    has_report = bool(glob.glob(str(reports / (tid + "_Отчёт_*"))))
    has_verdict = bool(glob.glob(str(reports / (tid + "_Вердикт_*"))))
    if has_report and not has_verdict:
        cmd_verify(cfg, _ap.Namespace(task=tid))
        return True
    return False


def check_stalled(cfg, client, timeout_sec: int = 10800) -> int:
    """Найти зависшие TDL-задачи: in_progress дольше timeout_sec без отчёта.

    Каждая задача помечается в history один раз (action=stalled), публикуется
    событие task_stalled (to=controller) и печатается предупреждение.
    Возвращает количество вновь помеченных задач."""
    if not getattr(cfg, "tdl_enabled", True):
        return 0
    import datetime
    import json
    from pipeline.tdl import store as tdl_store
    from pipeline.tdl._tpl import _now_iso
    ad = tdl_store.active_dir(cfg)
    if not ad.is_dir():
        return 0
    now = datetime.datetime.now()
    stalled = []
    for tf in sorted(ad.glob("*.task.json")):
        try:
            t = json.loads(tf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if t.get("workflow_state") != "in_progress":
            continue
        tid = t.get("task_id", "")
        if tdl_store.latest_report_path(cfg, tid):
            continue  # отчёт есть — не завис
        start = (t.get("dates") or {}).get("start")
        if not start:
            continue
        try:
            st = datetime.datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            if st.tzinfo is not None:
                st = st.astimezone().replace(tzinfo=None)
        except ValueError:
            continue
        elapsed = (now - st).total_seconds()
        if elapsed < timeout_sec:
            continue
        hist = t.setdefault("history", [])
        if any(h.get("action") == "stalled" for h in hist):
            continue  # уже помечена
        hist.append({"timestamp": _now_iso(), "actor": "watch", "action": "stalled",
                     "details": f"Задача в работе {int(elapsed // 3600)} ч без отчёта/прогресса "
                                f"(порог {timeout_sec // 3600} ч)."})
        tdl_store.save_task(cfg, t)
        stalled.append(tid)
    if stalled:
        try:
            tdl_store.rebuild_index(cfg)
        except Exception:
            pass
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


def _stall_poll(cfg, client, stop, timeout_sec: int):
    """Фоновый детектор зависших задач (в SSE-режиме)."""
    while not (stop and stop.is_set()):
        try:
            check_stalled(cfg, client, timeout_sec)
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
            # TDL JSON-задачи (источник истины)
            if getattr(cfg, "tdl_enabled", True):
                try:
                    from pipeline.tdl import store as tdl_store
                    ad = tdl_store.active_dir(cfg)
                    if ad.is_dir():
                        for tf in sorted(ad.glob("*.task.json")):
                            import json
                            try:
                                t = json.loads(tf.read_text(encoding="utf-8"))
                            except Exception:
                                continue
                            tid = t.get("task_id")
                            if not tid:
                                continue
                            _verify_tdl_or_legacy(cfg, tid)
                except Exception as e:
                    print(f"[watch] tdl-поллинг ошибка: {e}")
            # legacy Markdown
            for f in sorted(glob.glob(str(cfg.abs_tasks_dir("active") / "A-*.md"))):
                tid = os.path.basename(f).split("_")[0]
                try:
                    _verify_tdl_or_legacy(cfg, tid)
                except Exception as e:
                    print(f"[watch] verify {tid} ошибка: {e}")
            # зависшие in_progress-задачи
            check_stalled(cfg, client, _STALL_TIMEOUT)
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
                    _verify_tdl_or_legacy(cfg, tid)
                    client.notify("verdict", to="executor", task=tid,
                                  payload={"action": "check verdict file"})
                except Exception as e:
                    print(f"[watch] verify {tid} ошибка: {e}")
        elif etype == "agent_offline":
            print(f"[watch] зомби-агент: {ev.get('payload', {})}")
        elif etype == "message":
            # сообщение адресовано контролёру — выводим
            print(f"[watch] сообщение: {ev.get('text', '')}")
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
