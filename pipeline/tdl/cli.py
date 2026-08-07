# -*- coding: utf-8 -*-
"""TDL: CLI-команды (tdl-init, tdl-dispatch, tdl-start, tdl-report, tdl-verify,
tdl-migrate, tdl-validate, tdl-index, tdl-status)."""
from __future__ import annotations

import json
import sys

from . import store, validate
from .migrate import migrate_project
from .schema import REPORT_STATUSES


def _task_or_die(cfg, task_id: str) -> dict:
    t = store.load_task(cfg, task_id)
    if not t:
        print(f"ЗАДАЧА НЕ НАЙДЕНА (JSON): {task_id}")
        sys.exit(1)
    return t


def tdl_init(cfg, args) -> int:
    store.ensure_dirs(cfg)
    ip = store.rebuild_index(cfg)
    print(f"TDL init: каталоги созданы, индекс: {ip}")
    return 0


def tdl_dispatch(cfg, args) -> int:
    import pathlib
    from ._tpl import make_task
    src = pathlib.Path(args.file)
    if not src.exists():
        print("ФАЙЛ НЕ НАЙДЕН:", src)
        return 1
    body = src.read_text(encoding="utf-8", errors="replace")
    task_id = store.next_task_id(cfg)
    wbs = args.wbs or f"1.{int(task_id.split('-')[1]):02d}"
    task = make_task(task_id=task_id, project=cfg.name, name=args.title or src.stem,
                     wbs=wbs, priority=args.priority or "средний", goal=args.goal or body,
                     acceptance=args.requirements or [body[:2000]],
                     commands=args.result or [], source=src.name)
    if args.requirements and args.goal:
        task["acceptance_criteria"] = [args.requirements]
    store.save_task(cfg, task)
    store.rebuild_index(cfg)
    history = src.parent / "_история"
    history.mkdir(parents=True, exist_ok=True)
    src.rename(history / src.name)
    from .. import checks
    checks.git(cfg.root, f"tdl: задача {task_id} оформлена из {src.name}")
    print(f"TDL-ЗАДАЧА: {task_id} -> {store.task_path(cfg, task_id)}")
    return 0


def tdl_start(cfg, args) -> int:
    t = _task_or_die(cfg, args.task)
    if t.get("is_summary"):
        print(f"Задача {args.task} — summary, выполнять нельзя")
        return 1
    t["workflow_state"] = "in_progress"
    t["status_display"] = "Открыто"
    _append_history(t, "subagent", "workflow_state_changed", workflow_state="in_progress",
                    details="Исполнитель взял задачу.")
    store.save_task(cfg, t)
    store.rebuild_index(cfg)
    print(f"TDL-START: {args.task} -> in_progress")
    return 0


def tdl_report(cfg, args) -> int:
    from ._tpl import make_report
    t = _task_or_die(cfg, args.task)
    report = store.load_report(cfg, args.task)
    if report is None:
        report = make_report(task_id=args.task, wbs=t.get("wbs_code", ""))
    if args.from_md:
        _extract_report_from_md(report, args.from_md)
    report["report_status"] = "final" if args.final else "draft"
    store.save_report(cfg, report)
    t["workflow_state"] = "reported"
    _append_history(t, "subagent", "report_created", report_ref=report["report_id"],
                    details=f"Создан отчёт {report['report_id']} (report_status={report['report_status']})")
    store.save_task(cfg, t)
    store.rebuild_index(cfg)
    print(f"TDL-REPORT: {report['report_id']} (status={report['report_status']})")
    return 0


def tdl_verify(cfg, args) -> int:
    from .. import checks as C
    from ._tpl import make_verdict
    t = _task_or_die(cfg, args.task)
    report = store.load_report(cfg, args.task)
    if report is None:
        print(f"JSON-отчёт отсутствует для {args.task} — задача не может быть закрыта")
        return 2

    # механические проверки (structured)
    b_rc, b_out = C.build_sln(cfg)
    checks_list = _build_checks(cfg, args.task, report, b_rc, b_out)
    ok = all(c["status"] == "pass" for c in checks_list if c.get("critical", True))
    result = "pass" if ok else "fail"

    verdict = make_verdict(task_id=args.task, report_ref=report["report_id"], result=result)
    verdict["checks"] = checks_list
    verdict["can_move_forward"] = result == "pass"
    if result == "fail":
        verdict["required_fixes"] = _collect_fixes(checks_list)
    store.save_verdict(cfg, verdict)

    if result == "pass":
        t["status"] = "done"
        t["workflow_state"] = "verified"
        t["status_display"] = "Выполнено"
        _append_history(t, "controller", "status_changed", from_="open", to="done",
                        details=f"Вердикт {verdict['verdict_id']}: pass")
    else:
        t["status"] = "open"
        t["workflow_state"] = "rejected"
        _append_history(t, "controller", "verdict_created", verdict_ref=verdict["verdict_id"],
                        result="fail", details="Задача отклонена")
    store.save_task(cfg, t)
    store.rebuild_index(cfg)
    C.git(cfg.root, f"tdl-verify: {args.task} -> {result} ({verdict['verdict_id']})")
    print(f"TDL-VERIFY: {args.task} -> {result}")
    return 0 if result == "pass" else 1


