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
            for f in sorted(glob.glob(str(cfg.abs_tasks_dir("active") / "A-*.md"))):
                tid = os.path.basename(f).split("_")[0]
                has_report = bool(glob.glob(str(cfg.abs_tasks_dir("reports") / (tid + "_Отчёт_*"))))
                has_verdict = bool(glob.glob(str(cfg.abs_tasks_dir("reports") / (tid + "_Вердикт_*"))))
                if has_report and not has_verdict:
                    try:
                        cmd_verify(cfg, _ap.Namespace(task=tid))
                    except Exception as e:
                        print(f"[watch] verify {tid} ошибка: {e}")
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
                    cmd_verify(cfg, _ap.Namespace(task=tid))
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
    ap.add_argument("--polling-only", action="store_true")
    a = ap.parse_args()

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
