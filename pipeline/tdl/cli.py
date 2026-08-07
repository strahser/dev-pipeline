# -*- coding: utf-8 -*-
"""TDL: CLI-команды (tdl-init, tdl-dispatch, tdl-start, tdl-report, tdl-verify,
tdl-migrate, tdl-validate, tdl-index, tdl-status)."""
from __future__ import annotations

import json
import re
import sys

from . import store, validate
from .migrate import migrate_project
from .schema import REPORT_STATUSES
from ..models import failed_test_names_vstest


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
                     commands=args.result or [], source=src.name,
                     module=getattr(args, "module", "") or "",
                     class_name=getattr(args, "class_name", "") or "",
                     layer=getattr(args, "layer", "") or "")
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
    dates = t.setdefault("dates", {})
    if not dates.get("start"):
        dates["start"] = store.today()
    _append_history(t, "subagent", "workflow_state_changed", workflow_state="in_progress",
                    details="Исполнитель взял задачу.")
    store.save_task(cfg, t)
    store.rebuild_index(cfg)
    print(f"TDL-START: {args.task} -> in_progress (start={dates.get('start')})")
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
        dates = t.setdefault("dates", {})
        dates["finish"] = store.today()
        dates["duration_sec"] = _duration_sec(dates.get("start"), dates["finish"])
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


