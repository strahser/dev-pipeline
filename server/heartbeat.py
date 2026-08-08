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
import os


async def zombie_watchdog(store, hub, check_interval_sec: int = 30,
                          max_age_sec: int = 90, controller: str = "controller",
                          session_max_age_sec: int = 300):
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
