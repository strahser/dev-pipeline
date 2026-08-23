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
from server.plan_api import router as plan_router, init as plan_api_init

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


# Белый список команд запуска агентов (имя -> argv). Единственный источник argv
# для рестарта: POST /heartbeat не должен превращать БД в магазин исполняемых
# команд (любой локальный процесс мог подложить cmd без авторизации).
AGENT_LAUNCH_COMMANDS: dict[str, list[str]] = {
    name: [sys.executable or "python", "-X", "utf8", "-m", module]
    for name, module in {
        "executor": "agents.executor_client",
        "plan-runner": "agents.plan_runner",
        "controller": "agents.agent_watch",
        "browser": "agents.browser_client",
        "agent-manager": "agents.agent_manager",
    }.items()
}


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
plan_api_init(store, hub)
app.include_router(plan_router)

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
    # cmd сохраняем только для имён из белого списка — остальные игнорируем
    cmd = body.cmd if body.agent in AGENT_LAUNCH_COMMANDS else ""
    store.heartbeat(body.agent, project=body.project, pid=body.pid, cmd=cmd)
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


# (legacy /api/activity «что происходит» удалён — вкладка давно на /api/pulse;
#  /api/activity теперь git-активность проекта, карточка 5.1)


@app.get("/api/projects")
async def api_projects():
    """Проекты + признак наличия плана (панель помечает ★ и выбирает их первыми)."""
    out = []
    for name in list_projects():
        try:
            cfg = load_config(name)
            has_plan = cfg.find_plan_file() is not None
        except ConfigError:
            has_plan = False
        out.append({"name": name, "has_plan": has_plan})
    return out


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
    """Перезапуск ТОЛЬКО по белому списку AGENT_LAUNCH_COMMANDS: сохранённая
    команда сравнивается с реестром дословно, исполняется argv из реестра."""
    try:
        argv = AGENT_LAUNCH_COMMANDS.get(name)
        if not argv:
            raise HTTPException(403, f"агент {name} отсутствует в белом списке запуска")
        a = _agent_proc(name)
        saved = ((a or {}).get("cmd") or "").strip()
        if not saved:
            raise HTTPException(404, f"у агента {name} нет команды запуска (cmd)")
        canonical = " ".join(argv)
        if saved != canonical:
            raise HTTPException(
                403, f"сохранённая команда {name} не совпадает с белым списком запуска")
        if a.get("pid") is not None:
            _kill_pid(int(a["pid"]))
        root = Path(__file__).resolve().parent.parent  # корень dev-pipeline
        _run_detached(list(argv), cwd=str(root))
        store.add_event("agent_restarted", "dashboard", "feed",
                        payload={"agent": name, "cmd": canonical})
        return {"ok": True, "agent": name, "cmd": canonical}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


class TerminalIn(BaseModel):
    project: str = ""          # пусто допустимо только для role=manager
    role: str = "executor"
    prompt: str = ""


@app.get("/api/chat/agents/auto_task")
async def chat_auto_task(project: str):
    """Автозадание проекта — предзаполнение окна «🖥 Терминал/🛡 Менеджер»,
    чтобы владелец видел и мог отредактировать то, что уйдёт агенту."""
    try:
        cfg = load_config(project)
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")
    from agents.tui_cycle import auto_task
    try:
        return {"project": cfg.name, "task": auto_task(cfg)}
    except Exception as e:
        return {"project": cfg.name, "task": "", "note": f"автозадание недоступно: {e}"}


