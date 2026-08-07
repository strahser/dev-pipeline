# -*- coding: utf-8 -*-
"""TDL: dataclass-модели сущностей (task/report/verdict/index)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


def _to_dict(obj):
    return asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj


@dataclass
class TdlLink:
    type: str
    href: str = ""
    ref: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in ("", None)}

    @staticmethod
    def from_dict(d: dict) -> "TdlLink":
        return TdlLink(type=d.get("type", "reference"), href=d.get("href", ""),
                       ref=d.get("ref", ""), url=d.get("url", ""))


@dataclass
class TdlEvidence:
    evidence_id: str
    type: str
    name: str = ""
    command: str = ""
    exit_code: int | None = None
    result: str = "pass"
    details: str = ""
    metrics: dict = field(default_factory=dict)
    baseline: dict = field(default_factory=dict)
    acceptance_rule: str = ""
    links: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TdlEvidence":
        return TdlEvidence(
            evidence_id=d.get("evidence_id", ""), type=d.get("type", ""),
            name=d.get("name", ""), command=d.get("command", ""),
            exit_code=d.get("exit_code"), result=d.get("result", "pass"),
            details=d.get("details", ""), metrics=d.get("metrics", {}),
            baseline=d.get("baseline", {}), acceptance_rule=d.get("acceptance_rule", ""),
            links=d.get("links", []))


@dataclass
class TdlCheck:
    check_id: str
    name: str
    status: str
    expected: str
    actual: str
    critical: bool = False
    exit_code: int | None = None
    note: str = ""
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TdlCheck":
        return TdlCheck(
            check_id=d.get("check_id", ""), name=d.get("name", ""),
            status=d.get("status", "fail"), expected=d.get("expected", ""),
            actual=d.get("actual", ""), critical=d.get("critical", False),
            exit_code=d.get("exit_code"), note=d.get("note", ""),
            details=d.get("details", ""))


@dataclass
class TdlHistoryEntry:
    timestamp: str
    actor: str
    action: str
    details: str = ""
    workflow_state: str = ""
    from_: str = ""
    to: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.from_:
            d["from"] = self.from_
        del d["from_"]
        return d

    @staticmethod
    def from_dict(d: dict) -> "TdlHistoryEntry":
        return TdlHistoryEntry(
            timestamp=d.get("timestamp", ""), actor=d.get("actor", ""),
            action=d.get("action", ""), details=d.get("details", ""),
            workflow_state=d.get("workflow_state", ""),
            from_=d.get("from", ""), to=d.get("to", ""))


@dataclass
class TdlTask:
    task_id: str
    wbs_code: str
    project: str
    name: str
    status: str
    task_kind: str
    goal: str
    acceptance_criteria: list
    dates: dict
    links: list
    schema_version: str = "1.0.0"
    entity_type: str = "tdl.task"
    parent_wbs: str = ""
    is_summary: bool = False
    status_display: str = ""
    workflow_state: str = "issued"
    legacy_status: str = ""
    priority: str = "средний"
    module: str = ""
    class_name: str = ""
    layer: str = ""
    description: str = ""
    context: str = ""
    inputs: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    assignees: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    artifacts: list = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    progress_log: list = field(default_factory=list)
    blocker: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TdlTask":
        return TdlTask(**{k: v for k, v in d.items() if k in TdlTask.__dataclass_fields__})


@dataclass
class TdlReport:
    task_ref: str
    report_id: str
    date: str
    executor: dict
    problem: str
    work_done: list
    evidence: list
    verification_commands: list
    schema_version: str = "1.0.0"
    entity_type: str = "tdl.report"
    wbs_ref: str = ""
    report_status: str = "draft"
    files_changed: list = field(default_factory=list)
    metrics: list = field(default_factory=list)
    open_questions: list = field(default_factory=list)
    links: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TdlReport":
        return TdlReport(**{k: v for k, v in d.items() if k in TdlReport.__dataclass_fields__})


@dataclass
class TdlVerdict:
    task_ref: str
    verdict_id: str
    report_ref: str
    date: str
    result: str
    checks: list
    schema_version: str = "1.0.0"
    entity_type: str = "tdl.verdict"
    confidence: str = ""
    can_move_forward: bool = False
    evidence_review: list = field(default_factory=list)
    required_fixes: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    links: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TdlVerdict":
        return TdlVerdict(**{k: v for k, v in d.items() if k in TdlVerdict.__dataclass_fields__})


@dataclass
class TdlIndexEntry:
    task_id: str
    path: str
    wbs_code: str
    status: str
    workflow_state: str
    report_refs: list = field(default_factory=list)
    verdict_refs: list = field(default_factory=list)
    name: str = ""
    priority: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TdlIndex:
    tasks: list = field(default_factory=list)
    schema_version: str = "1.0.0"
    entity_type: str = "tdl.index"
    generated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
