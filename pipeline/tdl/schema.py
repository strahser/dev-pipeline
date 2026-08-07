# -*- coding: utf-8 -*-
"""TDL: константы, схемы, допустимые значения (по docs/План реализации.txt)."""
from __future__ import annotations

TDL_SCHEMA_VERSION = "1.0.0"

# TDL-статус задачи (пользовательский): только open/done
TASK_STATUS = {"open", "done"}

STATUS_DISPLAY = {"open": "Открыто", "done": "Выполнено"}

# Технологическое состояние (workflow)
WORKFLOW_STATES = {
    "issued", "in_progress", "blocked", "reported", "control",
    "verified", "rejected", "archived", "pending_report", "pending_verdict",
}

TASK_KINDS = {"group", "request", "execution"}

VERDICT_RESULTS = {"pass", "fail", "pending"}

REPORT_STATUSES = {"draft", "final", "partial", "migrated"}

LAYERS = {"core", "ui", "infrastructure", "features", "tests"}

MAX_WBS_DEPTH = 4

# Легаси-статусы (Markdown) -> workflow
LEGACY_TO_WORKFLOW = {
    "open": "issued",
    "in_progress": "in_progress",
    "done_report": "reported",
    "verified": "pending_verdict",
    "rejected": "rejected",
    "closed": "blocked",
}

WORKFLOW_TO_STATUS = {
    "issued": "open", "in_progress": "open", "blocked": "open",
    "reported": "open", "control": "open", "rejected": "open",
    "pending_report": "open", "pending_verdict": "open",
    "verified": "done", "archived": "done",
}

REQUIRED_TASK_FIELDS = [
    "schema_version", "entity_type", "task_id", "wbs_code", "project",
    "name", "status", "task_kind", "goal", "acceptance_criteria", "dates", "links",
]

REQUIRED_REPORT_FIELDS = [
    "schema_version", "entity_type", "report_id", "task_ref", "date",
    "executor", "problem", "work_done", "evidence", "verification_commands",
]

REQUIRED_VERDICT_FIELDS = [
    "schema_version", "entity_type", "verdict_id", "task_ref", "report_ref",
    "date", "result", "checks",
]

LINK_TYPES = {
    "task_source", "assignment", "reference", "source_file", "report",
    "verdict", "commit", "log", "test_report", "dxf", "screenshot",
    "artifact", "task", "report_markdown",
}

EVIDENCE_TYPES = {
    "git_diff", "build_log", "test_report", "file", "log", "screenshot",
    "dxf", "commit", "json_result", "ci_run",
}

# Человекочитаемые подписи событий (для server/db.py)
EVENT_HUMAN = {
    "tdl_task_created": "Создана TDL-задача",
    "tdl_report_created": "Создан TDL-отчёт",
    "tdl_verdict_created": "Создан TDL-вердикт",
    "tdl_task_done": "TDL-задача закрыта",
    "tdl_validation_failed": "TDL-валидация не прошла",
}
