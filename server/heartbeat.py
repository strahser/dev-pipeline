# -*- coding: utf-8 -*-
"""Фоновая задача сервера: детектор зомби-агентов.

Каждые check_interval_sec проверяет агентов: последний heartbeat старше
max_age_sec -> помечает offline и публикует событие 'agent_offline' в канал
контролёра (to=controller).

Параметры переопределяются переменными окружения:
  PIPELINE_WATCH_INTERVAL (сек, default 30)
  PIPELINE_WATCH_MAX_AGE   (сек, default 90)
"""
from __future__ import annotations

import asyncio
import os


async def zombie_watchdog(store, hub, check_interval_sec: int = 30,
                          max_age_sec: int = 90, controller: str = "controller"):
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


def start_watchdog(store, hub, check_interval_sec: int | None = None,
                   max_age_sec: int | None = None):
    if check_interval_sec is None:
        check_interval_sec = int(os.environ.get("PIPELINE_WATCH_INTERVAL", "30"))
    if max_age_sec is None:
        max_age_sec = int(os.environ.get("PIPELINE_WATCH_MAX_AGE", "90"))
    return asyncio.create_task(
        zombie_watchdog(store, hub, check_interval_sec, max_age_sec))