def tdl_plan(cfg, args) -> int:
    """Построить иерархию миссии из спецификации: миссия(summary) -> этапы -> классы -> листья.

    Формат спецификации (JSON или YAML):
      mission: { name, goal }
      phases:
        - name: <этап>            # level 2
          module: <модуль>
          goal: ...
          packages:
            - name: <класс>       # level 3 (класс/пакет)
              class_name: <ClassName>
              layer: core|ui|infrastructure|features|tests
              goal: ...
              tasks: [ "текст задачи", ... ]   # level 4 листья
        - name: ...
          tasks: [ ... ]           # level 4 листья напрямую под этапом
    """
    import json
    import pathlib
    from ._tpl import make_task
    from .. import checks as C

    src = pathlib.Path(args.file)
    if not src.exists():
        print("ФАЙЛ НЕ НАЙДЕН:", src)
        return 1
    raw = src.read_text(encoding="utf-8", errors="replace")
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError:
        try:
            import yaml
            spec = yaml.safe_load(raw)
        except Exception as e:
            print(f"СПЕЦИФИКАЦИЯ НЕ РАСПОЗНАНА (JSON/YAML): {e}")
            return 1

    store.ensure_dirs(cfg)
    commands = _tdl_default_commands(cfg)
    mission = spec.get("mission", {})
    m_name = mission.get("name") or args.title or src.stem
    m_goal = mission.get("goal") or raw[:2000]
    ids_created = []

    # Уровень 1: миссия (summary, task_kind=group) — корень WBS = свободный уровень 1
    used_l1 = set()
    idx = store.load_index(cfg) or {"tasks": []}
    for t in idx.get("tasks", []):
        w = str(t.get("wbs_code", ""))
        seg = w.split(".")[0]
        if seg.isdigit():
            used_l1.add(int(seg))
    l1 = 1
    while l1 in used_l1:
        l1 += 1
    root_wbs = str(l1)

    mid = store.next_task_id(cfg)
    mt = make_task(task_id=mid, project=cfg.name, name=m_name, wbs=root_wbs,
                   priority="высокий", goal=m_goal, source=src.name,
                   task_kind="group", is_summary=True, description=m_goal)
    store.save_task(cfg, mt)
    ids_created.append(mid)
    print(f"  МИССИЯ {mid} [{root_wbs}]: {m_name}")

    for ph_idx, phase in enumerate(spec.get("phases", []), start=1):
        p_wbs = f"{root_wbs}.{ph_idx}"
        p_name = phase.get("name") or f"Этап {ph_idx}"
        p_module = phase.get("module", "")
        p_goal = phase.get("goal", "")
        p_est = _estimate_sec(phase.get("estimate_sec") or phase.get("estimate"))
        # Уровень 2: этап (summary, group)
        pid = store.next_task_id(cfg)
        pt = make_task(task_id=pid, project=cfg.name, name=p_name, wbs=p_wbs,
                       priority=phase.get("priority", "высокий"), goal=p_goal,
                       source=src.name, module=p_module, is_summary=True,
                       description=phase.get("description", p_goal),
                       estimate_sec=p_est)
        store.save_task(cfg, pt)
        ids_created.append(pid)
        print(f"  ЭТАП {pid} [{p_wbs}]: {p_name} (module={p_module}"
              + (f", estimate={_fmt_est(p_est)}" if p_est else "") + ")")

        # Уровень 3: классы/пакеты (summary, group)
        packages = phase.get("packages", [])
        if not packages:
            packages = [{"name": p_name, "class_name": "", "layer": "",
                         "goal": p_goal, "tasks": phase.get("tasks", [])}]
        for pk_idx, pkg in enumerate(packages, start=1):
            pk_wbs = f"{p_wbs}.{pk_idx}"
            pk_name = pkg.get("name") or pkg.get("class_name") or f"Пакет {pk_idx}"
            pk_est = _estimate_sec(pkg.get("estimate_sec") or pkg.get("estimate")) or p_est
            cid = store.next_task_id(cfg)
            ct = make_task(task_id=cid, project=cfg.name, name=pk_name, wbs=pk_wbs,
                           priority=pkg.get("priority", "средний"),
                           goal=pkg.get("goal", pkg.get("name", "")),
                           source=src.name, module=p_module,
                           class_name=pkg.get("class_name", ""),
                           layer=pkg.get("layer", ""), is_summary=True,
                           description=pkg.get("description", ""),
                           estimate_sec=pk_est)
            store.save_task(cfg, ct)
            ids_created.append(cid)
            print(f"    ПАКЕТ {cid} [{pk_wbs}]: {pk_name} (class={pkg.get('class_name','')} layer={pkg.get('layer','')}"
                  + (f", estimate={_fmt_est(pk_est)}" if pk_est else "") + ")")

            # Уровень 4: листья
            tasks = pkg.get("tasks", [])
            if not tasks:
                tasks = [f"Выполнить {pk_name}"]
            for lf_idx, leaf in enumerate(tasks, start=1):
                lf_wbs = f"{pk_wbs}.{lf_idx}"
                if isinstance(leaf, str):
                    l_name = leaf
                    l_goal = leaf
                    l_accept = [leaf]
                    l_est = pk_est
                else:
                    l_name = leaf.get("name", f"Задача {lf_idx}")
                    l_goal = leaf.get("goal", leaf.get("name", ""))
                    l_accept = leaf.get("acceptance_criteria") or [l_goal]
                    l_class = leaf.get("class_name", pkg.get("class_name", ""))
                    l_layer = leaf.get("layer", pkg.get("layer", ""))
                    l_est = _estimate_sec(leaf.get("estimate_sec") or leaf.get("estimate")) or pk_est
                lid = store.next_task_id(cfg)
                lt = make_task(task_id=lid, project=cfg.name, name=l_name, wbs=lf_wbs,
                               priority=leaf.get("priority", "средний") if isinstance(leaf, dict) else "средний",
                               goal=l_goal, acceptance=l_accept, commands=commands,
                               source=src.name, module=p_module,
                               class_name=l_class if isinstance(leaf, dict) else pkg.get("class_name", ""),
                               layer=l_layer if isinstance(leaf, dict) else pkg.get("layer", ""),
                               task_kind="execution",
                               dependencies=[pid, cid] if not isinstance(leaf, str) else [pid, cid],
                               estimate_sec=l_est)
                store.save_task(cfg, lt)
                ids_created.append(lid)
                print(f"      ЛИСТ {lid} [{lf_wbs}]: {l_name}"
                      + (f" (estimate={_fmt_est(l_est)})" if l_est else ""))

    store.rebuild_index(cfg)
    C.git(cfg.root, f"tdl: план миссии {m_name} ({len(ids_created)} задач)")
    print(f"TDL-PLAN: {len(ids_created)} задач создано (миссия+этапы+классы+листья)")
    return 0


def tdl_tree(cfg, args) -> int:
    """Показать дерево TDL (миссия -> этапы -> классы -> листья) по WBS."""
    from . import render
    idx = store.load_index(cfg) or {"tasks": []}
    tasks = idx.get("tasks", [])
    if not tasks:
        print("TDL-TREE: нет задач")
        return 0
    print(render.render_tree(tasks))
    return 0


