# -*- coding: utf-8 -*-
"""Агент-3 (браузерный мост): подписка на канал 'browser'.

На событие browser_task — отправляет промпт в облачный ИИ (LocalAssitent:
Qwen/DeepSeek через chat.qwen.ai, Edge порт 9222) и сохраняет ответ в файл.

Фолбэк: сервер недоступен -> поллинг Tasks\\Конвейер\\Браузер\\*.txt (как v1).

Запуск: python -m agents.browser_client --project HeatLossRevit2
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.client import Client               # noqa: E402
from pipeline.config import load_config           # noqa: E402

QWEN_DIR = r"E:\ПлагиныРевит\LocalAssitent"
PROVIDER = "qwen"


def run_cloud(source_path: str, output_path: str) -> str:
    """Отправить файл-промпт в облачный ИИ и сохранить ответ в output_path."""
    cmd = ["python", "-X", "utf8", "-m", "tools.send_to_cloud", source_path,
           "--provider", PROVIDER, "--output", output_path]
    r = subprocess.run(cmd, cwd=QWEN_DIR, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    return (r.stdout or "") + (r.stderr or "")


def handle_browser_task(cfg, client, prompt_file: Path, ack_id=None):
    """Обработка задания Агенту-3: файл-промпт, первая строка — путь сохранения."""
    lines = prompt_file.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        print(f"[browser] пустой файл задания: {prompt_file.name}")
        return
    output_rel = lines[0].strip()
    output_path = (cfg.root / output_rel) if not os.path.isabs(output_rel) else Path(output_rel)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[browser] отправка в {PROVIDER}: {prompt_file.name} -> {output_path}")
    out = run_cloud(str(prompt_file), str(output_path))
    ok = output_path.exists() and output_path.stat().st_size > 200
    print(f"[browser] ответ {'сохранён' if ok else 'НЕ сохранён'} ({len(out)} байт лога)")
    client.notify("browser_done" if ok else "browser_partial", to="controller",
                  payload={"source": prompt_file.name, "output": str(output_path),
                           "log_tail": out[-300:]})
    if ack_id is not None:
        client.ack(ack_id)


def file_polling_loop(cfg, client, stop):
    browser_dir = cfg.resolve(cfg.conveyor) / "Браузер"
    history = browser_dir / "_история"
    history.mkdir(parents=True, exist_ok=True)
    while not (stop and stop.is_set()):
        try:
            for f in sorted(glob.glob(str(browser_dir / "*.txt"))):
                p = Path(f)
                if p.name.startswith("_"):
                    continue
                print(f"[browser] задание: {p.name}")
                handle_browser_task(cfg, client, p)
                shutil.move(str(p), str(history / p.name))
        except Exception as e:
            print(f"[browser] поллинг ошибка: {e}")
        time.sleep(30)


def sse_loop(cfg, client):
    stop = threading.Event()

    def on_event(ev):
        if ev.get("type") == "browser_task":
            path = ev.get("payload", {}).get("path", "")
            if path:
                p = cfg.resolve(path)
                if p.exists():
                    handle_browser_task(cfg, client, p, ack_id=ev.get("id"))
                    return
            print(f"[browser] задание без файла: {ev}")
            client.ack(ev.get("id")) if ev.get("id") else None

    client.heartbeat()
    client.subscribe(on_event, stop_event=stop)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="heatlossrevit2")
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--polling-only", action="store_true")
    a = ap.parse_args()

    cfg = load_config(a.project)
    client = Client("browser", project=cfg.name, base_url=a.url,
                    notif_dir=str(cfg.resolve(cfg.notif)))

    if a.polling_only or not client.server_alive():
        print(f"[browser] сервер недоступен — файловый поллинг ({cfg.root})")
        stop = threading.Event()
        file_polling_loop(cfg, client, stop)
    else:
        print(f"[browser] SSE-подписка ({a.url})")
        sse_loop(cfg, client)


if __name__ == "__main__":
    main()
