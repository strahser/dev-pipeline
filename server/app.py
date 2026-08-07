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


@app.get("/api/plan")
async def api_plan(project: str = ""):
    """План проекта — таблица согласованного плана. Если TDL включён — из JSON index,
    иначе из legacy Markdown."""
    project = project or (list_projects() or ["_test"])[0]
    try:
        cfg = load_config(project)
        if cfg.tdl_enabled:
            from pipeline.tdl import store as tdl_store
            idx = tdl_store.load_index(cfg) or {"tasks": []}
            rows = []
            for t in idx.get("tasks", []):
                rows.append({
                    "id": t.get("task_id", ""),
                    "wbs": t.get("wbs_code", ""),
                    "title": t.get("name", ""),
                    "status": t.get("status", "open"),
                    "workflow_state": t.get("workflow_state", ""),
                    "документ": "",
                    "report_refs": t.get("report_refs", []),
                    "verdict_refs": t.get("verdict_refs", []),
                })
            done = [r for r in rows if r["status"] == "done"]
            return {
                "project": project, "tdl": True,
                "total": len(rows), "done": len(done),
                "rows": rows,
                "active": [r for r in rows if r["status"] != "done"],
                "archive": done,
                "working": _working_subagents(cfg),
            }
        rows = _plan_rows(cfg)
        active = [r for r in rows if r["status"] in ("open", "in_progress", "done_report", "rejected")]
        done = [r for r in rows if r["status"] in ("verified", "closed")]
        total = len(rows)
        return {
            "project": project, "tdl": False,
            "total": total, "done": len(done),
            "rows": rows, "active": active, "archive": done,
            "working": _working_subagents(cfg),
        }
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")


def _plan_rows(cfg) -> list:
    """Задачи проекта в виде строк плана-таблицы."""
    rows = []
    for d in ("active", "archive"):
        folder = cfg.abs_tasks_dir(d)
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("A-*.md"), key=lambda p: p.name):
            try:
                t = Task.from_file(f)
            except Exception:
                continue
            meta = t.meta
            report = _task_report(cfg, t.id)
            verdict = _task_verdict(cfg, t.id)
            rows.append({
                "id": t.id,
                "file": f.name,
                "title": meta.get("title", _title_from_file(f.name, t.id)),
                "status": t.status,
                "priority": t.priority or "средний",
                "начато": meta.get("дата", ""),
                "завершено": report.get("date", "") if report else "",
                "агент": meta.get("исполнитель", "subagent"),
                "документ": report.get("path", "") if report else "",
                "вердикт": verdict or "",
                "detail": report.get("detail", "") if report else "",
                "tests": report.get("tests", "") if report else "",
            })
    return rows


def _title_from_file(name: str, tid: str) -> str:
    rest = name[len(tid) + 1:-3] if name.startswith(tid) else name[:-3]
    return rest.replace("_", " ") or tid


def _task_report(cfg, tid: str) -> dict | None:
    """Последний отчёт задачи: путь, дата, деталь, тесты."""
    rd = cfg.abs_tasks_dir("reports")
    if not rd.is_dir():
        return None
    files = sorted(rd.glob(tid + "_Отчёт_*.md"))
    if not files:
        return None
    p = files[-1]
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    # Деталь: первая строка «Что сделано» (первые пункты)
    detail = _extract_section(txt, "Что сделано", 500)
    tests = _extract_tests(txt)
    # Дата отчёта из имени A-NN_Отчёт_YYYY-MM-DD.md
    import re as _re
    m = _re.search(r"Отчёт_(\d{4}-\d{2}-\d{2})", p.name)
    return {
        "path": str(p.relative_to(cfg.root)),
        "date": m.group(1) if m else "",
        "detail": detail or "",
        "tests": tests,
    }


def _task_verdict(cfg, tid: str) -> str:
    rd = cfg.abs_tasks_dir("reports")
    if not rd.is_dir():
        return ""
    files = sorted(rd.glob(tid + "_Вердикт_*.md"))
    if not files:
        return ""
    txt = files[-1].read_text(encoding="utf-8", errors="replace")
    import re as _re
    m = _re.search(r"\*\*(PASS|FAIL|PARTIAL|NEED_DATA)\*\*", txt)
    return m.group(1) if m else ""


def _extract_section(txt: str, heading: str, limit: int = 500) -> str:
    import re as _re
    m = _re.search(rf"##\s*{heading}\s*\n(.*?)(?=\n##\s|\Z)", txt, _re.S)
    if not m:
        return ""
    body = m.group(1).strip()
    return body[:limit] + ("…" if len(body) > limit else "")


def _extract_tests(txt: str) -> str:
    import re as _re
    m = _re.search(r"(?:тест[а-я]*|Пройдено|passed)[^\n]{0,80}\d+/\d+", txt, _re.I)
    return m.group(0).strip() if m else ""


def _working_subagents(cfg) -> list:
    import datetime
    evs = store.recent_events(limit=200, project=cfg.name)
    started: dict[str, str] = {}
    finished: set[str] = set()
    for e in evs:
        tid = e.get("task") or ""
        if not tid:
            continue
        if e["type"] == "task_started":
            started[tid] = e["created_at"]
        elif e["type"] == "subagent_finished":
            finished.add(tid)
    cutoff = (datetime.datetime.now() - datetime.timedelta(minutes=30)).isoformat()
    return [{"task": tid, "since": ts} for tid, ts in started.items()
            if tid not in finished and ts >= cutoff]


