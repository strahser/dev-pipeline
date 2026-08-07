# -*- coding: utf-8 -*-
"""TDL: файловое хранилище (JSON как источник истины)."""
from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
from pathlib import Path

from .models import TdlIndex, TdlIndexEntry
from .schema import TDL_SCHEMA_VERSION


def _write_json_atomic(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def tdl_root(cfg) -> Path:
    return cfg.resolve(getattr(cfg, "tdl_root", "Tasks\\JSON"))


def active_dir(cfg) -> Path:
    return cfg.resolve(getattr(cfg, "tdl_active", "Tasks\\JSON\\Active"))


def reports_dir(cfg) -> Path:
    return cfg.resolve(getattr(cfg, "tdl_reports", "Tasks\\JSON\\Reports"))


def verdicts_dir(cfg) -> Path:
    return cfg.resolve(getattr(cfg, "tdl_verdicts", "Tasks\\JSON\\Verdicts"))


def index_path(cfg) -> Path:
    return cfg.resolve(getattr(cfg, "tdl_index", "Tasks\\JSON\\Index\\tdl.index.json"))


def ensure_dirs(cfg) -> None:
    for d in (tdl_root(cfg), active_dir(cfg), reports_dir(cfg),
              verdicts_dir(cfg), index_path(cfg).parent):
        d.mkdir(parents=True, exist_ok=True)


def task_path(cfg, task_id: str) -> Path | None:
    p = active_dir(cfg) / f"{task_id}.task.json"
    return p if p.exists() else None


def load_task(cfg, task_id: str) -> dict | None:
    p = task_path(cfg, task_id)
    if not p:
        return None
    return _read_json(p)


def save_task(cfg, task: dict) -> Path:
    ensure_dirs(cfg)
    p = active_dir(cfg) / f"{task['task_id']}.task.json"
    _write_json_atomic(p, task)
    return p


def report_path(cfg, task_id: str, date: str) -> Path:
    return reports_dir(cfg) / f"{task_id}_{date}.report.json"


def latest_report_path(cfg, task_id: str) -> Path | None:
    d = reports_dir(cfg)
    if not d.is_dir():
        return None
    files = sorted(d.glob(f"{task_id}_*.report.json"))
    return files[-1] if files else None


def load_report(cfg, task_id: str) -> dict | None:
    p = latest_report_path(cfg, task_id)
    if not p:
        return None
    return _read_json(p)


def save_report(cfg, report: dict) -> Path:
    ensure_dirs(cfg)
    p = report_path(cfg, report["task_ref"], report["date"])
    _write_json_atomic(p, report)
    return p


def verdict_path(cfg, task_id: str, date: str) -> Path:
    return verdicts_dir(cfg) / f"{task_id}_{date}.verdict.json"


def latest_verdict_path(cfg, task_id: str) -> Path | None:
    d = verdicts_dir(cfg)
    if not d.is_dir():
        return None
    files = sorted(d.glob(f"{task_id}_*.verdict.json"))
    return files[-1] if files else None


def load_verdict(cfg, task_id: str) -> dict | None:
    p = latest_verdict_path(cfg, task_id)
    if not p:
        return None
    return _read_json(p)


def save_verdict(cfg, verdict: dict) -> Path:
    ensure_dirs(cfg)
    p = verdict_path(cfg, verdict["task_ref"], verdict["date"])
    _write_json_atomic(p, verdict)
    return p


def today() -> str:
    return datetime.date.today().isoformat()


def next_task_id(cfg) -> str:
    """Следующий A-NN с учётом legacy Markdown + JSON Active/Reports/Verdicts."""
    ids: list[int] = []

    def scan(glob_paths):
        for gp in glob_paths:
            for f in Path(cfg.root).glob(gp):
                m = re.search(r"(?:^|[\\/_])(A-(\d+))", str(f.name))
                if m:
                    ids.append(int(m.group(2)))

    scan([f"Tasks/**/A-*.md"])
    for d in (active_dir(cfg), reports_dir(cfg), verdicts_dir(cfg)):
        if d.is_dir():
            for f in d.iterdir():
                m = re.search(r"A-(\d+)", f.name)
                if m:
                    ids.append(int(m.group(1)))
    nxt = (max(ids) + 1) if ids else 1
    return f"A-{nxt:02d}"


def rebuild_index(cfg) -> Path:
    """Пересоздать tdl.index.json из Active-задач + отчётов/вердиктов."""
    ensure_dirs(cfg)
    entries = []
    ad = active_dir(cfg)
    if ad.is_dir():
        for f in sorted(ad.glob("*.task.json")):
            try:
                t = _read_json(f)
            except Exception:
                continue
            tid = t.get("task_id", f.stem)
            report_refs = [p.name for p in sorted(reports_dir(cfg).glob(f"{tid}_*.report.json"))] \
                if reports_dir(cfg).is_dir() else []
            verdict_refs = [p.name for p in sorted(verdicts_dir(cfg).glob(f"{tid}_*.verdict.json"))] \
                if verdicts_dir(cfg).is_dir() else []
            entries.append(TdlIndexEntry(
                task_id=tid,
                path=f"Tasks/JSON/Active/{f.name}".replace("\\", "/"),
                wbs_code=t.get("wbs_code", ""),
                status=t.get("status", "open"),
                workflow_state=t.get("workflow_state", "issued"),
                report_refs=report_refs,
                verdict_refs=verdict_refs,
                name=t.get("name", ""),
                priority=t.get("priority", ""),
            ).to_dict())
    index = TdlIndex(
        tasks=entries,
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    ip = index_path(cfg)
    _write_json_atomic(ip, index.to_dict())
    return ip


def load_index(cfg) -> dict | None:
    ip = index_path(cfg)
    if not ip.exists():
        return None
    return _read_json(ip)
