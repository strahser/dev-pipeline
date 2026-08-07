# -*- coding: utf-8 -*-
"""TDL: валидаторы задач/отчётов/вердиктов и правила закрытия."""
from __future__ import annotations

from .schema import (MAX_WBS_DEPTH, REPORT_STATUSES, REQUIRED_REPORT_FIELDS,
                     REQUIRED_TASK_FIELDS, REQUIRED_VERDICT_FIELDS,
                     TASK_KINDS, TASK_STATUS, VERDICT_RESULTS)


def _err(path, code, message) -> dict:
    return {"path": path, "code": code, "message": message}


def _wbs_depth(wbs: str) -> int:
    return len([x for x in str(wbs).split(".") if x])


def validate_task(task: dict, ctx: dict | None = None) -> list[dict]:
    ctx = ctx or {}
    path = ctx.get("path", "Tasks/JSON/Active/<task>.task.json")
    errors = []
    for f in REQUIRED_TASK_FIELDS:
        if f not in task or task[f] is None or task[f] == "" or task[f] == {}:
            errors.append(_err(path, "missing_field", f"Отсутствует обязательное поле {f}"))
        elif isinstance(task[f], list) and not task[f] and f in ("acceptance_criteria",):
            # для execution проверяется отдельно ниже
            pass
    if task.get("entity_type") != "tdl.task":
        errors.append(_err(path, "bad_entity", f"entity_type должен быть tdl.task, а не {task.get('entity_type')}"))
    if task.get("status") not in TASK_STATUS:
        errors.append(_err(path, "bad_status", f"status должен быть open/done, а не {task.get('status')}"))
    wbs = task.get("wbs_code", "")
    if _wbs_depth(wbs) > MAX_WBS_DEPTH:
        errors.append(_err(path, "wbs_depth", f"WBS глубже {MAX_WBS_DEPTH} уровней: {wbs}"))
    if task.get("task_kind") not in TASK_KINDS:
        errors.append(_err(path, "bad_kind", f"task_kind должен быть в {TASK_KINDS}"))
    if task.get("task_kind") == "execution":
        if not task.get("goal"):
            errors.append(_err(path, "missing_goal", "Для execution обязательна goal"))
        if not task.get("acceptance_criteria"):
            errors.append(_err(path, "missing_criteria", "Для execution обязательны acceptance_criteria"))
        verification = task.get("verification") or {}
        if not verification.get("commands"):
            errors.append(_err(path, "missing_verification", "Для execution обязателен verification.commands"))
    if task.get("task_kind") == "execution" and task.get("is_summary"):
        errors.append(_err(path, "summary_execution", "execution-задача не может быть is_summary"))
    # level соответствует глубине wbs (если задан)
    if task.get("level") is not None:
        depth = _wbs_depth(wbs)
        if task["level"] != depth:
            errors.append(_err(path, "level_mismatch", f"level={task['level']} != глубина wbs={depth}"))
    return errors


def validate_report(report: dict, ctx: dict | None = None) -> list[dict]:
    ctx = ctx or {}
    path = ctx.get("path", "Tasks/JSON/Reports/<task>.report.json")
    errors = []
    for f in REQUIRED_REPORT_FIELDS:
        if f not in report or report[f] in (None, "", [], {}):
            errors.append(_err(path, "missing_field", f"Отсутствует обязательное поле {f}"))
    if report.get("entity_type") != "tdl.report":
        errors.append(_err(path, "bad_entity", "entity_type должен быть tdl.report"))
    if not report.get("work_done"):
        errors.append(_err(path, "empty_work_done", "work_done не может быть пустым"))
    evidence = report.get("evidence") or []
    if not evidence:
        errors.append(_err(path, "empty_evidence", "evidence не может быть пустым"))
    for ev in evidence:
        if not ev.get("evidence_id"):
            errors.append(_err(path, "evidence_no_id", "Каждое доказательство требует evidence_id"))
        if not ev.get("type"):
            errors.append(_err(path, "evidence_no_type", "Каждое доказательство требует type"))
        if ev.get("exit_code") not in (None, 0) and not (ev.get("acceptance_rule") and ev.get("baseline")):
            errors.append(_err(path, "evidence_bad_exit",
                               f"Доказательство {ev.get('evidence_id')}: exit_code!=0 требует acceptance_rule и baseline"))
    if "open_questions" not in report:
        errors.append(_err(path, "missing_open_questions", "Поле open_questions обязательно (может быть [])"))
    if report.get("report_status") not in REPORT_STATUSES:
        errors.append(_err(path, "bad_report_status", f"report_status должен быть в {REPORT_STATUSES}"))
    return errors


