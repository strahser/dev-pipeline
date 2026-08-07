# -*- coding: utf-8 -*-
"""TDL: фабрики JSON-шаблонов (task/report/verdict)."""
from __future__ import annotations

import datetime


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def make_task(task_id: str, project: str, name: str, wbs: str,
              priority: str = "средний", goal: str = "",
              acceptance: list | None = None, commands: list | None = None,
              source: str = "") -> dict:
    return {
        "schema_version": "1.0.0",
        "entity_type": "tdl.task",
        "task_id": task_id,
        "wbs_code": wbs,
        "parent_wbs": wbs.rsplit(".", 1)[0] if "." in wbs else "",
        "is_summary": False,
        "project": {"code": project, "name": project},
        "name": name,
        "description": goal,
        "status": "open",
        "status_display": "Открыто",
        "workflow_state": "issued",
        "priority": priority,
        "task_kind": "execution",
        "goal": goal,
        "acceptance_criteria": acceptance or ([goal] if goal else []),
        "constraints": ["Не менять архитектуру сверх задачи.",
                        "Исполнитель не закрывает задачу сам; закрытие только контролёром."],
        "dates": {"issued": datetime.date.today().isoformat(),
                  "start": None, "finish": None},
        "assignees": {"issued_by": "агент-менеджер", "executor": "subagent",
                      "controller": "controller"},
        "source": {"type": "mission", "name": source},
        "links": [],
        "artifacts": [],
        "verification": {"required": True, "method": "build_and_tests_plus_controller_review",
                         "commands": commands or [], "baseline": {}, "result": None,
                         "verified_by": None, "verdict_ref": None},
        "history": [{"timestamp": _now_iso(), "actor": "агент-менеджер",
                     "action": "task_issued", "details": f"Задача выдана."}],
        "progress_log": [],
        "blocker": "",
    }


def make_report(task_id: str, wbs: str = "", date: str = "") -> dict:
    date = date or datetime.date.today().isoformat()
    return {
        "schema_version": "1.0.0",
        "entity_type": "tdl.report",
        "report_id": f"{task_id}_{date}",
        "task_ref": task_id,
        "wbs_ref": wbs,
        "date": date,
        "report_status": "draft",
        "executor": {"name": "subagent", "agent": "", "role": "Агент-2"},
        "problem": "",
        "work_done": [],
        "files_changed": [],
        "metrics": [],
        "evidence": [],
        "open_questions": [],
        "verification_commands": [],
        "links": [{"type": "task", "href": f"file://Tasks/JSON/Active/{task_id}.task.json"}],
    }


def make_verdict(task_id: str, report_ref: str, result: str = "pending", date: str = "") -> dict:
    date = date or datetime.date.today().isoformat()
    return {
        "schema_version": "1.0.0",
        "entity_type": "tdl.verdict",
        "verdict_id": f"{task_id}_{date}",
        "task_ref": task_id,
        "report_ref": report_ref,
        "date": date,
        "result": result,
        "confidence": "medium",
        "can_move_forward": result == "pass",
        "checks": [],
        "evidence_review": [],
        "required_fixes": [],
        "notes": [],
        "links": [{"type": "task", "href": f"file://Tasks/JSON/Active/{task_id}.task.json"},
                  {"type": "report", "href": f"file://Tasks/JSON/Reports/{report_ref}.report.json"}],
    }
