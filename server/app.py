# -*- coding: utf-8 -*-
"""FastAPI-приложение сервера координации dev-pipeline.

Запуск:
    python -X utf8 server/app.py [--port 8787]
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
    POST  /api/sessions              создать сессию субагента (явная сессия)
    GET   /api/sessions              список сессий (?project=&task=&status=)
    GET   /api/sessions/{id}         сессия (инструкция для session_worker)
    POST  /api/sessions/{id}/start   субагент взял сессию (pid/cmd)
    POST  /api/sessions/{id}/status  статус (done/failed + report/error)
    POST  /api/sessions/{id}/heartbeat
    POST  /api/sessions/{id}/instruction  контролёр -> SSE-канал session-<id>
    POST  /api/sessions/{id}/kill    убить процесс субагента (taskkill /T)
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # прямой запуск server/app.py

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline.config import ConfigError, load_config, list_projects
from pipeline.models import Task
from server.db import Store, now_iso as _now_iso
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
    project: str = ""
    pid: int | None = None
    cmd: str = ""


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

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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
    store.heartbeat(body.agent, project=body.project, pid=body.pid, cmd=body.cmd)
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

def _wbs_level(level, wbs_code: str) -> int:
    """Уровень вложения: явный level, иначе глубина WBS (2.1.1 -> 3)."""
    try:
        if level is not None:
            return int(level)
    except (TypeError, ValueError):
        pass
    return len([p for p in str(wbs_code).split(".") if p])


def _tdl_task_row(cfg, t: dict) -> dict:
    """Обогатить индексную строку TDL-задачи данными из JSON-файла задачи
    (module/class_name/layer/is_summary/task_kind/dates) + счётчиками из отчёта."""
    from pipeline.tdl import store as tdl_store
    task = tdl_store.load_task(cfg, t.get("task_id", "")) or {}
    report = tdl_store.load_report(cfg, t.get("task_id", ""))
    verdict = tdl_store.load_verdict(cfg, t.get("task_id", ""))
    evidence_count = 0
    if report:
        ev = report.get("evidence") or []
        evidence_count = sum(1 for e in ev if e.get("evidence_id"))
    return {
        "task_id": t.get("task_id", ""),
        "path": t.get("path", ""),
        "wbs_code": t.get("wbs_code", ""),
        "parent_wbs": t.get("parent_wbs", task.get("parent_wbs", "")) or "",
        "level": _wbs_level(t.get("level", task.get("level")), t.get("wbs_code", task.get("wbs_code", ""))),
        "is_summary": bool(task.get("is_summary", t.get("is_summary", False))),
        "task_kind": task.get("task_kind", t.get("task_kind", "")),
        "name": t.get("name", task.get("name", "")),
        "description": task.get("description", "") or t.get("description", "") or "",
        "status": t.get("status", "open"),
        "workflow_state": t.get("workflow_state", "issued"),
        "priority": t.get("priority", task.get("priority", "средний")),
        "module": task.get("module", t.get("module", "")) or "",
        "class_name": task.get("class_name", t.get("class_name", "")) or "",
        "layer": task.get("layer", t.get("layer", "")) or "",
        "dates": task.get("dates", {}) or {},
        "has_report": bool(t.get("report_refs")),
        "has_verdict": bool(t.get("verdict_refs")),
        "verdict_result": (verdict or {}).get("result") if verdict else None,
        "links_count": len(task.get("links", []) or []),
        "evidence_count": evidence_count,
    }


@app.get("/api/tdl/tasks")
async def api_tdl_tasks(project: str = "", status: str = "", workflow_state: str = "",
                        task_kind: str = "", module: str = "", class_name: str = "",
                        layer: str = "", is_summary: str = "", wbs: str = "",
                        q: str = "", has_report: str = "", has_verdict: str = ""):
    from pipeline.tdl import store as tdl_store
    project = project or (list_projects() or [""])[0]
    try:
        cfg = load_config(project)
    except ConfigError:
        return []
    idx = tdl_store.load_index(cfg) or {"tasks": []}
    out = []
    for t in idx.get("tasks", []):
        row = _tdl_task_row(cfg, t)
        if status and row["status"] != status:
            continue
        if workflow_state and row["workflow_state"] != workflow_state:
            continue
        if task_kind and row["task_kind"] != task_kind:
            continue
        if module and row["module"] != module:
            continue
        if class_name and row["class_name"] != class_name:
            continue
        if layer and row["layer"] != layer:
            continue
        if is_summary in ("true", "1") and not row["is_summary"]:
            continue
        if is_summary in ("false", "0") and row["is_summary"]:
            continue
        if wbs and wbs != row["wbs_code"]:
            continue
        if has_report in ("true", "1") and not row["has_report"]:
            continue
        if has_report in ("false", "0") and row["has_report"]:
            continue
        if has_verdict in ("true", "1") and not row["has_verdict"]:
            continue
        if has_verdict in ("false", "0") and row["has_verdict"]:
            continue
        if q:
            hay = " ".join([row["name"], row["task_id"], row["wbs_code"],
                            row["module"], row["class_name"]]).lower()
            if q.lower() not in hay:
                continue
        out.append(row)
    return out


@app.get("/api/tdl/filters")
async def api_tdl_filters(project: str = ""):
    """Списки значений для панели фильтров dashboard (модули/классы/слои/типы/статусы)."""
    from pipeline.tdl import store as tdl_store
    project = project or (list_projects() or [""])[0]
    try:
        cfg = load_config(project)
    except ConfigError:
        return {}
    idx = tdl_store.load_index(cfg) or {"tasks": []}
    statuses: dict[str, int] = {}
    workflows: dict[str, int] = {}
    kinds: dict[str, int] = {}
    modules: dict[str, int] = {}
    classes: dict[str, int] = {}
    layers: dict[str, int] = {}
    for t in idx.get("tasks", []):
        row = _tdl_task_row(cfg, t)
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        workflows[row["workflow_state"]] = workflows.get(row["workflow_state"], 0) + 1
        if row["task_kind"]:
            kinds[row["task_kind"]] = kinds.get(row["task_kind"], 0) + 1
        if row["module"]:
            modules[row["module"]] = modules.get(row["module"], 0) + 1
        if row["class_name"]:
            classes[row["class_name"]] = classes.get(row["class_name"], 0) + 1
        if row["layer"]:
            layers[row["layer"]] = layers.get(row["layer"], 0) + 1

    def _to_list(cnt: dict) -> list:
        return [{"value": k, "count": v} for k, v in
                sorted(cnt.items(), key=lambda x: (-x[1], x[0]))]

    return {
        "statuses": _to_list(statuses),
        "workflow_states": _to_list(workflows),
        "task_kinds": _to_list(kinds),
        "modules": _to_list(modules),
        "class_names": _to_list(classes),
        "layers": _to_list(layers),
    }


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
    evs = [e for e in store.recent_events(limit=500, project=project)
           if (e.get("task") or "") == task_id]
    task_path = tdl_store.task_path(cfg, task_id)
    report_path = tdl_store.latest_report_path(cfg, task_id)
    verdict_path = tdl_store.latest_verdict_path(cfg, task_id)
    return {
        "task": task,
        "report": report,
        "verdict": verdict,
        "events": evs,
        "markdown": {
            "task_card": render.render_task_card(task),
            "report": render.render_report_md(report) if report else "",
            "verdict": render.render_verdict_md(verdict) if verdict else "",
        },
        "sources": {
            "task": str(task_path.relative_to(cfg.root)) if task_path else "",
            "report": str(report_path.relative_to(cfg.root)) if report_path else "",
            "verdict": str(verdict_path.relative_to(cfg.root)) if verdict_path else "",
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


def _running_tasks(cfg) -> list:
    """Текущие выполняющиеся задачи с затраченным временем (сек).
    Источник: события task_started/subagent_finished из БД сервера,
    фолбэк — dates.start из TDL JSON-задачи."""
    import datetime
    from pipeline.tdl import store as tdl_store
    evs = store.recent_events(limit=500, project=cfg.name)
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
    idx = tdl_store.load_index(cfg) or {"tasks": []}
    now = datetime.datetime.now()  # локальное время — события в БД наивные (local)
    out = []
    for t in idx.get("tasks", []):
        tid = t.get("task_id", "")
        if t.get("status") == "done":
            continue
        if t.get("workflow_state") in ("blocked", "rejected"):
            continue
        started_at = started.get(tid)
        if started_at and tid not in finished:
            try:
                st = datetime.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                if st.tzinfo is not None:
                    st = st.astimezone().replace(tzinfo=None)
                out.append({"task_id": tid, "name": t.get("name", ""),
                            "elapsed_sec": max(0, int((now - st).total_seconds()))})
            except ValueError:
                pass
        elif not started_at:
            # фолбэк: дата старта из TDL-задачи — только если задача реально в работе
            if t.get("workflow_state") not in ("in_progress",):
                continue
            task = tdl_store.load_task(cfg, tid) or {}
            st = (task.get("dates") or {}).get("start")
            if st:
                try:
                    st_dt = datetime.datetime.fromisoformat(str(st).replace("Z", "+00:00"))
                    if st_dt.tzinfo is not None:
                        st_dt = st_dt.astimezone().replace(tzinfo=None)
                    out.append({"task_id": tid, "name": t.get("name", ""),
                                "elapsed_sec": max(0, int((now - st_dt).total_seconds()))})
                except ValueError:
                    pass
    out.sort(key=lambda x: -x["elapsed_sec"])
    return out


@app.get("/api/tdl/running")
async def api_tdl_running(project: str = ""):
    """Текущие выполняющиеся задачи с затраченным временем (для прогресс-бара)."""
    from pipeline.config import ConfigError as _CE
    project = project or (list_projects() or [""])[0]
    try:
        cfg = load_config(project)
    except _CE:
        return []
    return _running_tasks(cfg)


def _parse_iso_dt(v) -> datetime.datetime | None:
    """Дата (YYYY-MM-DD) или ISO-момент -> локальный datetime."""
    if not v:
        return None
    try:
        s = str(v).strip().replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _has_time(v) -> bool:
    """Есть ли в значении время (ISO-момент), а не только дата."""
    s = str(v or "").strip()
    return "T" in s or (":" in s and " " in s)


def _duration_sec(start, finish) -> int | None:
    s = _parse_iso_dt(start)
    f = _parse_iso_dt(finish)
    if s is None or f is None:
        return None
    return max(0, int((f - s).total_seconds()))


@app.get("/api/tdl/durations")
async def api_tdl_durations(project: str = ""):
    """Таблица длительностей задач: план (estimate_sec) vs факт (duration_sec).
    Для summary-задач план/факт = сумма по потомкам (по префиксу wbs).
    У незавершённых задач duration_sec = время в работе (start -> now)."""
    import datetime as _dt
    from pipeline.tdl import store as tdl_store
    project = project or (list_projects() or [""])[0]
    try:
        cfg = load_config(project)
    except ConfigError:
        return {"project": project, "tasks": [], "summary": {}}

    idx = tdl_store.load_index(cfg) or {"tasks": []}
    tasks = []
    for e in idx.get("tasks", []):
        t = tdl_store.load_task(cfg, e.get("task_id", "")) or {}
        tasks.append(t)

    by_wbs = {str(t.get("wbs_code", "")): t for t in tasks}
    wbs_set = set(by_wbs)
    prefix = lambda w: str(w) + "."

    def children_wbs(w):
        p = prefix(w)
        return sorted((x for x in wbs_set if x.startswith(p)), key=lambda s: [int(v) for v in s.split(".")])

    def all_descendants_wbs(w):
        """Все потомки по WBS (не только прямые): 2 -> 2.1, 2.1.1, 2.1.1.1..."""
        p = prefix(w)
        return sorted((x for x in wbs_set if x.startswith(p)), key=lambda s: [int(v) for v in s.split(".")])

    def child_sum(w, field):
        """Сумма field по всем потомкам (листьям) summary-задачи."""
        total = 0
        for c in all_descendants_wbs(w):
            v = by_wbs[c].get("dates", {}).get(field)
            if v:
                total += v
        return total or None

    now = _dt.datetime.now()
    rows = []
    for t in tasks:
        tid = t.get("task_id", "")
        w = str(t.get("wbs_code", ""))
        dates = t.get("dates", {}) or {}
        is_sum = bool(t.get("is_summary"))
        est = dates.get("estimate_sec") or (child_sum(w, "estimate_sec") if is_sum else None)
        if t.get("status") == "done":
            dur = dates.get("duration_sec") or (child_sum(w, "duration_sec") if is_sum else None) \
                  or _duration_sec(dates.get("start"), dates.get("finish"))
        else:
            st = _parse_iso_dt(dates.get("start"))
            if st is not None:
                dur = int((now - st).total_seconds())
            else:
                dur = child_sum(w, "duration_sec") if is_sum else None
        delta = (dur - est) if (est is not None and dur is not None) else None
        over = bool(delta is not None and delta > 0 and est and delta > est * 0.5)
        rows.append({
            "task_id": tid,
            "name": t.get("name", ""),
            "wbs_code": w,
            "level": t.get("level", 1),
            "is_summary": is_sum,
            "status": t.get("status", "open"),
            "workflow_state": t.get("workflow_state", "issued"),
            "issued": dates.get("issued", ""),
            "start": dates.get("start", ""),
            "finish": dates.get("finish", ""),
            "estimate_sec": est,
            "duration_sec": dur,
            "delta_sec": delta,
            "over_plan": over,
            "has_report": bool(e.get("report_refs", [])) if not is_sum else None,
            "has_verdict": bool(e.get("verdict_refs", [])) if not is_sum else None,
        })

    rows.sort(key=lambda r: (r["wbs_code"]))
    plan_total = sum(r["estimate_sec"] or 0 for r in rows if r["is_summary"])
    fact_total = sum(r["duration_sec"] or 0 for r in rows if r["is_summary"])
    done_count = sum(1 for r in rows if r["status"] == "done")
    over_count = sum(1 for r in rows if r["over_plan"])
    return {
        "project": project,
        "tasks": rows,
        "summary": {
            "total": len(rows),
            "done": done_count,
            "plan_sec": plan_total,
            "fact_sec": fact_total,
            "over_plan": over_count,
        },
    }


@app.get("/api/tdl/load")
async def api_tdl_load(project: str = "", period: str = "day", buckets: int = 14):
    """Примерная загруженность (план vs факт, в часах) по периодам.

    period: day — последние N дней; week — последние N недель; month — по месяцам.
    Факт: duration_sec задач, размазанный равномерно по дням start→finish
    (или start→now для незавершённых). План: estimate_sec аналогично."""
    import datetime as _dt
    from pipeline.tdl import store as tdl_store
    project = project or (list_projects() or [""])[0]
    try:
        cfg = load_config(project)
    except ConfigError:
        return {"project": project, "buckets": [], "period": period}

    idx = tdl_store.load_index(cfg) or {"tasks": []}
    now = _dt.datetime.now()

    def norm(dt):
        return dt if dt.tzinfo is None else dt.astimezone().replace(tzinfo=None)

    def parse(v):
        if not v:
            return None
        try:
            return norm(_parse_iso_dt(v))
        except Exception:
            return None

    # 1) собрать задачи с длительностью (done: duration_sec; в работе: start->now)
    tasks = []
    for e in idx.get("tasks", []):
        t = tdl_store.load_task(cfg, e.get("task_id", "")) or {}
        dates = t.get("dates", {}) or {}
        start = parse(dates.get("start"))
        finish = parse(dates.get("finish"))
        est = dates.get("estimate_sec")
        is_dates = not _has_time(dates.get("start"))  # start задан датой (не ISO)
        if t.get("status") == "done":
            dur = dates.get("duration_sec")
            if not dur and start and finish:
                dur = max(0, int((finish - start).total_seconds()))
        else:
            dur = max(0, int((now - start).total_seconds())) if start else None
        if not dur:
            continue
        if not start:
            # нет start: берём issued 09:00 (или finish-день) как начало работы
            start = parse(dates.get("issued")) or finish
            if start:
                start = start.replace(hour=9, minute=0, second=0)
                is_dates = True
        if not start:
            continue
        end = finish or now
        if is_dates and finish:
            end = finish + _dt.timedelta(days=1)  # даты: finish = последний день работы
        if end <= start:
            end = start + _dt.timedelta(days=1)  # один день работы
        tasks.append({"start": start, "end": end, "dur": dur, "est": est,
                      "is_dates": is_dates})

    # 2) бакеты по периоду
    def day_key(dt):
        return dt.date().isoformat()

    def bucket_label(dt, period):
        if period == "day":
            return dt.date().isoformat()
        if period == "week":
            monday = dt.date() - _dt.timedelta(days=dt.date().weekday())
            return monday.isoformat()
        return dt.strftime("%Y-%m")

    buckets_out = []
    if period == "day":
        dates = [(now - _dt.timedelta(days=i)) for i in range(buckets - 1, -1, -1)]
        for d in dates:
            buckets_out.append({"label": day_key(d), "fact_h": 0, "plan_h": 0})
    elif period == "week":
        # последние N недель, начиная с понедельника текущей
        monday = now.date() - _dt.timedelta(days=now.date().weekday())
        weeks = [(monday - _dt.timedelta(days=7 * i)) for i in range(buckets - 1, -1, -1)]
        for w in weeks:
            buckets_out.append({"label": w.isoformat(), "fact_h": 0, "plan_h": 0})
    else:  # month
        cur = _dt.date(now.year, now.month, 1)
        months = []
        y, m = cur.year, cur.month
        for _ in range(buckets):
            months.append(_dt.date(y, m, 1))
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        for mo in reversed(months):
            buckets_out.append({"label": mo.strftime("%Y-%m"), "fact_h": 0, "plan_h": 0})

    # 3) размазать длительность по бакетам
    def overlaps(t0, t1, bucket_start, bucket_end):
        s = max(t0, bucket_start)
        e = min(t1, bucket_end)
        return max(0, (e - s).total_seconds()) if e > s else 0

    for t in tasks:
        if t["is_dates"]:
            # даты без времени: duration = рабочие часы, раскидываем равномерно
            # по календарным дням диапазона (start..end)
            span_days = max(1, (t["end"].date() - t["start"].date()).days)
            per_day = t["dur"] / span_days / 3600
            for b in buckets_out:
                if period == "day":
                    bs = _dt.datetime.fromisoformat(b["label"] + "T00:00:00")
                    if bs.date() < t["start"].date() or bs.date() >= t["end"].date():
                        continue
                    b["fact_h"] += per_day
                    if t["est"]:
                        b["plan_h"] += t["est"] / span_days / 3600
                elif period == "week":
                    bs = _dt.datetime.fromisoformat(b["label"] + "T00:00:00")
                    be = bs + _dt.timedelta(days=7)
                    days_in = max(0, (min(t["end"].date(), be.date())
                                      - max(t["start"].date(), bs.date())).days)
                    if days_in <= 0:
                        continue
                    b["fact_h"] += per_day * days_in
                    if t["est"]:
                        b["plan_h"] += t["est"] / span_days / 3600 * days_in
                else:
                    bs = _dt.datetime.fromisoformat(b["label"] + "-01T00:00:00")
                    if bs.month == 12:
                        be = _dt.datetime(bs.year + 1, 1, 1)
                    else:
                        be = _dt.datetime(bs.year, bs.month + 1, 1)
                    days_in = max(0, (min(t["end"].date(), be.date())
                                      - max(t["start"].date(), bs.date())).days)
                    if days_in <= 0:
                        continue
                    b["fact_h"] += per_day * days_in
                    if t["est"]:
                        b["plan_h"] += t["est"] / span_days / 3600 * days_in
            continue
        for b in buckets_out:
            if period == "day":
                bs = _dt.datetime.fromisoformat(b["label"] + "T00:00:00")
                be = bs + _dt.timedelta(days=1)
            elif period == "week":
                bs = _dt.datetime.fromisoformat(b["label"] + "T00:00:00")
                be = bs + _dt.timedelta(days=7)
            else:
                bs = _dt.datetime.fromisoformat(b["label"] + "-01T00:00:00")
                if bs.month == 12:
                    be = _dt.datetime(bs.year + 1, 1, 1)
                else:
                    be = _dt.datetime(bs.year, bs.month + 1, 1)
            # доля длительности, попавшая в бакет
            total_sec = max(1, (t["end"] - t["start"]).total_seconds())
            share = overlaps(t["start"], t["end"], bs, be) / total_sec
            b["fact_h"] += t["dur"] * share / 3600
            if t["est"]:
                b["plan_h"] += t["est"] * share / 3600

    for b in buckets_out:
        b["fact_h"] = round(b["fact_h"], 1)
        b["plan_h"] = round(b["plan_h"], 1)
    return {"project": project, "period": period, "buckets": buckets_out}


# --- Потребление токенов (opencode.db: session.tokens_*) ------------------

def _opencode_db_path() -> Path | None:
    """Путь к локальной БД opencode с токенами (session.tokens_input/output/cost)."""
    env = os.environ.get("OPENCODE_DB")
    if env:
        return Path(env)
    cand = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    return cand if cand.exists() else None


def _session_rows(days: int | None) -> list[dict]:
    """Сырые сессии opencode (time_created мс, tokens_*, cost), опционально за N дней."""
    db_path = _opencode_db_path()
    if not db_path:
        return []
    import sqlite3
    import time as _t
    rows = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        q = "SELECT time_created, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, cost FROM session"
        params: tuple = ()
        if days:
            cutoff = int((_t.time() - days * 86400) * 1000)
            q += " WHERE time_created >= ?"
            params = (cutoff,)
        q += " ORDER BY time_created"
        for r in con.execute(q, params):
            rows.append(dict(r))
        con.close()
    except Exception:
        return []
    return rows


def _token_series(rows: list[dict], period: str) -> dict:
    """Сгруппировать сессии по дням/неделям и посчитать токены + стоимость."""
    import datetime as _dt
    daily: dict[str, dict] = {}

    def _day(ms) -> str:
        return _dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")

    def _week(ms) -> str:
        d = _dt.datetime.fromtimestamp(ms / 1000)
        monday = (d - _dt.timedelta(days=d.weekday())).strftime("%Y-%m-%d")
        return monday

    for r in rows:
        ts = r.get("time_created") or 0
        key = _day(ts) if period == "day" else _week(ts)
        bucket = daily.setdefault(key, {
            "date": key, "input": 0, "output": 0, "reasoning": 0,
            "cache_read": 0, "cache_write": 0, "cost": 0.0, "sessions": 0,
        })
        bucket["input"] += int(r.get("tokens_input") or 0)
        bucket["output"] += int(r.get("tokens_output") or 0)
        bucket["reasoning"] += int(r.get("tokens_reasoning") or 0)
        bucket["cache_read"] += int(r.get("tokens_cache_read") or 0)
        bucket["cache_write"] += int(r.get("tokens_cache_write") or 0)
        bucket["cost"] += float(r.get("cost") or 0.0)
        bucket["sessions"] += 1

    series = sorted(daily.values(), key=lambda x: x["date"])
    return {
        "period": period,
        "series": series,
        "total_input": sum(b["input"] for b in series),
        "total_output": sum(b["output"] for b in series),
        "total_cost": round(sum(b["cost"] for b in series), 4),
        "sessions": sum(b["sessions"] for b in series),
    }


@app.get("/api/usage/tokens")
async def api_usage_tokens(period: str = "day", days: int = 30):
    """Потребление токенов: ?period=day|week&days=30.
    Читает локальную opencode.db (session.tokens_input/output/cost)."""
    if period not in ("day", "week"):
        period = "day"
    rows = _session_rows(days)
    return _token_series(rows, period)


# Лимиты подписки OpenCode Go (docs/go: 5h=$12, неделя=$30, месяц=$60)
GO_LIMITS = [
    {"key": "rolling", "label": "Скользящее использование", "window_h": 5, "limit": 12.0},
    {"key": "weekly", "label": "Недельное использование", "window_h": 7 * 24, "limit": 30.0},
    {"key": "monthly", "label": "Ежемесячное использование", "window_h": 30 * 24, "limit": 60.0},
]


def _go_reset_in(window_h: float) -> int:
    """Секунд до сброса окна Go-лимита: 5h/неделя/месяц от начала текущего окна."""
    import time as _t
    now = _t.time()
    if window_h == 5:
        return int(5 * 3600 - (now % (5 * 3600)))
    if window_h == 7 * 24:
        # неделя: до понедельника 00:00 UTC
        import datetime as _dt
        now_dt = _dt.datetime.now(_dt.timezone.utc)
        monday = (now_dt - _dt.timedelta(days=now_dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return int((monday + _dt.timedelta(weeks=1) - now_dt).total_seconds())
    # месяц: до 1-го числа следующего месяца 00:00 UTC
    import datetime as _dt
    now_dt = _dt.datetime.now(_dt.timezone.utc)
    nxt = (now_dt.replace(day=1) + _dt.timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int((nxt - now_dt).total_seconds())


@app.get("/api/usage/go")
async def api_usage_go():
    """Локальный расчёт использования подписки OpenCode Go (лимиты из docs/go).
    Проценты: потрачено $ за окно / лимит. Источник — opencode.db session.cost."""
    import time as _t
    now_ms = int(_t.time() * 1000)
    rows = _session_rows(days=None)  # все сессии с cost
    out = []
    for lim in GO_LIMITS:
        cutoff = now_ms - int(lim["window_h"] * 3600 * 1000)
        used = sum(float(r.get("cost") or 0.0) for r in rows
                   if (r.get("time_created") or 0) >= cutoff)
        pct = min(100.0, round(used / lim["limit"] * 100, 1))
        out.append({
            "key": lim["key"],
            "label": lim["label"],
            "used": round(used, 2),
            "limit": lim["limit"],
            "percent": pct,
            "reset_in_sec": _go_reset_in(lim["window_h"]),
        })
    return {"provider": "OpenCode Go", "items": out}


def _session_durations(project: str = "", days: int = 30) -> dict:
    """Длительности сессий opencode (time_updated - time_created) с группировкой
    по проекту/задаче. project — имя проекта: фильтр по directory, содержащему его."""
    import sqlite3
    import time as _t
    db_path = _opencode_db_path()
    if not db_path:
        return {"project": project, "sessions": [], "total_sec": 0, "count": 0}
    rows = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        q = ("SELECT directory, title, model, time_created, time_updated, "
             "tokens_input, tokens_output, cost FROM session "
             "WHERE time_created IS NOT NULL AND time_updated IS NOT NULL")
        params: tuple = ()
        if days:
            cutoff = int((_t.time() - days * 86400) * 1000)
            q += " AND time_created >= ?"
            params = (cutoff,)
        q += " ORDER BY time_created DESC"
        for r in con.execute(q, params):
            d = dict(r)
            d["duration_sec"] = max(0, int((d["time_updated"] - d["time_created"]) / 1000))
            rows.append(d)
        con.close()
    except Exception:
        return {"project": project, "sessions": [], "total_sec": 0, "count": 0}

    if project:
        proj_key = project.lower()
        rows = [r for r in rows if proj_key in (r.get("directory") or "").lower()]
    total = sum(r["duration_sec"] for r in rows)
    return {"project": project, "sessions": rows, "total_sec": total, "count": len(rows)}


@app.get("/api/usage/sessions")
async def api_usage_sessions(project: str = "", days: int = 30):
    """Длительности сессий opencode (таблица: задача | длительность | итого).
    project — имя проекта (фильтр по directory)."""
    days = min(max(days, 1), 365)
    return _session_durations(project or "", days)


@app.get("/api/sessions/live")
async def api_sessions_live(minutes: int = 15):
    """Открытые сессии opencode: обновлявшиеся за последние minutes минут
    (не архивированные). Источник — opencode.db (session.time_updated)."""
    return _live_opencode_sessions(minutes=minutes)


@app.post("/api/sessions/live/{sid}/kill")
async def api_sessions_live_kill(sid: str):
    """Убить/удалить сессию opencode (opencode session delete <sid>).
    Возвращает {ok, sid, error}."""
    import shutil
    import subprocess
    from pipeline.proc import no_window_flags
    oc = os.environ.get("OPENCODE_CMD") or ""
    if oc and os.path.exists(oc):
        pass
    elif os.name == "nt":
        cand = Path(os.environ.get("APPDATA", "")) / "npm" / "opencode.cmd"
        oc = str(cand) if cand.exists() else shutil.which("opencode.cmd") or "opencode"
    else:
        oc = shutil.which("opencode") or "opencode"
    try:
        r = subprocess.run([oc, "session", "delete", sid],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60,
                           creationflags=no_window_flags())
        ok = r.returncode == 0
        return {"ok": ok, "sid": sid,
                "error": None if ok else (r.stderr or r.stdout or f"rc={r.returncode}")[:300]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "sid": sid, "error": str(e)})


# --- Сырые задания пользователя (Входящие): БД + файл + git ------------------

class RequestIn(BaseModel):
    project: str
    text: str


def _req_id() -> str:
    import uuid
    return "R-" + uuid.uuid4().hex[:10].upper()


@app.post("/api/requests")
async def request_create(body: RequestIn):
    """Зафиксировать сырое задание пользователя: запись в БД + файл
    Tasks\\Входящие\\ + git-коммит в проекте (как общение агентов)."""
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "пустой текст задания")
    try:
        cfg = load_config(body.project)
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")
    req_id = _req_id()
    inbox = cfg.abs_tasks_dir("inbox")
    inbox.mkdir(parents=True, exist_ok=True)
    import re as _re
    slug = _re.sub(r"[^\wа-яА-ЯёЁ\- ]", "", text[:50]).strip().replace(" ", "_") or "задание"
    fname = f"{req_id}_{slug}.md"
    fpath = inbox / fname
    fpath.write_text(
        f"# Сырое задание {req_id}\n\n"
        f"- Источник: dashboard\n"
        f"- Дата: {_now_iso()}\n"
        f"- Статус: new\n\n## Задание\n\n{text}\n",
        encoding="utf-8")
    # git-фиксация (как общение агентов: файлы+git — источник правды)
    commit = ""
    try:
        import subprocess as _sp
        from pipeline.proc import no_window_flags as _nwf
        _sp.run(["git", "-C", str(cfg.root), "add", "-A"],
                capture_output=True, timeout=30, creationflags=_nwf())
        r = _sp.run(["git", "-C", str(cfg.root), "commit", "-m",
                     f"inbox: сырое задание {req_id} ({fname})"],
                    capture_output=True, text=True, timeout=30,
                    creationflags=_nwf())
        commit = (r.stdout or "").strip().splitlines()[-1][:80] if r.returncode == 0 else ""
    except Exception:
        pass
    row = store.add_request(req_id, project=body.project, text=text,
                            status="new",
                            file=str(fpath.relative_to(cfg.root)).replace("\\", "/"),
                            commit_msg=commit)
    ev = store.add_event("request_created", "dashboard", "controller",
                         project=body.project, task="",
                         payload={"request_id": req_id, "text": text[:120],
                                  "file": row["file"], "commit_msg": commit})
    hub.publish(ev)
    return row


@app.get("/api/requests")
async def request_list(project: str = "", status: str = "", limit: int = 100):
    rows = store.list_requests(project=project, status=status, limit=limit)
    out = []
    for r in rows:
        try:
            cfg = load_config(r.get("project") or "")
        except ConfigError:
            out.append(r)
            continue
        rel = (r.get("file") or "").replace("/", "\\")
        r = dict(r)
        r["path"] = str(cfg.root / rel) if rel else ""
        out.append(r)
    return out


@app.get("/api/requests/{req_id}")
async def request_get(req_id: str):
    """Прочитать сырое задание: метаданные из БД + актуальный текст из файла."""
    row = store.get_request(req_id)
    if not row:
        raise HTTPException(404, f"задание {req_id} не найдено")
    try:
        cfg = load_config(row["project"])
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")
    rel = (row.get("file") or "").replace("/", "\\")
    path = str(cfg.root / rel) if rel else ""
    content = ""
    try:
        fpath = Path(path)
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
    except Exception:
        content = ""
    row = dict(row)
    row["path"] = path
    row["content"] = content
    return row


@app.patch("/api/requests/{req_id}")
async def request_update(req_id: str, body: RequestIn = None):
    """Обновить текст сырого задания: перезаписать файл + git-коммит + БД."""
    row = store.get_request(req_id)
    if not row:
        raise HTTPException(404, f"задание {req_id} не найдено")
    text = (body.text if body else "").strip()
    if not text:
        raise HTTPException(400, "пустой текст задания")
    try:
        cfg = load_config(row["project"])
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")
    rel = (row.get("file") or "").replace("/", "\\")
    fpath = Path(str(cfg.root / rel)) if rel else None
    if fpath is None or not fpath.exists():
        raise HTTPException(404, f"файл задания не найден: {rel}")
    fpath.write_text(
        f"# Сырое задание {req_id}\n\n"
        f"- Источник: dashboard\n"
        f"- Дата: {_now_iso()}\n"
        f"- Статус: {row.get('status') or 'new'}\n\n## Задание\n\n{text}\n",
        encoding="utf-8")
    commit = _git_commit(cfg.root, f"inbox: обновлено задание {req_id} ({fpath.name})")
    store.update_request(req_id, text=text, commit_sha=commit)
    ev = store.add_event("request_updated", "dashboard", "controller",
                         project=row["project"], task="",
                         payload={"request_id": req_id, "file": row["file"],
                                  "commit_sha": commit})
    hub.publish(ev)
    row = store.get_request(req_id)
    row = dict(row)
    row["path"] = str(fpath)
    row["content"] = fpath.read_text(encoding="utf-8")
    return row


def _git_commit(root, message: str) -> str:
    """git add -A + commit в корне проекта; возвращает короткий хеш (или '')."""
    import subprocess as _sp
    from pipeline.proc import no_window_flags as _nwf
    try:
        _sp.run(["git", "-C", str(root), "add", "-A"],
                capture_output=True, timeout=30, creationflags=_nwf())
        r = _sp.run(["git", "-C", str(root), "commit", "-m", message],
                    capture_output=True, text=True, timeout=30, creationflags=_nwf())
        return (r.stdout or "").strip().splitlines()[-1][:80] if r.returncode == 0 else ""
    except Exception:
        return ""


@app.post("/api/requests/{req_id}/dispatch")
async def request_dispatch(req_id: str):
    """Оформить сырое задание в задачу (dispatch): файл уходит в Активные,
    статус запроса -> dispatched (БД + git-коммит)."""
    row = store.get_request(req_id)
    if not row:
        raise HTTPException(404, f"задание {req_id} не найдено")
    try:
        cfg = load_config(row["project"])
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")
    import argparse as _ap
    from pipeline.cli import cmd_dispatch
    src = cfg.abs_tasks_dir("inbox") / Path(row["file"]).name
    if not src.exists():
        # фолбэк: пересоздать файл из текста
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(f"# Задание {req_id}\n\n{row['text']}\n", encoding="utf-8")
    rc = cmd_dispatch(cfg, _ap.Namespace(file=str(src), title=None, priority=None,
                                         requirements=None, result=None,
                                         remark=f"из сырого задания {req_id}",
                                         id=None))
    if rc != 0:
        raise HTTPException(500, f"dispatch не удался (rc={rc})")
    store.update_request(req_id, status="dispatched")
    ev = store.add_event("request_dispatched", "dashboard", "controller",
                         project=row["project"], task="",
                         payload={"request_id": req_id, "file": row["file"]})
    hub.publish(ev)
    return store.get_request(req_id)


@app.get("/api/inbox")
async def api_inbox(limit: int = 200):
    msgs = []
    for ag in store.agents():
        msgs += store.inbox_messages(ag["name"])
    msgs.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return msgs[:limit]


# --- Чат: агенты, диалоги, статусы --------------------------------------

KNOWN_AGENTS = [
    {"name": "controller", "role": "Агент-1 контролёр"},
    {"name": "executor", "role": "Агент-2 исполнитель"},
    {"name": "agent-manager", "role": "Менеджер (оркестратор)"},
    {"name": "agent_watch", "role": "Сторож контролёра"},
    {"name": "browser", "role": "Агент-3 браузерный мост"},
]


@app.get("/api/chat/agents")
async def chat_agents():
    """Агенты для чата: ТОЛЬКО ЖИВЫЕ, синхронно с панелью «🗂 Сессии».

    В список попадают:
      1. агенты-процессы со свежим heartbeat (online, age <= 90 с);
      2. живые сессии субагентов конвейера (status = running/created);
      3. живые opencode-сессии (time_updated за последние 15 мин, не архивные).
    Мёртвые (offline / спящие / завершённые сессии) НЕ показываются."""
    import datetime as _dt
    now = _dt.datetime.now()
    out = []

    def _age_sec(ts_iso):
        if not ts_iso:
            return None
        try:
            t = _parse_iso_dt(ts_iso)
        except ValueError:
            return None
        return max(0, int((now - t).total_seconds())) if t else None

    # 1) живые агенты-процессы (heartbeat)
    evs = store.recent_events(limit=400)
    started: dict[str, str] = {}
    finished: set[tuple] = set()
    for e in evs:
        src = e.get("from") or ""
        if not src:
            continue
        if e["type"] == "task_started":
            started[src] = e.get("task", "")
        elif e["type"] == "subagent_finished":
            finished.add((src, e.get("task", "")))
    for a in store.agents():
        age = _age_sec(a.get("last_seen"))
        if a["status"] != "online" or age is None or age > 90:
            continue  # мёртвый — не показываем
        name = a["name"]
        task = started.get(name, "")
        if task and (name, task) in finished:
            task = ""
        role = next((k["role"] for k in KNOWN_AGENTS if k["name"] == name), "субагент")
        out.append({
            "name": name, "role": role, "status": a["status"],
            "project": a.get("project", ""), "pid": a.get("pid"),
            "cmd": a.get("cmd", ""), "last_seen": a.get("last_seen"),
            "heartbeat_age_sec": age, "sleeping": False,
            "current_task": task, "restartable": bool(a.get("cmd")),
            "killable": a.get("pid") is not None,
            "live": True, "chat_ok": True, "kind": "agent",
        })

    # 2) живые сессии субагентов (running/created) — как в панели «Сессии»
    for s in store.list_sessions():
        if s["status"] not in ("running", "created"):
            continue
        age = _age_sec(s.get("heartbeat") or s.get("created_at"))
        role_cfg = AGENT_ROLES.get(s.get("role", ""), {})
        # name = SSE-канал сессии (session_worker слушает session-<sid>),
        # display_name — человеческое имя агента для чата
        out.append({
            "name": f"session-{s['id']}",
            "display_name": s.get("agent") or f"session-{s['id']}",
            "role": role_cfg.get("title") or s.get("role", "субагент"),
            "status": "online", "project": s.get("project", ""),
            "pid": s.get("pid"), "cmd": s.get("cmd", ""),
            "last_seen": s.get("heartbeat") or s.get("created_at"),
            "heartbeat_age_sec": age, "sleeping": False,
            "current_task": s.get("task", ""),
            "restartable": bool(s.get("cmd")), "killable": s.get("pid") is not None,
            "live": True, "chat_ok": True, "kind": "session",
            "session_id": s["id"], "session_role": s.get("role", ""),
        })

    # 3) живые opencode-сессии (time_updated за 15 мин) — как в панели «Сессии»
    for s in _live_opencode_sessions(minutes=15):
        age = s.get("age_sec")
        dir_name = (s.get("directory") or "").rstrip("/").split("/")[-1]
        out.append({
            "name": s.get("slug") or s.get("id", ""),
            "role": "opencode-сессия", "status": "online",
            "project": dir_name, "pid": None, "cmd": "",
            "last_seen": _dt.datetime.fromtimestamp(
                (s.get("time_updated") or 0) / 1000).isoformat(timespec="seconds"),
            "heartbeat_age_sec": age, "sleeping": False,
            "current_task": (s.get("title") or "")[:60],
            "restartable": False, "killable": True,
            "live": True, "chat_ok": True, "kind": "opencode",
            "session_id": s.get("id"), "title": s.get("title") or "",
        })

    out.sort(key=lambda x: (not x.get("current_task"), x.get("kind") != "agent", x["name"]))
    return out


def _live_opencode_sessions(minutes: int = 15) -> list[dict]:
    """Открытые сессии opencode: обновлявшиеся за последние minutes минут."""
    import sqlite3
    import time as _t
    db_path = _opencode_db_path()
    if not db_path:
        return []
    minutes = min(max(minutes, 1), 1440)
    cutoff = int((_t.time() - minutes * 60) * 1000)
    out = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        q = ("SELECT id, slug, title, directory, agent, model, time_created, "
             "time_updated, time_archived FROM session "
             "WHERE time_updated IS NOT NULL AND time_updated >= ? "
             "AND time_archived IS NULL ORDER BY time_updated DESC")
        for r in con.execute(q, (cutoff,)):
            d = dict(r)
            d["live"] = True
            d["age_sec"] = max(0, int((_t.time() * 1000 - d["time_updated"]) / 1000))
            out.append(d)
        con.close()
    except Exception:
        return []
    return out


def _agent_proc(name: str) -> dict | None:
    """Информация о процессе агента (pid, cmd) или None."""
    for a in store.agents():
        if a["name"] == name:
            return a
    return None


def _run_detached(cmd: list[str], cwd: str) -> None:
    """Запустить процесс агента в отрыве от сервера (новый процесс, без консоли)."""
    import subprocess
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS \
        if os.name == "nt" else 0
    subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     creationflags=creationflags, shell=False)


def _kill_pid(pid: int) -> bool:
    import subprocess
    from pipeline.proc import no_window_flags
    try:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                               capture_output=True, timeout=15,
                               creationflags=no_window_flags())
            return r.returncode == 0
        os.kill(pid, 9)
        return True
    except Exception:
        return False


@app.post("/api/chat/agents/{name}/kill")
async def chat_agent_kill(name: str):
    """Убить процесс агента (если серверу известен его PID)."""
    try:
        a = _agent_proc(name)
        if not a or a.get("pid") is None:
            raise HTTPException(404, f"у агента {name} нет зарегистрированного PID")
        ok = _kill_pid(int(a["pid"]))
        store.mark_offline(name)
        if ok:
            store.add_event("agent_killed", "dashboard", "feed",
                            payload={"agent": name, "pid": a["pid"]})
        return {"ok": ok, "agent": name, "pid": a["pid"]}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.post("/api/chat/agents/{name}/restart")
async def chat_agent_restart(name: str):
    """Перезапустить агента: убить текущий процесс и поднять заново по сохранённой команде."""
    import shlex
    try:
        a = _agent_proc(name)
        if not a or not a.get("cmd"):
            raise HTTPException(404, f"у агента {name} нет команды запуска (cmd)")
        if a.get("pid") is not None:
            _kill_pid(int(a["pid"]))
        try:
            cmd = shlex.split(a["cmd"])
        except ValueError:
            cmd = a["cmd"].split()
        root = Path(__file__).resolve().parent.parent  # корень dev-pipeline
        _run_detached(cmd, cwd=str(root))
        store.add_event("agent_restarted", "dashboard", "feed",
                        payload={"agent": name, "cmd": a["cmd"]})
        return {"ok": True, "agent": name, "cmd": a["cmd"]}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.get("/api/chat/history")
async def chat_history(agent: str, limit: int = 200):
    """Диалог dashboard <-> агент (сообщения в обе стороны)."""
    return store.dialog_messages(agent, limit=min(limit, 500))


@app.post("/api/chat/command")
async def chat_command(body: MessageIn):
    """Команда из чата агенту: сохраняется в очередь + публикуется в его SSE-канал."""
    msg = store.add_message(body.from_, body.to, body.text)
    hub.publish({"id": msg["id"], "type": "message", "from": msg["from"],
                 "to": msg["to"], "text": msg["text"],
                 "created_at": msg["created_at"], "delivery": msg["delivery"],
                 "payload": {"chat": True}})
    return msg


@app.get("/")
async def index():
    from fastapi.responses import FileResponse
    if DASHBOARD.exists():
        return FileResponse(DASHBOARD, media_type="text/html; charset=utf-8")
    return {"error": "dashboard.html не найден"}


# --- Создание агента-помощника (роль + скиллы) ------------------------------

AGENT_ROLES = {
    "controller": {
        "title": "Контролёр/планировщик (Агент-1)",
        "skill": "pipeline-controller",
        "desc": "Диспатч задач, приём отчётов, вердикты (verify), оркестрация, реакция на task_stalled.",
        "prompt": ("Ты — Агент-1 (контролёр/планировщик) конвейера dev-pipeline. "
                   "Прочитай скилл {skill_path} ПЕРВЫМ — там методика и твоя роль.\n"
                   "Твоя работа: диспатч задач (dispatch), приём отчётов, verify (вердикты PASS/FAIL), "
                   "оркестрация субагентов, реакция на task_stalled (редиспатч).\n"
                   "Дай краткий ответ: подтверди роль и опиши текущий статус задач проекта "
                   "(можешь запустить python -m pipeline.cli tdl-status {project})."),
    },
    "executor": {
        "title": "Исполнитель (Агент-2)",
        "skill": "pipeline-executor",
        "desc": "Выполняет задачи A-NN: правит код, собирает, тестирует, пишет отчёты с доказательствами.",
        "prompt": ("Ты — Агент-2 (исполнитель) конвейера dev-pipeline. "
                   "Прочитай скилл {skill_path} ПЕРВЫМ — там методика и твоя роль.\n"
                   "Твоя работа: выполнять задачи из Tasks\\Активные\\A-NN_*.md (или TDL JSON), "
                   "собирать и тестировать проект, писать отчёты с доказательствами в Tasks\\Отчёты.\n"
                   "Дай краткий ответ: подтверди роль и покажи, какие задачи сейчас открыты "
                   "(python -m pipeline.cli tdl-status {project})."),
    },
    "browser": {
        "title": "Облачный мост с ИИ (Агент-3)",
        "skill": "pipeline-browser-bridge",
        "desc": "Передаёт промпты в облачный ИИ (Qwen/DeepSeek через LocalAssitent) и возвращает ответы.",
        "prompt": ("Ты — Агент-3 (браузерный мост) конвейера dev-pipeline. "
                   "Прочитай скилл {skill_path} ПЕРВЫМ — там методика и твоя роль.\n"
                   "Твоя работа: забирать задания из Tasks\\Конвейер\\Браузер\\*.txt, отправлять промпты "
                   "в облачный ИИ через LocalAssitent (Edge 9222) и сохранять ответы.\n"
                   "Дай краткий ответ: подтверди роль и проверь, есть ли задания в папке Браузер."),
    },
    "reviewer": {
        "title": "Тестировщик/ревьюер",
        "skill": "pipeline-reviewer",
        "desc": "Независимая проверка задач: git diff/status, тесты, соответствие факту; вердикт REVIEW.md.",
        "prompt": ("Ты — тестировщик/ревьюер конвейера dev-pipeline. "
                   "Прочитай скилл {skill_path} ПЕРВЫМ — там методика и твоя роль.\n"
                   "Твоя работа: независимо проверять выполненные задачи (git diff/status/log, тесты), "
                   "фиксировать вердикт REVIEW.md (PASS/NEEDS_CHANGES/FAIL). НЕ правишь код.\n"
                   "Дай краткий ответ: подтверди роль и покажи незакрытые задачи проекта "
                   "(python -m pipeline.cli tdl-status {project})."),
    },
    "qwen": {
        "title": "Бесплатный рабочий (Qwen)",
        "skill": "pipeline-qwen-worker",
        "desc": "Тонкий локальный агент: тяжёлую генерацию файлов делегирует облачному Qwen через мост.",
        "prompt": ("Ты — бесплатный рабочий (Qwen) конвейера dev-pipeline. "
                   "Прочитай скилл {skill_path} ПЕРВЫМ — там схема работы и команды моста.\n"
                   "Твоя работа: формулировать задачу, отправлять контекст в облачный Qwen через "
                   "qwen_bridge.py, применять FILE:-блоки ответов, собирать и проверять.\n"
                   "Дай краткий ответ: подтверди роль и опиши, как начнёшь работу."),
    },
    "planner": {
        "title": "Планировщик миссии",
        "skill": "pipeline-planner",
        "desc": "LLM-декомпозиция миссии на этапы/классы/листовые задачи (spec.json → tdl-plan).",
        "prompt": ("Ты — планировщик миссии конвейера dev-pipeline. "
                   "Прочитай скилл {skill_path} ПЕРВЫМ — схема выхода, правила декомпозиции.\n"
                   "Твоя работа: декомпозировать миссию на иерархию этапы→классы→листья и писать "
                   "spec.json для tdl-plan.\n"
                   "Дай краткий ответ: подтверди роль и жди файл миссии."),
    },
}

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


class AgentIn(BaseModel):
    role: str
    project: str = ""
    model: str = ""
    task: str = ""


@app.post("/api/agents")
async def agent_create(body: AgentIn):
    """Создать агента-помощника: сессия с ролью и предзагруженным скиллом.

    Агент поднимает opencode run со скиллом роли (методика + роль) и кратким
    ТЗ. Сессия видна в панели «🗂 Сессии», убить можно через
    POST /api/sessions/{sid}/kill."""
    role = body.role
    cfg = AGENT_ROLES.get(role)
    if not cfg:
        raise HTTPException(400, f"роль {role} не известна: {sorted(AGENT_ROLES)}")
    skill = cfg["skill"]
    skill_path = SKILLS_DIR / skill / "SKILL.md"
    if not skill_path.exists():
        raise HTTPException(404, f"скилл {skill} не найден ({skill_path})")
    prompt = cfg["prompt"].format(skill_path=skill_path, project=body.project or "?")
    if body.task:
        prompt += f"\n\nДОПОЛНИТЕЛЬНОЕ ЗАДАНИЕ (выполни после подтверждения роли):\n{body.task}"
    sid = _sess_id()
    s = store.create_session(
        sid, project=body.project, task="", agent=f"agent-{role}-{sid[-4:]}",
        role=role, model=body.model or "", skill=skill,
        instruction={"prompt": prompt, "model": body.model or "", "skill": skill,
                     "role": role, "task": body.task or ""})
    ev = store.add_event("session_created", "server", "feed", project=body.project,
                         task="", payload={"session_id": sid, "agent": s["agent"],
                                           "role": role, "agent_role": True})
    hub.publish(ev)
    return s


# --- Явные сессии субагентов ------------------------------------------------

SESSION_CHANNEL = "session-{sid}"   # SSE-канал сессии (агент подписывается как session-<sid>)


class SessionIn(BaseModel):
    id: str = ""
    project: str
    task: str = ""
    agent: str = ""
    role: str = "worker"
    model: str = ""
    skill: str = ""
    instruction: dict = {}


class SessionStatusIn(BaseModel):
    status: str
    note: str = ""
    report: str = ""
    error: str = ""


class SessionStartIn(BaseModel):
    pid: int | None = None
    cmd: str = ""


def _sess_id() -> str:
    import uuid
    return "S-" + uuid.uuid4().hex[:10].upper()


@app.post("/api/sessions")
async def session_create(body: SessionIn):
    """Создать явную сессию субагента (id генерируется сервером).
    Инструкция (JSON) — источник постановки: task_file, report, log, model, prompt..."""
    sid = body.id or _sess_id()
    if store.get_session(sid):
        raise HTTPException(409, f"сессия {sid} уже существует")
    s = store.create_session(sid, project=body.project, task=body.task, agent=body.agent,
                             role=body.role, model=body.model, skill=body.skill,
                             instruction=body.instruction)
    ev = store.add_event("session_created", "server", "feed", project=body.project,
                         task=body.task, payload={"session_id": sid, "agent": body.agent,
                                                  "role": body.role})
    hub.publish(ev)
    return s


@app.get("/api/sessions")
async def session_list(project: str = "", task: str = "", status: str = ""):
    return store.list_sessions(project=project, task=task, status=status)


@app.get("/api/sessions/{sid}")
async def session_get(sid: str):
    s = store.get_session(sid)
    if not s:
        raise HTTPException(404, f"сессия {sid} не найдена")
    return s


@app.post("/api/sessions/{sid}/start")
async def session_start(sid: str, body: SessionStartIn):
    """Субагент взял сессию в работу (pid/cmd фиксируются для kill/restart)."""
    s = store.get_session(sid)
    if not s:
        raise HTTPException(404, f"сессия {sid} не найдена")
    fields = {"status": "running", "started_at": datetime.datetime.now().isoformat(timespec="seconds")}
    if body.pid is not None:
        fields["pid"] = body.pid
    if body.cmd:
        fields["cmd"] = body.cmd
    store.update_session(sid, **fields)
    store.touch_session(sid)
    ev = store.add_event("session_started", "server", "feed", project=s["project"],
                         task=s["task"], payload={"session_id": sid, "pid": body.pid})
    hub.publish(ev)
    return store.get_session(sid)


@app.post("/api/sessions/{sid}/status")
async def session_status(sid: str, body: SessionStatusIn):
    """Обновить статус сессии (running/note/progress | done/failed + report/error)."""
    s = store.get_session(sid)
    if not s:
        raise HTTPException(404, f"сессия {sid} не найдена")
    allowed = {"created", "running", "done", "failed", "killed", "stalled"}
    if body.status not in allowed:
        raise HTTPException(400, f"статус {body.status} не из списка {sorted(allowed)}")
    fields = {"status": body.status}
    if body.note:
        fields["note"] = body.note
    if body.report:
        fields["report"] = body.report
    if body.error:
        fields["error"] = body.error
    if body.status in ("done", "failed", "killed", "stalled"):
        fields["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    store.update_session(sid, **fields)
    store.touch_session(sid)
    ev = store.add_event("session_status", "server", "feed", project=s["project"],
                         task=s["task"], payload={"session_id": sid, "status": body.status,
                                                  "note": body.note, "report": body.report,
                                                  "error": body.error})
    hub.publish(ev)
    return store.get_session(sid)


@app.post("/api/sessions/{sid}/heartbeat")
async def session_heartbeat(sid: str):
    if not store.get_session(sid):
        raise HTTPException(404, f"сессия {sid} не найдена")
    store.touch_session(sid)
    return {"ok": True, "session_id": sid}


@app.post("/api/sessions/{sid}/instruction")
async def session_instruction(sid: str, body: MessageIn):
    """Контролёр -> субагент: инструкция в канал сессии (SSE session-<sid>)."""
    s = store.get_session(sid)
    if not s:
        raise HTTPException(404, f"сессия {sid} не найдена")
    msg = store.add_message(body.from_, SESSION_CHANNEL.format(sid=sid), body.text)
    ev = store.add_event("session_instruction", body.from_, SESSION_CHANNEL.format(sid=sid),
                         project=s["project"], task=s["task"],
                         payload={"session_id": sid, "chat": True})
    hub.publish({"id": msg["id"], "type": "session_instruction", "from": msg["from"],
                 "to": SESSION_CHANNEL.format(sid=sid), "text": msg["text"],
                 "created_at": msg["created_at"], "delivery": msg["delivery"],
                 "payload": {"session_id": sid, "chat": True, "event_id": ev["id"]}})
    return msg


@app.post("/api/sessions/{sid}/kill")
async def session_kill(sid: str):
    """Убить процесс субагента сессии (taskkill /F /T по зарегистрированному pid)."""
    s = store.get_session(sid)
    if not s:
        raise HTTPException(404, f"сессия {sid} не найдена")
    pid = s.get("pid")
    ok = False
    if pid is not None:
        ok = _kill_pid(int(pid))
    store.update_session(sid, status="killed",
                         finished_at=datetime.datetime.now().isoformat(timespec="seconds"),
                         error="убита по запросу" if ok else "не найден процесс")
    ev = store.add_event("session_status", "server", "feed", project=s["project"],
                         task=s["task"], payload={"session_id": sid, "status": "killed",
                                                  "pid": pid, "killed": ok})
    hub.publish(ev)
    return {"ok": ok, "session_id": sid, "pid": pid}
@app.get("/dashboard")
async def dashboard_page():
    from fastapi.responses import FileResponse
    if DASHBOARD.exists():
        return FileResponse(DASHBOARD, media_type="text/html; charset=utf-8")
    raise HTTPException(404, "dashboard.html не найден")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


def main():
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(description="Сервер координации dev-pipeline")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    uvicorn.run(app, host=a.host, port=a.port, reload=a.reload)


if __name__ == "__main__":
    main()