def _tdl_default_commands(cfg) -> list:
    """Команды сборки/тестов из конфига проекта."""
    cmds = []
    if cfg.msbuild.lower() == "dotnet" and cfg.sln:
        cmds.append(f"dotnet build {cfg.sln} --nologo -v q")
        cmds.append(f"dotnet test {cfg.sln} --nologo -v q")
    elif cfg.msbuild:
        cmds.append(f"\"{cfg.msbuild}\" {cfg.root / cfg.sln} /t:Restore,Build "
                    f"/p:Configuration={cfg.configuration} /p:Platform=\"{cfg.platform}\"")
        if cfg.vstest:
            cmds.append(f"\"{cfg.vstest}\" {cfg.root / cfg.test_dll}")
    return cmds


# ---------- helpers ----------

def _parse_dt(v) -> datetime.datetime | None:
    """Распарсить дату (YYYY-MM-DD) или ISO-момент -> локальный datetime."""
    import datetime as _dt
    if not v:
        return None
    try:
        s = str(v).strip().replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _duration_sec(start, finish) -> int | None:
    """Фактическая длительность (сек) между start и finish (даты или ISO)."""
    s = _parse_dt(start)
    f = _parse_dt(finish)
    if s is None or f is None:
        return None
    return max(0, int((f - s).total_seconds()))


def _estimate_sec(v) -> int | None:
    """Плановая оценка в секундах: число (сек) | часы (float) | "2ч 30м" | "3.5h"."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 0 < v <= 24:          # «2», «0.5», «24» — часы
            return int(v * 3600)
        return int(v)            # иначе — секунды
    s = str(v).strip().lower()
    total = 0
    for h, hh, mi, dy in re.findall(
            r"(\d+(?:[.,]\d+)?)\s*(?:ч|час)|(\d+(?:[.,]\d+)?)\s*(?:h|hr)|(\d+)\s*(?:м|мин)|(\d+)\s*(?:д|дн)", s):
        if dy:
            total += int(dy) * 86400
        elif mi:
            total += int(mi) * 60
        else:
            total += int(float((h or hh).replace(",", ".")) * 3600)
    if total:
        return total
    try:
        return int(float(s.replace(",", ".")) * 3600)  # «2» -> 2 часа
    except ValueError:
        return None


def _fmt_est(sec: int | None) -> str:
    if not sec:
        return ""
    if sec % 3600 == 0:
        return f"{sec // 3600}ч"
    if sec >= 3600:
        return f"{sec // 3600}ч {sec % 3600 // 60}м"
    return f"{sec // 60}м"


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
            failed_names = []
        else:
            passed, total, failed = parse_tests_vstest(t_out)
            failed_names = failed_test_names_vstest(t_out)
        known = [k for k in cfg.known_failures if any(k.lower() in n.lower() for n in failed_names)]
        unexpected = [n for n in failed_names if not any(k.lower() in n.lower() for k in cfg.known_failures)]
        ok = (cfg.baseline_passed is None or (passed is not None and passed >= cfg.baseline_passed))
        if failed and unexpected:
            ok = False
        if t_rc not in (None, 0) and not (failed and not unexpected):
            ok = False
        note = _tail(t_out)
        if known:
            note = f"известные падения ({len(known)}): " + ", ".join(known) + " | " + note
        out.append({"check_id": "tests_baseline", "name": "Тесты не хуже базовых",
                    "status": "pass" if ok else "fail",
                    "expected": f"passed>={cfg.baseline_passed}, failed=0 (или только known_failures)",
                    "actual": f"passed={passed},total={total},failed={failed}",
                    "critical": True, "exit_code": t_rc, "note": note})
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
        # заголовки вида "## Что сделано", "## 2. Что сделано", "## 2. Что сделано (детали)"
        m = re.search(rf"##\s*(?:\d+\.\s*)?{re.escape(h)}[^\n]*\n(.*?)(?=\n##\s|\Z)", txt, re.S)
        return m.group(1).strip() if m else ""

    report["problem"] = sec("Что было не так") or report.get("problem", "")
    report["work_done"] = [l.strip() for l in sec("Что сделано").splitlines() if l.strip()] or []
    report["verification_commands"] = [l.strip() for l in sec("Как пересобрать/проверить").splitlines() if l.strip()] or []
    report["open_questions"] = [l.strip() for l in sec("Открытые вопросы").splitlines() if l.strip()] or []
    evidence = [l.strip() for l in sec("Доказательства").splitlines() if l.strip()]
    if evidence and not report.get("evidence"):
        report["evidence"] = [{"evidence_id": f"E{i + 1}", "type": "md_section", "name": e[:120],
                               "details": e} for i, e in enumerate(evidence)]