@app.post("/api/chat/agents/terminal")
async def chat_agent_terminal(body: TerminalIn):
    """Открыть ВИДИМОЕ окно терминала с агентом (карточка 2.1 + ОС 2026-08-23).

    Любой запуск из панели = полноценное окно opencode TUI с автопромптом:
    role=manager — ОБЩИЙ менеджер на все проекты (project не нужен),
    остальные роли — agents/tui_cycle.py по проекту (порция = свежая сессия
    opencode, handoff -> /new-самоперезагрузка). Промпт — env
    PIPELINE_TUI_PROMPT. Открытие — pipeline.proc.spawn_visible (вкладка
    WezTerm / окно / консоль, внутри cmd /k)."""
    agents_dir = Path(__file__).resolve().parent.parent / "agents"
    py = sys.executable or "python"
    dp_root = Path(__file__).resolve().parent.parent

    if body.role == "manager":
        # ОБЩИЙ менеджер на ВСЕ проекты: приёмка работы + восстановление сессий
        script = agents_dir / "project_manager.py"
        base = [py, "-X", "utf8", str(script)]
        if body.project:
            base += ["--project", body.project]
        workdir = dp_root
        label = f"менеджер ({body.project or 'все проекты'})"
        project_out = body.project or ""
    else:
        if not body.project:
            raise HTTPException(400, "для роли нужен project")
        try:
            cfg = load_config(body.project)
        except ConfigError as e:
            raise HTTPException(404, f"проект не найден: {e}")
        from pipeline.crew import ensure_permissions
        try:
            ensure_permissions(cfg)
        except Exception:
            pass
        script = agents_dir / "tui_cycle.py"
        base = [py, "-X", "utf8", str(script),
                "--project", body.project, "--role", body.role]
        workdir = cfg.root
        label = f"агент ({body.role}) для {cfg.name}"
        project_out = cfg.name

    env = dict(os.environ)
    if body.prompt:
        env["PIPELINE_TUI_PROMPT"] = body.prompt

    env = dict(os.environ)
    if body.prompt:
        env["PIPELINE_TUI_PROMPT"] = body.prompt

    from pipeline.proc import spawn_visible
    ev_where = spawn_visible(base, workdir, env=env)

    ev = store.add_event("agent_terminal_opened", "server", "feed",
                         project=project_out, task="",
                         payload={"role": body.role, "terminal": ev_where})
    hub.publish(ev)
    return {"ok": True, "project": project_out, "role": body.role,
            "terminal": ev_where, "message": f"{label} запущен в {ev_where}"}


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
        # без кэша: панель часто обновляется вместе с бэкендом
        return FileResponse(DASHBOARD, media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-store"})
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
                   "(можешь запустить python -m pipeline.cli status {project})."),
    },
    "executor": {
        "title": "Исполнитель (Агент-2)",
        "skill": "pipeline-executor",
        "desc": "Выполняет задачи A-NN: правит код, собирает, тестирует, пишет отчёты с доказательствами.",
        "prompt": ("Ты — Агент-2 (исполнитель) конвейера dev-pipeline. "
                   "Прочитай скилл {skill_path} ПЕРВЫМ — там методика и твоя роль.\n"
                   "Твоя работа: выполнять задачи из Tasks\\Активные\\A-NN_*.md и карточек плана "
                   "(ProjectsPalns), собирать и тестировать проект, писать отчёты с доказательствами "
                   "в Tasks\\Отчёты.\n"
                   "Дай краткий ответ: подтверди роль и покажи, какие задачи сейчас открыты "
                   "(python -m pipeline.cli status {project})."),
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
                   "(python -m pipeline.cli status {project})."),
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
        "title": "Планировщик",
        "skill": "pipeline-planner",
        "desc": "Декомпозиция целей в планы ProjectsPalns (этапы/карточки с критериями).",
        "prompt": ("Ты — планировщик конвейера dev-pipeline. "
                   "Прочитай скилл {skill_path} ПЕРВЫМ — схема плана, правила декомпозиции.\n"
                   "Твоя работа: декомпозировать цель в план ProjectsPalns "
                   "(этапы → карточки с критериями приёмки и зависимостями).\n"
                   "Дай краткий ответ: подтверди роль и жди файл миссии."),
    },
}

