# -*- coding: utf-8 -*-
"""Фоновая задача сервера: детектор зомби-агентов и зависших сессий субагентов.

Каждые check_interval_sec проверяет:
  - агентов: последний heartbeat старше max_age_sec -> помечает offline и
    публикует событие 'agent_offline' в канал контролёра (to=controller);
  - сессии: running/created с heartbeat старше session_max_age_sec -> помечает
    'stalled' и публикует 'session_stalled' (to=controller) для редиспатча.

Параметры переопределяются переменными окружения:
  PIPELINE_WATCH_INTERVAL   (сек, default 30)
  PIPELINE_WATCH_MAX_AGE    (сек, default 90)
  PIPELINE_SESSION_MAX_AGE  (сек, default 300)
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import time
from pathlib import Path


def find_checkpoint_orphans(load_cfg, names, now: float | None = None) -> list:
    """[(name, updated_iso, age_sec)] — раннер проекта в фазе checkpoint,
    но pending-файл пропал: ожидание стало невидимым для панели."""
    now = time.time() if now is None else now
    out = []
    for name in names:
        try:
            cfg = load_cfg(name)
            conv = Path(cfg.conveyor_dir())
            st = conv / "runner_state.json"
            if not st.is_file():
                continue
            state = json.loads(st.read_text(encoding="utf-8"))
            if state.get("phase") != "checkpoint":
                continue
            if any((conv / "checkpoints").glob("*.pending.json")):
                continue
            updated = str(state.get("updated", ""))
            age = now - datetime.datetime.fromisoformat(updated).timestamp()
            remind = int(getattr(cfg, "checkpoint_remind_sec", 600))
            if age < max(60, remind * 2):
                continue
            out.append((name, updated, int(age)))
        except Exception:
            continue
    return out


async def zombie_watchdog(store, hub, check_interval_sec: int = 30,
                          max_age_sec: int = 90, controller: str = "controller",
                          session_max_age_sec: int = 300):
    orphan_seen: dict[str, str] = {}
    while True:
        await asyncio.sleep(check_interval_sec)
        try:
            stale = store.stale_agents(max_age_sec)
            for name in stale:
                store.mark_offline(name)
                ev = store.add_event("agent_offline", "server", controller,
                                     payload={"agent": name, "reason": "heartbeat stale"})
                hub.publish(ev)
        except Exception:
            pass  # не роняем watchdog при ошибках БД
        try:
            for s in store.stale_sessions(session_max_age_sec):
                store.update_session(s["id"], status="stalled", note="heartbeat stale",
                                     error=f"нет heartbeat > {session_max_age_sec} с")
                ev = store.add_event("session_stalled", "server", controller,
                                     project=s.get("project", ""), task=s.get("task", ""),
                                     payload={"session_id": s["id"], "agent": s.get("agent", "")})
                hub.publish(ev)
        except Exception:
            pass
        try:
            from pipeline.config import list_projects as _projects
            from pipeline.config import load_config as _load
            for name, updated, age in find_checkpoint_orphans(
                    _load, _projects()):
                if orphan_seen.get(name) == updated:
                    continue  # одно событие на эпизод ожидания
                orphan_seen[name] = updated
                ev = store.add_event("checkpoint_orphan", "server", controller,
                                     project=name,
                                     payload={"phase_age_sec": age})
                hub.publish(ev)
        except Exception:
            pass


def start_watchdog(store, hub, check_interval_sec: int | None = None,
                   max_age_sec: int | None = None, session_max_age_sec: int | None = None):
    if check_interval_sec is None:
        check_interval_sec = int(os.environ.get("PIPELINE_WATCH_INTERVAL", "30"))
    if max_age_sec is None:
        max_age_sec = int(os.environ.get("PIPELINE_WATCH_MAX_AGE", "90"))
    if session_max_age_sec is None:
        session_max_age_sec = int(os.environ.get("PIPELINE_SESSION_MAX_AGE", "300"))
    return asyncio.create_task(
        zombie_watchdog(store, hub, check_interval_sec, max_age_sec,
                        session_max_age_sec=session_max_age_sec))
