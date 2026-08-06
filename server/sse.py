# -*- coding: utf-8 -*-
"""SSE hub: in-process pub/sub для рассылки событий подписчикам (агентам и dashboard).

Подписчик открывает GET /events/stream?agent=<имя>&last_event_id=<id>.
При поступлении нового события hub кладёт его в asyncio.Queue подписчика,
если адресат (to) совпадает с каналом подписчика или это 'feed' (лента для всех).
"""
from __future__ import annotations

import asyncio
import json


class SSEHub:
    def __init__(self):
        self._subs: dict[str, asyncio.Queue] = {}

    def subscribe(self, agent: str) -> asyncio.Queue:
        if agent not in self._subs:
            self._subs[agent] = asyncio.Queue(maxsize=1000)
        return self._subs[agent]

    def unsubscribe(self, agent: str, q: asyncio.Queue) -> None:
        if self._subs.get(agent) is q:
            self._subs.pop(agent, None)

    def publish(self, event: dict) -> None:
        """Разослать событие подписчикам, которым оно адресовано (или всем — feed)."""
        payload = f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        for agent, q in list(self._subs.items()):
            if self._matches(agent, event):
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass  # медленный подписчик — пропускаем (иначе блокировка)

    @staticmethod
    def _matches(agent: str, event: dict) -> bool:
        to = event.get("to", "")
        # 'feed' рассылается всем; конкретному агенту — только его адресованные события
        return to == "feed" or to == agent

    def subscriber_count(self) -> int:
        return len(self._subs)
