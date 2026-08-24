# -*- coding: utf-8 -*-
"""API планов ProjectsPalns, вопросов (grill), чекпоинтов и состояния план-раннера.

Источник данных — файл плана (pipeline/plans.py) + отчёты проекта + события сервера.

Новые эндпоинты:
    GET  /api/plan                      обзор текущего плана проекта
    GET  /api/plan/tasks                строки карточек (совместимо с таблицей панели)
    GET  /api/plan/filters              списки значений для фильтров панели
    GET  /api/plan/task/{task_id}       карточка+отчёт+вердикт (модальное окно)
    GET  /api/plan/running              выполняющиеся сейчас карточки (из событий)
    GET  /api/plan/durations            факт длительностей из событий (start->finish)
    GET  /api/plan/load                 заглушка бакетов (оценки не используются)
    GET  /api/questions                 открытые вопросы агентов (Tasks\Вопросы)
    POST /api/questions/{qid}/answer    ответ пользователя -> файл + git + SSE в канал сессии
    GET  /api/checkpoints               ожидающие одобрения чекпоинты раннера
    GET  /api/checkpoints/history       история решений по чекпоинтам (кто решил)
    POST /api/checkpoints/{cid}/approve одрбрить (продолжить)
    POST /api/checkpoints/{cid}/retry   перезапустить карточку
    GET  /api/runner                    состояние план-раннера (runner_state.json)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pipeline.config import ConfigError, load_config, list_projects
from pipeline.plans import load as load_plan, status_word

router = APIRouter()

_store = None
_hub = None


def init(store, hub):
    """Внедрение зависимостей сервера (вызывается из app.py до включения роутера)."""
    global _store, _hub
    _store, _hub = store, hub


class AnswerIn(BaseModel):
    project: str = ""
    text: str
    author: str = "owner"


# ---------------------------------------------------------------------------
# Вспомогательные
# ---------------------------------------------------------------------------

def _load_cfg_lazy(name: str):
    """Позднее связывание load_config (тесты подменяют его в server.app)."""
    import server.app as _app
    fn = getattr(_app, "load_config", None) or load_config
    return fn(name)


def _projects_for(project: str) -> list:
    return [project] if project else list_projects()


def _cfg_or_404(project: str):
    project = project or (list_projects() or [""])[0]
    try:
        return _load_cfg_lazy(project)
    except ConfigError as e:
        raise HTTPException(404, f"проект не найден: {e}")


def _plan_or_none(cfg):
    p = cfg.find_plan_file()
    return load_plan(p) if p else None


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _card_dates(card) -> dict:
    ds = _DATE_RE.findall(card.dates or "")
    return {
        "issued": ds[0] if ds else "",
        "start": "",
        "finish": ds[1] if len(ds) > 1 else "",
    }


def _wf_of(status: str) -> str:
    return {"open": "issued", "in_progress": "in_progress",
            "done": "verified", "cancelled": "rejected"}.get(status, "issued")


def _level_of(cid: str) -> int:
    core = cid.split("-", 1)[-1]
    return len([p for p in core.split(".") if p])


def _parent_wbs(cid: str) -> str:
    core = cid.split("-", 1)[-1]
    parts = core.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else ""


def _report_file(cfg, tid: str):
    rd = cfg.abs_tasks_dir("reports")
    if not rd.is_dir():
        return None
    files = sorted(rd.glob(f"{tid}_Отчёт_*.md"))
    return files[-1] if files else None


def _verdict_file(cfg, tid: str):
    rd = cfg.abs_tasks_dir("reports")
    if not rd.is_dir():
        return None
    files = sorted(rd.glob(f"{tid}_Вердикт_*.md"))
    return files[-1] if files else None


VERDICT_RE = re.compile(r"\*\*(PASS|FAIL|PARTIAL|NEED_DATA)\*\*")


def _verdict_result(cfg, tid: str) -> str:
    vf = _verdict_file(cfg, tid)
    if not vf:
        return ""
    try:
        m = VERDICT_RE.search(vf.read_text(encoding="utf-8", errors="replace"))
        return {"PASS": "pass", "FAIL": "fail"}.get(m.group(1), m.group(1)).lower() if m else ""
    except Exception:
        return ""


def _report_row_fields(cfg, tid: str) -> dict:
    rf = _report_file(cfg, tid)
    vf = _verdict_file(cfg, tid)
    return {
        "has_report": rf is not None,
        "has_verdict": vf is not None,
        "verdict_result": _verdict_result(cfg, tid),
        "report_path": str(rf.relative_to(cfg.root)) if rf else "",
        "evidence_count": len(re.findall(r"(?:EXIT 0|passed|Пройдено)", 
                                         rf.read_text(encoding="utf-8", errors="replace"))) if rf else 0,
    }


def _git_commit(root: Path, message: str) -> str:
    try:
        subprocess.run(["git", "-C", str(root), "add", "-A"],
                       capture_output=True, timeout=30,
                       creationflags=_nwf())
        r = subprocess.run(["git", "-C", str(root), "commit", "-m", message],
                           capture_output=True, text=True, timeout=30,
                           creationflags=_nwf())
        return (r.stdout or "").strip().splitlines()[-1][:12] if r.returncode == 0 else ""
    except Exception:
        return ""


def _nwf():
    from pipeline.proc import no_window_flags
    return no_window_flags()


def _publish(ev_type: str, to: str, project: str, task: str = "", payload: dict | None = None):
    if _store is None:
        return
    ev = _store.add_event(ev_type, "dashboard", to, project=project, task=task,
                          payload=payload or {})
    if _hub is not None:
        _hub.publish(ev)


def _sdr_kind(row: dict) -> str:
    k = (row.get("kind") or "").strip().lower()
    return "group" if k == "summary" else "execution"


# ---------------------------------------------------------------------------
# Строки плана (таблица панели)
# ---------------------------------------------------------------------------

def _plan_rows(cfg) -> list:
    plan = _plan_or_none(cfg)
    if plan is None:
        return []
    rows = []

    # 1) этапы из таблицы СДР (summary-строки без карточек)
    seen = set()
    valid_id = re.compile(r"^\d+(\.\d+)*$|^[A-Za-zА-Яа-яЁё]{1,12}-\d+$")
    for cid, row in plan.sdr_rows.items():
        if not valid_id.match(str(cid)):
            continue  # служебные/мусорные строки таблицы
        st = norm_status_safe(row.get("status_raw"))
        if st == "unknown":
            st = "open"
        card = plan.card(cid)
        if card is not None:
            continue  # карточка добавится отдельно
        seen.add(cid)
        rep = _report_row_fields(cfg, cid)
        rows.append(_row(cid, row.get("name", ""), st, kind="group",
                         level=_level_of(cid), parent="", description="",
                         dates={}, **rep))
    # 2) карточки
    for c in plan.cards:
        seen.add(c.id)
        rep = _report_row_fields(cfg, c.id)
        rows.append(_row(c.id, c.title, c.status,
                         kind="group" if c.is_stage else "execution",
                         level=max(2, _level_of(c.id)) if c.id[0].isdigit() else 2,
                         parent=_parent_wbs(c.id),
                         description=(c.goal or c.description)[:200],
                         dates=_card_dates(c),
                         priority="высокий",
                         deps=c.deps,
                         criteria=c.criteria,
                         links=c.links,
                         evidence_text=c.evidence,
                         layer=c.layer, module=c.module, checkpoint=c.checkpoint,
                         **rep))
    # 3) строки таблицы СДР без карточек и не-этапов пропускаем (уже выше)
    rows.sort(key=lambda r: _wbs_key(r["wbs_code"]))
    return rows


def norm_status_safe(raw: str) -> str:
    from pipeline.plans import norm_status
    try:
        v = norm_status(raw)
        return v if v in ("open", "in_progress", "done", "cancelled") else "unknown"
    except Exception:
        return "unknown"


def _wbs_key(w: str):
    parts = re.split(r"(\d+)", str(w))
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts)


def _row(tid, name, status, *, kind, level, parent, description, dates,
         has_report=False, has_verdict=False, verdict_result="", report_path="",
         evidence_count=0, priority="средний", deps=None, criteria=None,
         links="", evidence_text="", layer="", module="", checkpoint=False) -> dict:
    return {
        "task_id": str(tid),
        "path": report_path,
        "wbs_code": str(tid),
        "parent_wbs": parent,
        "level": level,
        "is_summary": kind == "group",
        "task_kind": kind,
        "name": name,
        "description": description,
        "status": "done" if status == "done" else "open",
        "status_raw": status_word(status),
        "workflow_state": _wf_of(status),
        "priority": priority,
        "module": module,
        "class_name": "",
        "layer": layer,
        "deps": deps or [],
        "criteria": criteria or [],
        "links_raw": links,
        "evidence_text": evidence_text,
        "checkpoint": bool(checkpoint),
        "dates": dates or {},
        "has_report": has_report,
        "has_verdict": has_verdict,
        "verdict_result": verdict_result,
        "links_count": len(re.findall(r"https?://", links or "")),
        "evidence_count": evidence_count,
    }


# ---------------------------------------------------------------------------
# Эндпоинты плана
# ---------------------------------------------------------------------------

@router.get("/api/plan/meta")
async def plan_meta(project: str = ""):
    """Лёгкие метаданные плана: найден ли файл, заголовок, прогресс (для баннера панели)."""
    cfg = _cfg_or_404(project)
    pf = cfg.find_plan_file()
    out = {"project": cfg.name, "found": bool(pf), "file": str(pf) if pf else ""}
    if pf:
        from pipeline.plans import load as load_plan
        pl = load_plan(pf)
        out.update({"title": pl.title[:140], "progress": pl.progress()})
    return out


@router.get("/api/plan")
async def plan_overview(project: str = ""):
    cfg = _cfg_or_404(project)
    plan = _plan_or_none(cfg)
    pf = cfg.find_plan_file()
    out = {
        "project": cfg.name,
        "plan_file": str(pf) if pf else "",
        "total": 0, "done": 0, "rows": [], "active": [], "archive": [],
        "working": _working_cards(cfg),
    }
    if plan is None:
        out["error"] = "план не найден (plan.repo/subdir/file в pipeline.yaml или папка _current пуста)"
        return out
    rows = _plan_rows(cfg)
    done = [r for r in rows if r["status"] == "done"]
    active = [r for r in rows if r["status"] != "done"]
    out.update({"total": len(rows), "done": len(done), "rows": rows,
                "active": active, "archive": done})
    return out


def _working_cards(cfg) -> list:
    import datetime
    evs = _store.recent_events(limit=300, project=cfg.name) if _store else []
    started: dict[str, str] = {}
    finished: set[str] = set()
    for e in evs:
        tid = e.get("task") or ""
        if not tid:
            continue
        if e["type"] == "task_started":
            started[tid] = e["created_at"]
        elif e["type"] in ("subagent_finished", "session_status"):
            finished.add(tid)
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=6)).isoformat()
    return [{"task": tid, "since": ts} for tid, ts in started.items()
            if tid not in finished and ts >= cutoff]


@router.get("/api/plan/tasks")
async def plan_tasks(project: str = ""):
    cfg = _cfg_or_404(project)
    return _plan_rows(cfg)


@router.get("/api/plan/filters")
async def plan_filters(project: str = ""):
    cfg = _cfg_or_404(project)
    rows = _plan_rows(cfg)

    def _to_list(cnt: dict) -> list:
        return [{"value": k, "count": v} for k, v in
                sorted(cnt.items(), key=lambda x: (-x[1], x[0]))]

    statuses: dict = {}
    workflows: dict = {}
    kinds: dict = {}
    modules: dict = {}
    for r in rows:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
        workflows[r["workflow_state"]] = workflows.get(r["workflow_state"], 0) + 1
        if r["module"]:
            modules[r["module"]] = modules.get(r["module"], 0) + 1
        kinds[r["task_kind"]] = kinds.get(r["task_kind"], 0) + 1
    return {
        "statuses": _to_list(statuses),
        "workflow_states": _to_list(workflows),
        "task_kinds": _to_list(kinds),
        "modules": _to_list(modules),
        "class_names": [],
        "layers": [],
    }


@router.get("/api/plan/task/{task_id}")
async def plan_task(task_id: str, project: str = ""):
    cfg = _cfg_or_404(project)
    plan = _plan_or_none(cfg)
    if plan is None:
        raise HTTPException(404, "план не найден")
    card = plan.card(task_id)
    if card is None:
        raise HTTPException(404, f"карточка {task_id} не найдена в плане")
    rep_f = _report_file(cfg, task_id)
    ver_f = _verdict_file(cfg, task_id)
    rep_txt = rep_f.read_text(encoding="utf-8", errors="replace") if rep_f else ""
    ver_txt = ver_f.read_text(encoding="utf-8", errors="replace") if ver_f else ""
    task = {
        "task_id": card.id, "wbs_code": card.id, "name": card.title,
        "goal": card.goal, "description": card.description,
        "acceptance_criteria": card.criteria,
        "inputs": ([{"type": "файлы", "path": i.strip()} for i in card.inputs.split(";")] if card.inputs else []),
        "links": ([{"type": "ссылка", "href": h.strip()} for h in re.findall(r"https?://\S+", card.links)]),
        "history": [], "blocker": "",
        "is_summary": card.is_stage, "task_kind": "group" if card.is_stage else "execution",
        "priority": "высокий", "module": card.module, "class_name": "", "layer": card.layer,
        "status": "done" if card.status == "done" else "open",
        "workflow_state": _wf_of(card.status),
        "dates": _card_dates(card),
    }
    report = None
    if rep_f:
        work_done = re.findall(r"^##\s+Что сделано\s*\n(.*?)(?=\n##\s|\Z)", rep_txt, re.S | re.M)
        report = {
            "report_status": "done",
            "date": (_DATE_RE.search(rep_f.name).group(0) if _DATE_RE.search(rep_f.name) else ""),
            "executor": {"name": "subagent"},
            "work_done": (work_done[-1].strip().splitlines()[:20] if work_done else []),
            "evidence": [{"evidence_id": f"E-{i}", "type": "text", "result": "pass",
                          "details": ln.strip()} for i, ln in enumerate(
                              [l for l in rep_txt.splitlines() if l.strip()][:8], 1)],
            "verification_commands": card.criteria,
            "_text": rep_txt,
        }
    verdict = None
    if ver_f:
        m = VERDICT_RE.search(ver_txt)
        result = (m.group(1) if m else "UNKNOWN").lower()
        verdict = {
            "result": result, "can_move_forward": result in ("pass", "partial"),
            "date": (_DATE_RE.search(ver_f.name).group(0) if _DATE_RE.search(ver_f.name) else ""),
            "checks": [{"check_id": f"C-{i}", "name": ln.strip()[:80], "status":
                        "pass" if result == "pass" else "warn",
                        "expected": "—", "actual": "—"}
                       for i, ln in enumerate([l for l in ver_txt.splitlines() if l.strip()][:8], 1)],
            "_text": ver_txt,
        }
    evs = [e for e in (_store.recent_events(limit=500, project=cfg.name) if _store else [])
           if (e.get("task") or "") == task_id]
    return {
        "task": task, "report": report, "verdict": verdict,
        "events": evs,
        "markdown": {"task_card": "", "report": rep_txt, "verdict": ver_txt},
        "sources": {
            "task": "",
            "report": str(rep_f) if rep_f else "",
            "verdict": str(ver_f) if ver_f else "",
        },
    }


@router.get("/api/plan/running")
async def plan_running(project: str = ""):
    cfg = _cfg_or_404(project)
    import datetime
    evs = _store.recent_events(limit=500, project=cfg.name) if _store else []
    started: dict = {}
    finished: set = set()
    for e in evs:
        tid = e.get("task") or ""
        if not tid:
            continue
        if e["type"] == "task_started":
            started[tid] = e["created_at"]
        elif e["type"] == "subagent_finished":
            finished.add(tid)
    now = datetime.datetime.now()
    out = []
    for tid, ts in started.items():
        if tid in finished:
            continue
        try:
            st = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if st.tzinfo is not None:
                st = st.astimezone().replace(tzinfo=None)
            out.append({"task_id": tid, "name": "", "elapsed_sec":
                        max(0, int((now - st).total_seconds()))})
        except ValueError:
            continue
    out.sort(key=lambda x: -x["elapsed_sec"])
    return out


@router.get("/api/plan/durations")
async def plan_durations(project: str = ""):
    """Факт длительности карточек из событий task_started/subagent_finished."""
    import datetime
    cfg = _cfg_or_404(project)
    plan = _plan_or_none(cfg)
    if plan is None:
        return {"project": cfg.name, "tasks": [], "summary": {}}
    evs = _store.recent_events(limit=1000, project=cfg.name) if _store else []
    spans: dict[str, list] = {}
    for e in evs:
        tid = e.get("task") or ""
        if not tid:
            continue
        if e["type"] == "task_started":
            spans.setdefault(tid, []).append([e["created_at"], None])
        elif e["type"] == "subagent_finished" and tid in spans:
            for pair in reversed(spans[tid]):
                if pair[1] is None:
                    pair[1] = e["created_at"]
                    break
    now = datetime.datetime.now()

    def dur(tid: str):
        total = 0
        for s, f in spans.get(tid, []):
            try:
                sd = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
                fd = datetime.datetime.fromisoformat(str(f).replace("Z", "+00:00")) if f else now
                if sd.tzinfo is not None:
                    sd = sd.astimezone().replace(tzinfo=None)
                if fd.tzinfo is not None:
                    fd = fd.astimezone().replace(tzinfo=None)
                total += max(0, int((fd - sd).total_seconds()))
            except ValueError:
                continue
        return total or None

    rows = []
    for c in plan.cards:
        d = dur(c.id)
        rows.append({
            "task_id": c.id, "name": c.title, "wbs_code": c.id,
            "level": max(2, _level_of(c.id)), "is_summary": c.is_stage,
            "status": "done" if c.status == "done" else "open",
            "workflow_state": _wf_of(c.status),
            "start": spans.get(c.id, [[None]])[0][0] or "",
            "finish": "", "estimate_sec": None, "duration_sec": d,
            "delta_sec": None, "over_plan": False,
        })
    rows.sort(key=lambda r: _wbs_key(r["wbs_code"]))
    fact_total = sum(r["duration_sec"] or 0 for r in rows)
    return {"project": cfg.name, "tasks": rows,
            "summary": {"total": len(rows),
                        "done": sum(1 for r in rows if r["status"] == "done"),
                        "plan_sec": 0, "fact_sec": fact_total, "over_plan": 0}}


@router.get("/api/plan/load")
async def plan_load(project: str = "", period: str = "day", buckets: int = 14):
    cfg = _cfg_or_404(project)
    return {"project": cfg.name, "period": period, "buckets": []}


# ---------------------------------------------------------------------------
# Вопросы агентов (grill-фаза): Tasks\Вопросы\<qid>.md
# ---------------------------------------------------------------------------

ANSWERS_MARK = "## Ответы"


def _q_path(cfg, qid: str) -> Path:
    return cfg.questions_dir() / f"{qid}.md"


# ---------------------------------------------------------------------------
# Project Brief (Уровень 1): авто-дайджест контекста для субагента
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Пульс проекта (главный экран панели вместо таблицы задач)
# ---------------------------------------------------------------------------

@router.get("/api/pulse")
async def api_pulse(project: str = ""):
    cfg = _cfg_or_404(project)
    out = {"project": cfg.name}

    # план и этапы
    plan = _plan_or_none(cfg)
    pf = cfg.find_plan_file()
    out["found"] = bool(pf)
    if plan:
        out["plan_title"] = plan.title[:140]
        prog = plan.progress()
        out["overall"] = prog
        stages, order = {}, []
        for c in plan.execution_cards():
            core = c.id.split("-", 1)[-1]
            s = c.id.rsplit(".", 1)[0] if "." in core else "—"
            if s not in stages:
                stages[s] = {"stage": s, "done": 0, "total": 0}
                order.append(s)
            stages[s]["total"] += 1
            if c.status == "done":
                stages[s]["done"] += 1
        out["stages"] = [stages[k] for k in order]

    # конвейер
    sf = cfg.conveyor_dir() / "runner_state.json"
    if sf.exists():
        try:
            out["runner"] = json.loads(sf.read_text(encoding="utf-8"))
        except Exception:
            out["runner"] = None

    # сессии в работе
    out["sessions_running"] = len([s for s in (_store.list_sessions() if _store else [])
                                   if s.get("status") in ("running", "created")])

    # вопросы (открытые)
    q = await questions_list(project=cfg.name)
    open_q = [x for x in q if not x.get("answered")]
    out["questions"] = [{"id": x["id"], "question": x["question"],
                         "card": x.get("card", ""), "age_min": x.get("age_min")}
                        for x in open_q[:5]]
    out["questions_total"] = len(open_q)

    # чекпоинты
    cps = await checkpoints_list(project=cfg.name)
    out["checkpoints"] = cps[:5]
    out["checkpoints_total"] = len(cps)

    # зависшие
    sd = cfg.conveyor_dir() / "stalled"
    out["stalled"] = [p.stem for p in sorted(sd.glob("*.txt"))] if sd.is_dir() else []

    # лента активности
    out["activity"] = (_store.activity(limit=14, project=cfg.name) if _store else [])

    # коммиты
    try:
        r = subprocess.run(["git", "-C", str(cfg.root), "log", "--oneline", "-5"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=10,
                           creationflags=_nwf())
        out["commits"] = [l for l in (r.stdout or "").splitlines() if l.strip()]
    except Exception:
        out["commits"] = []

    return out


# ---------------------------------------------------------------------------
# Пульс по ВСЕМ проектам (главный экран)
# ---------------------------------------------------------------------------

@router.get("/api/pulse_all")
async def api_pulse_all():
    """Карточки всех проектов: прогресс этапов (с названиями), время работы,
    работает/завис, открытые вопросы и чекпоинты."""
    import datetime as _dt
    items = []
    for name in list_projects():
        try:
            cfg = _load_cfg_lazy(name)
        except ConfigError:
            continue
        it = {"project": name}

        pf = cfg.find_plan_file()
        it["found"] = bool(pf)
        plan = _plan_or_none(cfg)
        if plan:
            it["plan_title"] = plan.title[:120]
            it["overall"] = plan.progress()
            stages, order = {}, []
            for c in plan.execution_cards():
                core = c.id.split("-", 1)[-1]
                sid = c.id.rsplit(".", 1)[0] if "." in core else ""
                if not sid:
                    continue
                if sid not in stages:
                    trow = plan.sdr_rows.get(sid)
                    scard = plan.card(sid)
                    tname = (trow or {}).get("name") or (scard.title if scard else "")
                    tname = re.sub(r"^\s*Этап\s*\d+[.:]?\s*", "", str(tname)).strip()
                    stages[sid] = {"stage": sid,
                                   "title": (tname or ("Этап " + sid))[:70],
                                   "done": 0, "total": 0}
                    order.append(sid)
                stages[sid]["total"] += 1
                if c.status == "done":
                    stages[sid]["done"] += 1
            it["stages"] = [stages[k] for k in order]
        else:
            it["stages"] = []

        sf = cfg.conveyor_dir() / "runner_state.json"
        rn = None
        if sf.exists():
            try:
                rn = json.loads(sf.read_text(encoding="utf-8"))
            except Exception:
                rn = None
        it["runner"] = ({k: rn[k] for k in ("phase", "card", "attempt", "note", "title")
                         if k in rn} if rn else None)

        # время работы из событий конвейера
        evs = _store.recent_events(limit=1000, project=cfg.name) if _store else []
        started = {}
        total_sec = 0.0
        run_sec = None
        run_card = None
        now = _dt.datetime.now()

        def pdt(v):
            try:
                d = _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
            except ValueError:
                return None

        for e in evs:
            tid = e.get("task") or ""
            if not tid:
                continue
            if e["type"] == "task_started":
                started.setdefault(tid, e["created_at"])
            elif e["type"] == "subagent_finished" and tid in started:
                s = pdt(started.pop(tid))
                f = pdt(e["created_at"])
                if s and f:
                    total_sec += max(0, (f - s).total_seconds())
        for tid, ts in started.items():   # незакрытый интервал = работает сейчас
            s = pdt(ts)
            if s:
                sec = max(0, int((now - s).total_seconds()))
                if run_sec is None or sec > run_sec:
                    run_sec, run_card = sec, tid
        it["work_sec"] = int(total_sec)
        it["running_sec"] = run_sec
        it["running_card"] = run_card

        q = await questions_list(project=name)
        it["questions_open"] = len([x for x in q if not x.get("answered")])
        cps = await checkpoints_list(project=name)
        it["checkpoints_open"] = len(cps)
        sd = cfg.conveyor_dir() / "stalled"
        it["stalled"] = [p.stem for p in sorted(sd.glob("*.txt"))] if sd.is_dir() else []

        items.append(it)
    return {"projects": items}


@router.get("/api/brief")
async def api_brief(project: str = "", card: str = ""):
    """Markdown-дайджест проекта: план/этап, коммиты, артефакты, вопросы, конвейер."""
    cfg = _cfg_or_404(project)
    from pipeline.brief import build_brief
    card_obj = None
    plan = _plan_or_none(cfg)
    if plan and card:
        card_obj = plan.card(card)
    md = build_brief(cfg, card=card_obj)
    return {"project": cfg.name, "card": card, "brief": md}


@router.get("/api/questions")
async def questions_list(project: str = ""):
    out = []
    for pname in _projects_for(project):
        try:
            cfg = _load_cfg_lazy(pname)
        except ConfigError:
            continue
        qd = cfg.questions_dir()
        if not qd.is_dir():
            continue
        for f in sorted(qd.glob("*.md"), key=lambda p: -p.stat().st_mtime):
            txt = f.read_text(encoding="utf-8", errors="replace")
            answered = ANSWERS_MARK in txt and txt.split(ANSWERS_MARK, 1)[1].strip()
            ms = re.search(r"^(?:сессия|session)\s*:\s*(\S+)", txt, re.M)
            mc = re.search(r"^(?:карточка|card|задача)\s*:\s*(\S+)", txt, re.M)
            ma = re.search(r"^автор\s*:\s*(.+)$", txt, re.M)
            mt = re.search(r"^#\s+(.+)$", txt, re.M)
            out.append({
                "id": f.stem, "project": pname, "file": str(f),
                "question": (mt.group(1).strip() if mt else f.stem)[:160],
                "card": mc.group(1) if mc else "",
                "session": ms.group(1) if ms else "",
                "author": (ma.group(1).strip() if ma else ""),
                "answered": bool(answered),
                "age_min": int((datetime.now().timestamp() - f.stat().st_mtime) / 60),
                "content": txt[:4000],
            })
    return out


@router.post("/api/questions/{qid}/answer")
async def question_answer(qid: str, body: AnswerIn):
    cfg = _cfg_or_404(body.project)
    qp = _q_path(cfg, qid)
    if not qp.exists():
        raise HTTPException(404, f"вопрос {qid} не найден ({qp})")
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "пустой ответ")
    author = (body.author or "").strip() or "owner"
    txt = qp.read_text(encoding="utf-8")
    tail = txt.split(ANSWERS_MARK, 1)[1].strip() if ANSWERS_MARK in txt else ""
    if tail:
        raise HTTPException(409, "на вопрос уже есть ответ")
    if ANSWERS_MARK in txt:
        # пустой плейсхолдер — дописываем под него
        head = txt.split(ANSWERS_MARK, 1)[0]
    else:
        head = txt.rstrip()
    qp.write_text(head.rstrip() + f"\n\n{ANSWERS_MARK}\n\n{text}\n\nавтор: {author}\n",
                  encoding="utf-8")
    commit = _git_commit(cfg.root, f"questions: ответ на вопрос {qid}")
    # уведомить сессию субагента (если указана) и ленту
    ms = re.search(r"^(?:сессия|session)\s*:\s*(\S+)", txt, re.M)
    sid = (ms.group(1) if ms else "").strip()
    if sid:
        msg = _store.add_message("dashboard", f"session-{sid}",
                                 f"ОТВЕТ на {qid} ({author}): {text}") if _store else None
        if _hub is not None and msg is not None:
            _hub.publish({"id": msg["id"], "type": "question_answered",
                          "from": "dashboard", "to": f"session-{sid}",
                          "text": msg["text"], "created_at": msg["created_at"],
                          "delivery": msg["delivery"],
                          "payload": {"question": qid, "author": author, "chat": True}})
    _publish("question_answered", "feed", cfg.name,
             payload={"question": qid, "author": author})
    return {"ok": True, "question": qid, "answer": text, "author": author,
            "commit": commit}


# ---------------------------------------------------------------------------
# Чекпоинты план-раннера: Tasks\Конвейер\checkpoints\<cid>.pending.json
# ---------------------------------------------------------------------------

def _cp_dir(cfg) -> Path:
    return cfg.conveyor_dir() / "checkpoints"


@router.get("/api/checkpoints")
async def checkpoints_list(project: str = ""):
    out = []
    for pname in _projects_for(project):
        try:
            cfg = _load_cfg_lazy(pname)
        except ConfigError:
            continue
        d = _cp_dir(cfg)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.pending.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            out.append({"id": f.stem.replace(".pending", ""), "project": pname,
                        **data})
    return out


class CpAction(BaseModel):
    project: str = ""
    comment: str = ""
    actor: str = "owner"


def _checkpoint_action(cid: str, body: CpAction, action: str):
    cfg = _cfg_or_404(body.project)
    d = _cp_dir(cfg)
    pend = d / f"{cid}.pending.json"
    if not pend.exists():
        raise HTTPException(404, f"чекпоинт {cid} не найден или уже обработан")
    try:
        data = json.loads(pend.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    actor = (body.actor or "").strip() or "owner"
    decided_at = datetime.now().isoformat(timespec="seconds")
    data.update({"action": action, "comment": body.comment, "actor": actor,
                 "decided_at": decided_at,
                 "decision": "approved" if action == "approve" else "retry"})
    (d / f"{cid}.decision.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    pend.unlink(missing_ok=True)
    # история решений (аудит): decision.json потребляется раннером однократно,
    # поэтому «кто решил» дублируем в _history.jsonl (файлы = источник правды)
    try:
        with open(d / "_history.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"checkpoint": cid, "card": data.get("card", ""),
                                 "action": action,
                                 "decision": data["decision"],
                                 "comment": body.comment[:300], "actor": actor,
                                 "decided_at": decided_at},
                                ensure_ascii=False) + "\n")
    except Exception:
        pass
    _publish(f"checkpoint_{action}", "feed", cfg.name,
             task=data.get("card", ""),
             payload={"checkpoint": cid, "actor": actor,
                      "comment": (body.comment or "")[:200]})
    return {"ok": True, "checkpoint": cid, "action": action, "actor": actor}


@router.post("/api/checkpoints/{cid}/approve")
async def checkpoint_approve(cid: str, body: CpAction):
    return _checkpoint_action(cid, body, "approve")


@router.post("/api/checkpoints/{cid}/retry")
async def checkpoint_retry(cid: str, body: CpAction):
    return _checkpoint_action(cid, body, "retry")


@router.get("/api/checkpoints/history")
async def checkpoints_history(project: str = ""):
    """История решений по чекпоинтам (_history.jsonl): кто решил, когда, что."""
    out = []
    for pname in _projects_for(project):
        try:
            cfg = _load_cfg_lazy(pname)
        except ConfigError:
            continue
        hf = _cp_dir(cfg) / "_history.jsonl"
        if not hf.exists():
            continue
        for line in hf.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append({"project": pname, **json.loads(line)})
            except Exception:
                continue
    out.sort(key=lambda x: str(x.get("decided_at", "")), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Состояние план-раннера: Tasks\Конвейер\runner_state.json
# ---------------------------------------------------------------------------

@router.get("/api/runner")
async def runner_state(project: str = ""):
    cfg = _cfg_or_404(project)
    sf = cfg.conveyor_dir() / "runner_state.json"
    if not sf.exists():
        return {"project": cfg.name, "state": None}
    try:
        state = json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        state = None
    return {"project": cfg.name, "state": state}