def tdl_migrate(cfg, args) -> int:
    res = migrate_project(cfg, dry_run=args.dry_run, allow_done_from_md=args.allow_done_from_md)
    print(f"TDL-MIGRATE: задач {len(res['tasks'])} (created={sum(1 for t in res['tasks'] if t.get('created'))}), "
          f"отчётов {len(res['reports'])}, вердиктов {len(res['verdicts'])}, dry_run={res['dry_run']}")
    for t in res["tasks"]:
        print(f"  {t.get('task_id')}: wbs={t.get('wbs')} status={t.get('status')} created={t.get('created')}")
    if not args.dry_run:
        store.rebuild_index(cfg)
    return 0


def tdl_validate(cfg, args) -> int:
    if args.task:
        errs = validate.validate_task_file(cfg, store.task_path(cfg, args.task))
    else:
        errs = validate.validate_project(cfg)
    if not errs:
        print("TDL-VALIDATE: OK (ошибок нет)")
        return 0
    print(f"TDL-VALIDATE: {len(errs)} ошибок")
    for e in errs:
        print(f"  [{e['code']}] {e['path']}: {e['message']}")
    return 1


def tdl_index(cfg, args) -> int:
    ip = store.rebuild_index(cfg)
    print(f"TDL-INDEX: {ip}")
    return 0


def tdl_status(cfg, args) -> int:
    idx = store.load_index(cfg) or {}
    tasks = idx.get("tasks", [])
    from collections import Counter
    by_status = Counter(t.get("status") for t in tasks)
    by_wf = Counter(t.get("workflow_state") for t in tasks)
    print(f"TDL-STATUS {cfg.name}: всего={len(tasks)}")
    print(f"  status: {dict(by_status)}")
    print(f"  workflow: {dict(by_wf)}")
    for t in tasks:
        print(f"  {t.get('task_id')} [{t.get('status')}/{t.get('workflow_state')}] "
              f"wbs={t.get('wbs_code')} отчёты={len(t.get('report_refs', []))} вердикты={len(t.get('verdict_refs', []))}")
    return 0


# ---------- helpers ----------

def _append_history(task, actor, action, **kw):
    from ._tpl import _now_iso
    entry = {"timestamp": _now_iso(), "actor": actor, "action": action}
    entry.update(kw)
    task.setdefault("history", []).append(entry)


def _build_checks(cfg, task_id, report, b_rc, b_out) -> list:
    from .. import checks as C
    from ..models import parse_tests_dotnet, parse_tests_vstest
    out = []
    # секции отчёта
    out.append(_ck("report_section_work_done", "Секция work_done в отчёте",
                   bool(report.get("work_done")), "present"))
    out.append(_ck("report_evidence", "Доказательства в отчёте",
                   bool(report.get("evidence")), "not_empty"))
    # сборка
    out.append({"check_id": "build_exit_0", "name": "Сборка",
                "status": "pass" if b_rc == 0 else "fail",
                "expected": "exit_code=0", "actual": f"exit_code={b_rc}",
                "critical": True, "exit_code": b_rc, "details": _tail(b_out)})
    # тесты
    if b_rc == 0:
        t_rc, t_out = C.run_tests(cfg)
        if cfg.test_runner == "dotnet":
            passed, total, failed = parse_tests_dotnet(t_out)
        else:
            passed, total, failed = parse_tests_vstest(t_out)
        ok = (cfg.baseline_passed is None or (passed is not None and passed >= cfg.baseline_passed))
        out.append({"check_id": "tests_baseline", "name": "Тесты не хуже базовых",
                    "status": "pass" if ok else "fail",
                    "expected": f"passed>={cfg.baseline_passed}",
                    "actual": f"passed={passed},total={total},failed={failed}",
                    "critical": True, "exit_code": t_rc, "note": _tail(t_out)})
    # правила слоёв
    for label, st in C.layer_rule_rows(cfg):
        ok = st.startswith("OK")
        out.append({"check_id": "layer_" + str(len(out) + 1), "name": label,
                    "status": "pass" if ok else "fail", "expected": "clean",
                    "actual": st, "critical": True})
    return out


def _ck(cid, name, ok, expected, actual=None) -> dict:
    return {"check_id": cid, "name": name, "status": "pass" if ok else "fail",
            "expected": expected, "actual": actual if actual is not None else str(ok),
            "critical": True}


def _collect_fixes(checks_list) -> list:
    return [f"{c['name']}: {c.get('actual', '')}" for c in checks_list
            if c.get("status") != "pass"]


def _tail(out: str, n: int = 200) -> str:
    return (out or "").strip().splitlines()[-1][:n] if out else ""


def _extract_report_from_md(report, md_path):
    import re
    txt = open(md_path, encoding="utf-8", errors="replace").read()

    def sec(h):
        m = re.search(rf"##\s*{h}\s*\n(.*?)(?=\n##\s|\Z)", txt, re.S)
        return m.group(1).strip() if m else ""

    report["problem"] = sec("Что было не так") or report.get("problem", "")
    report["work_done"] = [l.strip() for l in sec("Что сделано").splitlines() if l.strip()] or []
    report["verification_commands"] = [l.strip() for l in sec("Как пересобрать/проверить").splitlines() if l.strip()] or []
    report["open_questions"] = [l.strip() for l in sec("Открытые вопросы").splitlines() if l.strip()]