@app.get("/api/task/{task_id}")
async def api_task(task_id: str, project: str = ""):
    """Детализация задачи: этапы (разбита/начато/агент/закончено/тест/проверено),
    полный отчёт и вердикт."""
    project = project or (list_projects() or ["_test"])[0]
    try:
        cfg = load_config(project)
        for d in ("active", "archive"):
            folder = cfg.abs_tasks_dir(d)
            if folder.is_dir():
                for f in folder.glob(task_id + "_*.md"):
                    t = Task.from_file(f)
                    meta = t.meta
                    report = _task_report(cfg, t.id)
                    verdict = _task_verdict(cfg, t.id)
                    # события задачи
                    evs = [e for e in store.recent_events(limit=500, project=project)
                           if (e.get("task") or "") == task_id]
                    return {
                        "id": t.id,
                        "file": f.name,
                        "title": meta.get("title", _title_from_file(f.name, t.id)),
                        "status": t.status,
                        "priority": t.priority or "средний",
                        "начато": meta.get("дата", ""),
                        "агент": meta.get("исполнитель", "subagent"),
                        "завершено": report.get("date", "") if report else "",
                        "документ": report.get("path", "") if report else "",
                        "вердикт": verdict,
                        "report_text": _report_text(cfg, t.id),
                        "verdict_text": _verdict_text(cfg, t.id),
                        "events": evs,
                    }
        raise HTTPException(404, f"задача {task_id} не найдена")
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")


def _report_text(cfg, tid: str) -> str:
    rd = cfg.abs_tasks_dir("reports")
    if not rd.is_dir():
        return ""
    files = sorted(rd.glob(tid + "_Отчёт_*.md"))
    if not files:
        return ""
    try:
        return files[-1].read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _verdict_text(cfg, tid: str) -> str:
    rd = cfg.abs_tasks_dir("reports")
    if not rd.is_dir():
        return ""
    files = sorted(rd.glob(tid + "_Вердикт_*.md"))
    if not files:
        return ""
    try:
        return files[-1].read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


async def _verdict_list(cfg) -> list:
    rd = cfg.abs_tasks_dir("reports")
    if not rd.is_dir():
        return []
    return sorted((p.name for p in rd.glob("*_Вердикт_*")), reverse=True)[:20]


@app.get("/api/documents")
async def api_documents(project: str = ""):
    """Документы на проверку: DXF-отчёты (Эксперт) и последние отчёты/вердикты."""
    project = project or (list_projects() or ["_test"])[0]
    try:
        cfg = load_config(project)
        docs = []
        # DXF в папке Эксперт
        exp = cfg.root / "Tasks" / "Эксперт"
        if exp.is_dir():
            for f in sorted(exp.glob("*.dxf"), key=lambda p: -p.stat().st_mtime):
                docs.append({"kind": "dxf", "name": f.name, "path": str(f.relative_to(cfg.root)),
                             "size": f.stat().st_size})
        # Последние отчёты
        rd = cfg.abs_tasks_dir("reports")
        if rd.is_dir():
            for f in sorted(rd.glob("*_Отчёт_*.md"), key=lambda p: -p.stat().st_mtime)[:10]:
                docs.append({"kind": "report", "name": f.name, "path": str(f.relative_to(cfg.root)),
                             "size": f.stat().st_size})
        return docs
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


# --- TDL (JSON как источник истины) ---

@app.get("/api/tdl/tasks")
async def api_tdl_tasks(project: str = "", status: str = "", workflow_state: str = "",
                        task_kind: str = ""):
    from pipeline.tdl import store as tdl_store
    project = project or (list_projects() or [""])[0]
    try:
        cfg = load_config(project)
    except ConfigError:
        return []
    idx = tdl_store.load_index(cfg) or {"tasks": []}
    out = []
    for t in idx.get("tasks", []):
        if status and t.get("status") != status:
            continue
        if workflow_state and t.get("workflow_state") != workflow_state:
            continue
        out.append(t)
    return out


@app.get("/api/tdl/task/{task_id}")
async def api_tdl_task(task_id: str, project: str = ""):
    from pipeline.tdl import store as tdl_store, render
    project = project or (list_projects() or [""])[0]
    try:
        cfg = load_config(project)
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")
    task = tdl_store.load_task(cfg, task_id)
    if not task:
        raise HTTPException(404, f"TDL-задача {task_id} не найдена")
    report = tdl_store.load_report(cfg, task_id)
    verdict = tdl_store.load_verdict(cfg, task_id)
    return {
        "task": task,
        "report": report,
        "verdict": verdict,
        "markdown": {
            "task_card": render.render_task_card(task),
            "report": render.render_report_md(report) if report else "",
            "verdict": render.render_verdict_md(verdict) if verdict else "",
        },
    }


@app.get("/api/tdl/index")
async def api_tdl_index(project: str = ""):
    from pipeline.tdl import store as tdl_store
    project = project or (list_projects() or [""])[0]
    try:
        cfg = load_config(project)
    except ConfigError:
        return {}
    return tdl_store.load_index(cfg) or {}


@app.get("/api/tdl/plan")
async def api_tdl_plan(project: str = ""):
    from pipeline.tdl import store as tdl_store
    project = project or (list_projects() or [""])[0]
    try:
        cfg = load_config(project)
    except ConfigError:
        return {"project": project, "tasks": []}
    idx = tdl_store.load_index(cfg) or {"tasks": []}
    tasks = idx.get("tasks", [])
    done = [t for t in tasks if t.get("status") == "done"]
    return {
        "project": project,
        "total": len(tasks), "done": len(done),
        "tasks": tasks,
    }


@app.get("/api/tdl/activity")
async def api_tdl_activity(limit: int = 50, project: str = ""):
    evs = store.recent_events(limit=min(limit, 200), project=project)
    return [{"type": e["type"], "task": e.get("task", ""), "created_at": e["created_at"],
             "from": e["from"]} for e in evs]


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
