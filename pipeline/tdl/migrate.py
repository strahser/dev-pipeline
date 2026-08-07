# -*- coding: utf-8 -*-
"""TDL: миграция legacy Markdown (задачи/отчёты/вердикты) в TDL JSON.

Консервативные правила (docs/План реализации.txt §22.4):
- старый Markdown НЕ удаляется;
- TDL status не становится done только из legacy-статуса;
- без JSON-отчёта и JSON-вердикта задача остаётся open;
- legacy-статус сохраняется в legacy_status.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

from . import store
from .schema import LEGACY_TO_WORKFLOW, WORKFLOW_TO_STATUS
from .validate import can_close_task


def _wf(legacy_status: str) -> str:
    return LEGACY_TO_WORKFLOW.get(legacy_status, "issued")


def _date_str() -> str:
    return datetime.date.today().isoformat()


def migrate_task_file(cfg, path: Path, dry_run: bool = False) -> dict:
    """Перенести Markdown-задачу в JSON. Возвращает {'task_id', 'created', 'wbs', 'status'}."""
    txt = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    meta = _parse_frontmatter(txt)
    task_id = meta.get("id") or path.name.split("_")[0]
    existing = store.load_task(cfg, task_id)
    if existing:
        return {"task_id": task_id, "created": False, "wbs": existing.get("wbs_code", ""),
                "status": existing.get("status")}

    legacy = meta.get("статус", "open")
    wf = _wf(legacy)
    wbs = _wbs_from_mission(meta) or f"1.{_int(task_id)}"
    title = _title_from_md(txt) or meta.get("title") or path.name
    goal = _section(txt, "Цель") or _section(txt, "Требования") or _section(txt, "Контекст") or ""
    task = {
        "schema_version": "1.0.0",
        "entity_type": "tdl.task",
        "task_id": task_id,
        "external_ids": [path.name],
        "wbs_code": wbs,
        "parent_wbs": wbs.rsplit(".", 1)[0] if "." in wbs else "",
        "is_summary": False,
        "project": {"code": cfg.name, "name": cfg.name},
        "name": title,
        "description": _section(txt, "Контекст") or goal,
        "status": WORKFLOW_TO_STATUS.get(wf, "open"),
        "status_display": "Выполнено" if wf in ("verified", "archived") else "Открыто",
        "workflow_state": wf,
        "legacy_status": legacy,
        "priority": meta.get("приоритет", "средний"),
        "task_kind": "execution",
        "goal": goal,
        "context": _section(txt, "Контекст") or "",
        "acceptance_criteria": _criteria(_section(txt, "Требования")),
        "constraints": _criteria(_section(txt, "Границы")),
        "dates": {"issued": meta.get("дата", _date_str()),
                  "start": meta.get("дата", ""), "finish": None},
        "assignees": {"issued_by": meta.get("постановщик", "агент-менеджер"),
                      "executor": meta.get("исполнитель", "subagent"),
                      "controller": "controller"},
        "source": {"type": "mission", "name": meta.get("замечание", "")},
        "links": [{"type": "task_source", "href": "file://" + str(path.relative_to(cfg.root)).replace("\\", "/")}],
        "artifacts": [],
        "verification": {"required": True, "method": "build_and_tests_plus_controller_review",
                         "commands": _verification_commands(cfg), "baseline": {}, "result": None,
                         "verified_by": None, "verdict_ref": None},
        "history": [{"timestamp": _date_str() + "T00:00:00Z", "actor": "tdl_migration",
                     "action": "legacy_status_imported", "legacy_status": legacy,
                     "details": f"Старый Markdown-статус {legacy} сохранён; TDL-статус = {WORKFLOW_TO_STATUS.get(wf)} до появления JSON-отчёта и JSON-вердикта."}],
        "progress_log": [],
        "blocker": "",
    }
    if not dry_run:
        store.save_task(cfg, task)
        store.rebuild_index(cfg)
    return {"task_id": task_id, "created": True, "wbs": wbs, "status": task["status"]}


def migrate_report_file(cfg, path: Path, dry_run: bool = False) -> dict | None:
    """Перенести Markdown-отчёт в JSON (report_status=migrated)."""
    txt = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    m = re.match(r"(A-\d+)", path.name)
    if not m:
        return None
    task_id = m.group(1)
    date = _date_from_name(path.name) or _date_str()
    rid = f"{task_id}_{date}"
    if store.latest_report_path(cfg, task_id):
        return {"report_id": rid, "created": False}
    report = {
        "schema_version": "1.0.0", "entity_type": "tdl.report",
        "report_id": rid, "task_ref": task_id,
        "wbs_ref": (store.load_task(cfg, task_id) or {}).get("wbs_code", ""),
        "date": date, "report_status": "migrated",
        "executor": {"name": "subagent", "agent": "", "role": ""},
        "problem": _section(txt, "Что было не так") or "",
        "work_done": _bullets(_section(txt, "Что сделано")),
        "files_changed": [],
        "metrics": _metrics_from_md(_section(txt, "Числа до/после")),
        "evidence": _evidence_from_md(_section(txt, "Доказательства")),
        "open_questions": _bullets(_section(txt, "Открытые вопросы")),
        "verification_commands": _bullets(_section(txt, "Как пересобрать/проверить")) or _verification_commands(cfg),
        "links": [{"type": "task", "href": f"file://Tasks/JSON/Active/{task_id}.task.json"},
                  {"type": "report_markdown", "href": "file://" + str(path.relative_to(cfg.root)).replace("\\", "/")}],
    }
    if not dry_run:
        store.save_report(cfg, report)
    return {"report_id": rid, "created": True, "evidence": len(report["evidence"])}


def migrate_verdict_file(cfg, path: Path, dry_run: bool = False, allow_done_from_md: bool = False) -> dict | None:
    """Перенести Markdown-вердикт в JSON. Без флага --allow-done-from-md — только pending."""
    txt = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    m = re.match(r"(A-\d+)", path.name)
    if not m:
        return None
    task_id = m.group(1)
    date = _date_from_name(path.name) or _date_str()
    vid = f"{task_id}_{date}"
    if store.latest_verdict_path(cfg, task_id):
        return {"verdict_id": vid, "created": False}
    md_result = re.search(r"\*\*(PASS|FAIL|PARTIAL|NEED_DATA)\*\*", txt)
    md_pass = md_result and md_result.group(1) == "PASS"
    if not allow_done_from_md or not md_pass:
        result = "pending"
    else:
        result = "pass"
    verdict = {
        "schema_version": "1.0.0", "entity_type": "tdl.verdict",
        "verdict_id": vid, "task_ref": task_id,
        "report_ref": f"{task_id}_{date}", "date": date,
        "result": result, "confidence": "medium",
        "can_move_forward": result == "pass",
        "checks": _checks_from_md(txt),
        "evidence_review": [], "required_fixes": [],
        "notes": ["Мигрирован из Markdown-вердикта."],
        "links": [{"type": "task", "href": f"file://Tasks/JSON/Active/{task_id}.task.json"}],
    }
    if not dry_run:
        store.save_verdict(cfg, verdict)
        # при allow_done_from_md и pass — попробовать закрыть задачу
        if allow_done_from_md and result == "pass":
            task = store.load_task(cfg, task_id)
            if task:
                report = store.load_report(cfg, task_id)
                ok, _ = can_close_task(task, report, verdict)
                if ok:
                    task["status"] = "done"
                    task["workflow_state"] = "verified"
                    task["status_display"] = "Выполнено"
                    store.save_task(cfg, task)
    return {"verdict_id": vid, "created": True, "result": result}


def migrate_project(cfg, dry_run: bool = False, allow_done_from_md: bool = False) -> dict:
    """Мигрировать весь legacy Tasks/ в TDL JSON."""
    result = {"tasks": [], "reports": [], "verdicts": [], "dry_run": dry_run}
    for folder_key in ("active", "archive"):
        folder = cfg.abs_tasks_dir(folder_key)
        if folder.is_dir():
            for f in sorted(folder.glob("A-*.md")):
                result["tasks"].append(migrate_task_file(cfg, f, dry_run))
    rd = cfg.abs_tasks_dir("reports")
    if rd.is_dir():
        for f in sorted(rd.glob("*_Отчёт_*.md")):
            r = migrate_report_file(cfg, f, dry_run)
            if r:
                result["reports"].append(r)
        for f in sorted(rd.glob("*_Вердикт_*.md")):
            r = migrate_verdict_file(cfg, f, dry_run, allow_done_from_md)
            if r:
                result["verdicts"].append(r)
    if not dry_run:
        store.rebuild_index(cfg)
    return result


# ---------- helpers ----------

def _parse_frontmatter(text: str) -> dict:
    m = re.search(r"^---(.*?)^---$", text, re.S | re.M)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta


def _section(text: str, heading: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    m = re.search(rf"^#{{1,4}}\s*{re.escape(heading)}.*?\n(.*?)(?=^#{{1,4}}\s|\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


def _bullets(text: str) -> list[str]:
    if not text:
        return []
    return [re.sub(r"^\s*[-*]\s*", "", line).strip() for line in text.splitlines() if line.strip()]


def _criteria(text: str) -> list[str]:
    return _bullets(text) or ([text] if text else [])


def _title_from_md(text: str) -> str:
    m = re.search(r"^#\s*ЗАДАЧА:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def _int(task_id: str) -> int:
    m = re.search(r"(\d+)", task_id or "")
    return int(m.group(1)) if m else 0


def _wbs_from_mission(meta: dict) -> str:
    src = meta.get("источник_запроса", "") or ""
    m = re.search(r"часть\s*(\d+)/(\d+)", src)
    if m:
        return f"1.{int(m.group(1))}"
    return ""


def _date_from_name(name: str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return m.group(1) if m else ""


def _metrics_from_md(text: str) -> list[dict]:
    if not text:
        return []
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]?\s*([\w\sа-яА-ЯёЁ()\-/]+?)\s*[:|]\s*(\S+)\s*(?:→|->)\s*(\S+)", line)
        if m:
            out.append({"metric_id": "m" + str(len(out) + 1), "name": m.group(1).strip(),
                        "unit": "count", "before": m.group(2), "after": m.group(3)})
    return out


def _evidence_from_md(text: str) -> list[dict]:
    if not text:
        return []
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        line = re.sub(r"^\s*[-*]\s*", "", line).strip()
        if line:
            out.append({"evidence_id": f"E{i}", "type": "file", "name": line[:120],
                        "command": "", "result": "pass", "details": line})
    return out


def _checks_from_md(txt: str) -> list[dict]:
    checks = []
    # таблица "| Проверка | Результат |"
    for line in txt.splitlines():
        if line.strip().startswith("|") and "|" in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0] not in ("Проверка", "ID"):
                status = "pass" if cells[-1] in ("PASS", "OK", "PASS —") else "fail"
                checks.append({"check_id": "c" + str(len(checks) + 1), "name": cells[0],
                               "status": status, "expected": "OK", "actual": cells[-1]})
    return checks


def _verification_commands(cfg) -> list[str]:
    if cfg.test_runner == "dotnet":
        return [f"dotnet build {cfg.sln} --nologo -v q", f"dotnet test {cfg.sln} --nologo -v q"]
    return []
