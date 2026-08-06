# -*- coding: utf-8 -*-
"""FastAPI-приложение сервера координации dev-pipeline.

Запуск:
    python -m uvicorn server.app:app --host 127.0.0.1 --port 8787
    (или python -m server  — см. server/__main__.py)

Эндпоинты:
    POST  /events                    создать событие (публикуется в SSE)
    GET   /events/stream             SSE-лента (?agent=<имя>&last_event_id=<id>)
    POST  /events/{id}/ack           подтверждение доставки события
    GET   /events                    недавние события (?project=&limit=)
    GET   /messages                  inbox агента (?agent=&undelivered=)
    POST  /messages                  отправить сообщение
    POST  /messages/{id}/ack         подтверждение доставки сообщения
    POST  /heartbeat                 сердцебиение агента {agent}
    GET   /agents                    список агентов
    GET   /api/stats                 сводка для dashboard
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline.config import ConfigError, load_config, list_projects
from pipeline.models import Task
from server.db import Store
from server.heartbeat import start_watchdog
from server.sse import SSEHub

DB_PATH = os.environ.get("PIPELINE_DB", "conveyor.db")
DASHBOARD = Path(__file__).parent / "static" / "dashboard.html"


class EventIn(BaseModel):
    type: str
    from_: str = Field(alias="from")
    to: str
    project: str = ""
    task: str = ""
    payload: dict = {}


class MessageIn(BaseModel):
    from_: str = Field(alias="from")
    to: str
    text: str


class HeartbeatIn(BaseModel):
    agent: str


store = Store(DB_PATH)
hub = SSEHub()
watchdog = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global watchdog
    watchdog = start_watchdog(store, hub)
    yield
    store.close()
    if watchdog:
        watchdog.cancel()


app = FastAPI(title="dev-pipeline coordinator", lifespan=lifespan)


def _sse(event: dict) -> str:
    import json as _j
    return f"event: {event['type']}\ndata: {_j.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/events")
async def post_event(body: EventIn):
    ev = store.add_event(body.type, body.from_, body.to, body.project,
                         body.task, body.payload)
    hub.publish(ev)
    return ev


@app.get("/events/stream")
async def events_stream(request: Request, agent: str = "feed",
                        last_event_id: int = 0):
    q = hub.subscribe(agent)
    # Отметить адресованные события как delivered (до ACK)
    if agent != "feed":
        store.mark_events_delivered(agent)

    async def gen():
        try:
            # восстановление пропущенного (reconnect по Last-Event-ID)
            for ev in store.undelivered_events(agent):
                if last_event_id and ev["id"] <= last_event_id:
                    continue
                yield _sse(ev)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield item
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            hub.unsubscribe(agent, q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/events/{event_id}/ack")
async def ack_event(event_id: int):
    if not store.ack_event(event_id):
        raise HTTPException(404, "событие не найдено или уже подтверждено")
    return {"ok": True, "id": event_id}


@app.get("/events")
async def recent_events(project: str = "", limit: int = 200):
    return store.recent_events(limit=min(limit, 1000), project=project)


@app.get("/messages")
async def inbox(agent: str, undelivered: bool = False):
    return store.inbox_messages(agent, undelivered=undelivered)


@app.post("/messages")
async def post_message(body: MessageIn):
    msg = store.add_message(body.from_, body.to, body.text)
    hub.publish({"id": msg["id"], "type": "message", "from": msg["from"],
                 "to": msg["to"], "text": msg["text"],
                 "created_at": msg["created_at"], "delivery": msg["delivery"],
                 "payload": {}})
    return msg


@app.post("/messages/{msg_id}/ack")
async def ack_message(msg_id: int):
    if not store.ack_message(msg_id):
        raise HTTPException(404, "сообщение не найдено или уже подтверждено")
    return {"ok": True, "id": msg_id}


@app.post("/heartbeat")
async def heartbeat(body: HeartbeatIn):
    store.heartbeat(body.agent)
    return {"ok": True, "agent": body.agent}


@app.get("/agents")
async def agents():
    return store.agents()


@app.get("/api/stats")
async def stats():
    return {
        "db": store.stats(),
        "subscribers": hub.subscriber_count(),
        "agents": store.agents(),
    }


# --- Панель (dashboard): чтение Tasks проекта (источник правды) -----------

def _tasks_snapshot(project: str) -> dict:
    cfg = load_config(project)
    out = {}
    for key, label in [("inbox", "Входящие"), ("active", "Активные"), ("archive", "Архив")]:
        d = cfg.abs_tasks_dir(key)
        items = []
        if d.is_dir():
            for f in sorted(os.listdir(d)):
                if f.startswith("A-"):
                    try:
                        t = Task.from_file(d / f)
                        items.append({"file": f, "status": t.status, "priority": t.priority})
                    except Exception:
                        items.append({"file": f, "status": "?", "priority": ""})
        out[key] = items
    return out


@app.get("/api/tasks")
async def api_tasks(project: str = ""):
    project = project or (list_projects() or ["_test"])[0]
    try:
        return _tasks_snapshot(project)
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")


@app.get("/api/verdicts")
async def api_verdicts(project: str = "", limit: int = 20):
    project = project or (list_projects() or ["_test"])[0]
    try:
        cfg = load_config(project)
        rd = cfg.abs_tasks_dir("reports")
        files = sorted((p.name for p in rd.glob("*_Вердикт_*")), reverse=True) if rd.is_dir() else []
        return files[:limit]
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")


@app.get("/api/ledger")
async def api_ledger(limit: int = 200, project: str = ""):
    return store.recent_events(limit=min(limit, 500), project=project)


@app.get("/api/activity")
async def api_activity(limit: int = 50, project: str = ""):
    """Человекочитаемая лента «что происходит» для панели."""
    return store.activity(limit=min(limit, 200), project=project)


@app.get("/api/projects")
async def api_projects():
    return list_projects()


@app.get("/api/inbox")
async def api_inbox(limit: int = 200):
    msgs = []
    for ag in store.agents():
        msgs += store.inbox_messages(ag["name"])
    msgs.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return msgs[:limit]


@app.get("/")
async def index():
    from fastapi.responses import FileResponse
    if DASHBOARD.exists():
        return FileResponse(DASHBOARD, media_type="text/html; charset=utf-8")
    return {"error": "dashboard.html не найден"}


@app.get("/dashboard")
async def dashboard_page():
    from fastapi.responses import FileResponse
    if DASHBOARD.exists():
        return FileResponse(DASHBOARD, media_type="text/html; charset=utf-8")
    raise HTTPException(404, "dashboard.html не найден")


@app.get("/healthz")
async def healthz():
    return {"ok": True}