def _skills_dir() -> Path:
    """Каталог скиллов: env PIPELINE_SKILLS_DIR, затем локальный skills/,
    репозиторий revit-skills (скиллы конвейера перенесены туда)."""
    env = os.environ.get("PIPELINE_SKILLS_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    here = Path(__file__).resolve().parent.parent
    candidates = [
        here / "skills",
        here.parent / "revit-skills" / ".opencode" / "skills",
        Path(r"D:\Projects\revit-skills\.opencode\skills"),
        Path(r"E:\ПлагиныРевит\agent-skills\.opencode\skills"),
    ]
    for cand in candidates:
        if cand.is_dir() and any(cand.glob("*/SKILL.md")):
            return cand
    return here / "skills"


SKILLS_DIR = _skills_dir()


class AgentIn(BaseModel):
    role: str
    project: str = ""
    model: str = ""
    task: str = ""


@app.get("/api/activity")
async def activity(project: str = "", days: int = 7, limit: int = 50):
    """Git-активность проекта (карточка 5.1): коммиты root + plan.repo."""
    try:
        cfg = load_config(project)
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")
    from pipeline.activity import collect, project_repos
    commits = collect(project_repos(cfg),
                      days=min(max(days, 1), 90), limit=min(max(limit, 1), 200))
    return {"project": cfg.name, "commits": commits}


@app.get("/api/crew/{project}")
async def crew_get(project: str):
    """Crew-конфиг проекта (роли/модель/права) для кнопки «▶ Поднять проект»."""
    try:
        cfg = load_config(project)
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")
    from pipeline.crew import load_crew
    return {"project": cfg.name, **load_crew(cfg)}


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
    elif body.project:
        # Существующий проект без явного задания: агент стартует не «в пустоту»,
        # а с автозаданием по контексту (план/задачи, обратная связь 2026-08-23).
        try:
            from agents.tui_cycle import auto_task
            prompt += "\n\nЗАДАНИЕ (авто, по контексту проекта):\n" + \
                auto_task(load_config(body.project))
        except Exception:
            pass
    sid = _sess_id()
    s = store.create_session(
        sid, project=body.project, task="", agent=f"agent-{role}-{sid[-4:]}",
        role=role, model=body.model or "", skill=skill,
        instruction={"prompt": prompt, "model": body.model or "", "skill": skill,
                     "role": role, "task": body.task or ""})

    # Карточка 1.2: сессия роли = ЖИВОЙ агент. Разворачиваем права opencode
    # (crew-профиль проекта, write по умолчанию) и поднимаем session_worker
    # detached от сервера. Любая ошибка — soft: создание сессии не ломаем.
    spawn_note = ""
    if body.project:
        try:
            cfg = load_config(body.project)
            from pipeline.crew import ensure_permissions
            perm = ensure_permissions(cfg)
            worker = Path(__file__).resolve().parent.parent / "agents" / \
                "session_worker.py"
            _run_detached([sys.executable or "python", "-X", "utf8", str(worker),
                           "--session", sid, "--project", cfg.name],
                          cwd=str(cfg.root))
            spawn_note = f"worker spawned ({perm})" if perm else "worker spawned"
        except Exception as e:
            spawn_note = f"spawn не удался: {e}"

    ev = store.add_event("session_created", "server", "feed", project=body.project,
                         task="", payload={"session_id": sid, "agent": s["agent"],
                                           "role": role, "agent_role": True,
                                           "spawn": spawn_note})
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
        return FileResponse(DASHBOARD, media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-store"})
    raise HTTPException(404, "dashboard.html не найден")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "head": _SERVER_HEAD}


def _git_head() -> str:
    """Короткий HEAD dev-pipeline — версия кода на диске."""
    try:
        import subprocess

        from pipeline.proc import no_window_flags
        r = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent.parent),
             "rev-parse", "--short=8", "HEAD"],
            capture_output=True, text=True, timeout=5,
            creationflags=no_window_flags())
        return (r.stdout or "").strip()
    except Exception:
        return ""


_SERVER_HEAD = _git_head()   # версия кода в момент старта сервера


@app.get("/api/meta")
async def api_meta():
    """Версия кода: при старте сервера vs сейчас на диске.

    Панель сверяет и показывает баннер «перезапустите сервер» (панель отдаёт
    свежий HTML с диска, а процесс держит старый код — классическая ловушка)."""
    return {"head_at_start": _SERVER_HEAD, "head_now": _git_head(),
            "started_at": _now_iso()}


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