def validate_verdict(verdict: dict, ctx: dict | None = None) -> list[dict]:
    ctx = ctx or {}
    path = ctx.get("path", "Tasks/JSON/Verdicts/<task>.verdict.json")
    errors = []
    for f in REQUIRED_VERDICT_FIELDS:
        if f not in verdict or verdict[f] in (None, "", [], {}):
            errors.append(_err(path, "missing_field", f"Отсутствует обязательное поле {f}"))
    if verdict.get("entity_type") != "tdl.verdict":
        errors.append(_err(path, "bad_entity", "entity_type должен быть tdl.verdict"))
    if verdict.get("result") not in VERDICT_RESULTS:
        errors.append(_err(path, "bad_result", f"result должен быть в {VERDICT_RESULTS}"))
    checks = verdict.get("checks") or []
    if not checks:
        errors.append(_err(path, "empty_checks", "checks не может быть пустым"))
    for c in checks:
        for f in ("check_id", "name", "status", "expected", "actual"):
            if not c.get(f):
                errors.append(_err(path, "check_missing", f"Проверка {c.get('check_id','?')}: нет поля {f}"))
    if verdict.get("result") == "pass":
        crit_fail = [c for c in checks if c.get("critical") and c.get("status") != "pass"]
        if crit_fail:
            errors.append(_err(path, "pass_with_critical_fail",
                               f"result=pass, но критичные проверки не pass: {[c['check_id'] for c in crit_fail]}"))
    if verdict.get("result") == "fail" and not verdict.get("required_fixes"):
        errors.append(_err(path, "fail_no_fixes", "result=fail требует непустой required_fixes"))
    return errors


def can_close_task(task: dict, report: dict | None, verdict: dict | None) -> tuple[bool, str]:
    """Задача может получить status=done только при полном наборе доказательств."""
    if not report:
        return False, "Нет JSON-отчёта"
    if not report.get("evidence"):
        return False, "Отчёт без доказательств (evidence пуст)"
    if not verdict:
        return False, "Нет JSON-вердикта"
    if verdict.get("result") != "pass":
        return False, f"Вердикт не pass: {verdict.get('result')}"
    if not verdict.get("can_move_forward", False):
        return False, "verdict.can_move_forward != true"
    return True, "OK"


def validate_task_file(cfg, path) -> list[dict]:
    """Валидировать JSON-файл по его типу (task/report/verdict)."""
    import json
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [_err(str(path), "json_parse", f"Ошибка JSON: {e}")]
    rel = str(path.relative_to(cfg.root)).replace("\\", "/")
    ent = data.get("entity_type")
    if ent == "tdl.task":
        return validate_task(data, {"path": rel})
    if ent == "tdl.report":
        return validate_report(data, {"path": rel})
    if ent == "tdl.verdict":
        return validate_verdict(data, {"path": rel})
    return [_err(rel, "unknown_entity", f"Неизвестный entity_type: {ent}")]


def validate_project(cfg) -> list[dict]:
    from . import store
    errors = []
    for d in (store.active_dir(cfg), store.reports_dir(cfg), store.verdicts_dir(cfg)):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            errors += validate_task_file(cfg, f)
    # Проверка: статус done без доказательств
    ad = store.active_dir(cfg)
    if ad.is_dir():
        for f in sorted(ad.glob("*.task.json")):
            try:
                import json
                t = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if t.get("status") == "done":
                ok, why = can_close_task(t, store.load_report(cfg, t["task_id"]),
                                         store.load_verdict(cfg, t["task_id"]))
                if not ok:
                    rel = f"Tasks/JSON/Active/{f.name}".replace("\\", "/")
                    errors.append(_err(rel, "done_without_evidence",
                                       f"status=done, но невозможно закрыть: {why}"))
    return errors
