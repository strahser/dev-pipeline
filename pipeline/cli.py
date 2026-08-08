# -*- coding: utf-8 -*-
"""CLI конвейера: python -m pipeline.cli <project> <команда> [аргументы].

Команды:
  list                                   — список проектов
  env                                    — проверка окружения (пути сборки/тестов)
  status                                 — сводка по конвейеру -> Статус_конвейера.md
  dispatch <файл> [--title] [--priority] [--requirements] [--result] [--remark]
  verify <A-NN>                          — механическая проверка отчёта + сборка + тесты
  execute <A-NN> [--engine qwen|manual|parallel]
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys
from pathlib import Path

from . import checks, templates
from .config import ConfigError, check_env, load_config, list_projects
from .models import Task, parse_tests_dotnet, parse_tests_vstest


def slug(title: str) -> str:
    s = re.sub(r"[^\wа-яА-ЯёЁ\- ]", "", title).strip().replace(" ", "_")
    return s[:60] or "задача"


def next_task_id(cfg) -> str:
    ids = []
    for d in (cfg.abs_tasks_dir("active"), cfg.abs_tasks_dir("archive"),
              cfg.abs_tasks_dir("reports")):
        if d.is_dir():
            ids += re.findall(r"A-(\d+)", " ".join(os.listdir(d)))
    return "A-" + str((max([int(x) for x in ids] + [0]) + 1)).zfill(2)


def find_task(cfg, task_id: str):
    for d in (cfg.abs_tasks_dir("active"), cfg.abs_tasks_dir("reports"),
              cfg.abs_tasks_dir("archive")):
        for f in glob.glob(str(d / (task_id + "_*"))):
            return Path(f)
    return None


def read_task(cfg, task_id: str) -> Task:
    p = find_task(cfg, task_id)
    if not p:
        raise ConfigError(f"ЗАДАЧА НЕ НАЙДЕНА: {task_id}")
    return Task.from_file(p)


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------

def cmd_list(_args):
    for name in list_projects():
        print(f"  {name}")
    return 0


def cmd_env(_args):
    check_env()
    return 0


def cmd_dispatch(cfg, args):
    src = Path(os.path.abspath(args.file))
    if not src.exists():
        print("ФАЙЛ НЕ НАЙДЕН:", src)
        return 1
    inbox_history = cfg.resolve(cfg.inbox) / "_история"
    inbox_history.mkdir(parents=True, exist_ok=True)
    active = cfg.abs_tasks_dir("active")
    active.mkdir(parents=True, exist_ok=True)
    body = src.read_text(encoding="utf-8")
    title = args.title or os.path.splitext(src.name)[0][:80]
    task_id = args.id or next_task_id(cfg)
    task_file = f"{task_id}_{slug(title)}.md"
    dst = active / task_file
    content = templates.task_content(
        task_id=task_id, title=title, priority=args.priority or "средний",
        source=src.name, remark=args.remark, body=body,
        requirements=args.requirements, result=args.result)
    dst.write_text(content, encoding="utf-8")
    shutil.move(str(src), str(inbox_history / src.name))
    checks.git(cfg.root, f"agent/{task_id}: задача оформлена из {src.name}")
    print(f"ЗАДАЧА ОФОРМЛЕНА: {dst}")
    return 0


def cmd_execute(cfg, args):
    task = read_task(cfg, args.task)
    engine = args.engine or task.meta.get("исполнитель", "parallel")
    report_path = cfg.abs_tasks_dir("reports") / f"{args.task}_Отчёт_{templates.now()}.md"
    if engine == "qwen":
        return _run_cloud_engine(cfg, args.task, task, report_path)
    if engine in ("manual", "parallel"):
        print(f"ОЖИДАНИЕ ИСПОЛНИТЕЛЯ: выполни {task.file} и положи отчёт в {report_path}")
        print("  (parallel) сторож: powershell -ExecutionPolicy Bypass -File "
              f'"{cfg.root}\\Tasks\\Конвейер\\executor_loop.ps1"')
        return 0
    print("НЕИЗВЕСТНЫЙ ДВИЖОК:", engine, "(доступны: qwen, manual, parallel)")
    return 1


def _run_cloud_engine(cfg, task_id, task, report_path):
    # Облачный движок — опция (LocalAssitent). Активируется конфигом проекта.
    qwen_dir = getattr(cfg, "qwen_dir", r"E:\ПлагиныРевит\LocalAssitent")
    send = Path(qwen_dir) / "tools" / "send_to_cloud.py"
    if not send.exists():
        print(f"ДВИЖОК НЕ НАЙДЕН ({send}); используйте --engine manual")
        return 1
    instruction = (
        "Ты — исполнитель задачи из файла. Прочитай файл задачи полностью, выполни "
        "требования. Доказательства — реальные выводы сборки/тестов/grep. Отчёт — по "
        "шаблону протокола. Не выдумывай факты.")
    print("ЗАПУСК ИСПОЛНИТЕЛЯ (qwen через LocalAssitent, Edge порт 9222)")
    cmd = ["python", "-X", "utf8", "-m", "tools.send_to_cloud", str(task.file),
           "--provider", "qwen", "--prompt", instruction, "--output", str(report_path)]
    try:
        rc, out = checks.sh(cmd, timeout=1800, cwd=qwen_dir)
    except Exception as e:
        print("ДВИЖОК УПАЛ:", e)
        return 2
    print(out[-1500:])
    if report_path.exists() and report_path.stat().st_size > 200:
        checks.git(cfg.root, f"agent/{task_id}: отчёт исполнителя (qwen)")
        print("ОТЧЁТ ИСПОЛНИТЕЛЯ:", report_path)
        return 0
    print("ОТЧЁТ НЕ ПОЛУЧЕН — запустите вручную или повторите")
    return 2


def cmd_verify(cfg, args):
    task = read_task(cfg, args.task)
    reports_dir = cfg.abs_tasks_dir("reports")
    reports = sorted(glob.glob(str(reports_dir / (args.task + "_Отчёт_*"))))
    if not reports:
        print("ОТЧЁТ ИСПОЛНИТЕЛЯ НЕ НАЙДЕН — сначала execute")
        return 1
    report = Path(reports[-1])
    rtxt = report.read_text(encoding="utf-8")

    result = []  # (label, status_str)
    for sec in templates.REPORT_SECTIONS:
        result.append((f"секция «{sec}» в отчёте", "OK" if sec in rtxt else "НЕТ"))
    result.append(("отчёт не пустой", f"{len(rtxt)} символов"))
    if re.search(r"\{[a-z_]+\s*\[", rtxt):
        result.append(("отчёт без {meta}-шаблонов", "НАЙДЕНЫ"))
    else:
        result.append(("отчёт без {meta}-шаблонов", "OK"))

    b_rc, b_out = checks.build_sln(cfg)
    b_status = "PASS" if b_rc == 0 else "FAIL"
    tail = " ".join(l for l in b_out.splitlines() if "error" in l.lower())[-300:] \
        or (b_out.splitlines()[-1] if b_out.splitlines() else "")
    build_label = "сборка" if cfg.msbuild.lower() == "dotnet" else \
        f"сборка sln ({cfg.configuration}/{cfg.platform})"
    result.append((f"{build_label}: EXIT {b_rc}",
                   b_status + (" " + tail if b_status == "FAIL" else "")))

    if b_rc == 0:
        t_rc, t_out = checks.run_tests(cfg)
        if cfg.test_runner == "dotnet":
            passed, total, failed = parse_tests_dotnet(t_out)
        else:
            passed, total, failed = parse_tests_vstest(t_out)
        tail = " ".join(l.strip() for l in t_out.splitlines() if l.strip())[-250:]
        if failed is None and passed is None:
            t_status = "ОШИБКА ПАРСИНГА"
            t_summary = tail
        else:
            # Сравнение с базовым состоянием: не хуже baseline = PASS (задача не про тесты)
            base_p = cfg.baseline_passed
            base_t = cfg.baseline_total
            if base_p is not None and base_t is not None and passed is not None and total is not None:
                ok = (passed >= base_p and total == base_t)
                t_status = "PASS" if ok else "FAIL"
                t_summary = (f"rc={t_rc}, passed={passed}, total={total}, failed={failed}"
                             f" (база {base_p}/{base_t})")
                if not ok:
                    t_summary += " | " + tail
            else:
                ok = (t_rc == 0 and failed == 0) or (passed is not None and total is not None and passed == total)
                t_status = "PASS" if ok else "FAIL"
                t_summary = f"rc={t_rc}, passed={passed}, total={total}, failed={failed}"
                if not ok:
                    t_summary += " | " + tail
        result.append((f"тесты: {t_summary}", t_status))
    else:
        result.append(("тесты (сборка не прошла)", "SKIP"))

    for label, st in checks.verify_checks(cfg, args.task, b_out):
        result.append((label, st))

    # Правила слоёв (применяются на каждой проверке, если заданы)
    for label, st in checks.layer_rule_rows(cfg):
        result.append((label, st))

    try:
        ok, detail = checks.test_audit(cfg)
        result.append(("аудит тестов (заглушки/глупые тесты)",
                       ("OK — " if ok else "FAIL — ") + detail))
    except Exception as e:
        result.append(("аудит тестов (заглушки/глупые тесты)", "ОШИБКА — " + str(e)[:150]))

    v = "PASS"
    for _, st in result:
        if st.startswith("FAIL"):
            v = "FAIL"
            break
        if st.startswith(("НЕТ", "НАЙДЕНЫ", "SKIP", "PARTIAL", "ОШИБКА")):
            v = "PARTIAL"

    verdict_name = f"{args.task}_Вердикт_контролёра_{templates.now()}.md"
    verdict_path = reports_dir / verdict_name
    rows = "\n".join(f"| {n} | {s} |" for n, s in result)
    verdict = templates.verdict_content(
        task_id=args.task, title=task.file.name, task_file=task.file.name,
        report=report.name, verdict=v, checks_rows=rows,
        confidence="High" if v == "PASS" else "Medium",
        evidence_status="— (контролёр проверяет доказательства по промпту)",
        fixes="— (контролёр заполняет: критичные FAIL, PARTIAL-оговорки, NEED_DATA)" if v != "PASS" else "—",
        notes="Механический вердикт автоконвейера; финальное решение — за контролёром.")
    verdict_path.write_text(verdict, encoding="utf-8")

    task.set_status("verified" if v == "PASS" else "rejected")
    if v == "PASS":
        shutil.move(str(task.file), str(cfg.abs_tasks_dir("archive") / task.file.name))
    checks.git(cfg.root, f"review/{args.task}: вердикт контролёра {v} ({verdict_name})")

    print(f"ВЕРДИКТ: {v} -> {verdict_path}")
    return 0 if v == "PASS" else 2


def cmd_status(cfg, _args):
    lines = [f"# СТАТУС КОНВЕЙЕРА — {templates.now()}", ""]
    for key, label in [("inbox", "Входящие (не оформлены)"), ("active", "Активные (в работе)"),
                       ("archive", "Архив (закрыты)")]:
        d = cfg.abs_tasks_dir(key)
        files = sorted(os.listdir(d)) if d.is_dir() else []
        lines.append(f"## {label}: {len(files)}")
        for f in files:
            if f.startswith("A-"):
                meta = Task.parse_frontmatter((d / f).read_text(encoding="utf-8"))
                lines.append(f"- {f} [статус: {meta.get('статус', '?')}]")
        lines.append("")
    reports_dir = cfg.abs_tasks_dir("reports")
    v = sorted(glob.glob(str(reports_dir / "*_Вердикт_*")))
    lines.append(f"## Вердикты: {len(v)}")
    for f in v[-8:]:
        lines.append(f"- {os.path.basename(f)}")
    status_file = cfg.resolve(cfg.status)
    status_file.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# Главный вход
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="pipeline.cli")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("list"); p.set_defaults(handler=cmd_list)
    p = sub.add_parser("env"); p.set_defaults(handler=cmd_env)

    def add_project(p):
        p.add_argument("project")

    p = sub.add_parser("status"); add_project(p)
    p.set_defaults(handler=lambda a: cmd_status(load_config(a.project), a))

    p = sub.add_parser("dispatch"); add_project(p)
    p.add_argument("file"); p.add_argument("--id"); p.add_argument("--title")
    p.add_argument("--priority"); p.add_argument("--requirements")
    p.add_argument("--result"); p.add_argument("--remark")
    p.set_defaults(handler=lambda a: cmd_dispatch(load_config(a.project), a))

    p = sub.add_parser("execute"); add_project(p)
    p.add_argument("task"); p.add_argument("--engine", choices=["qwen", "manual", "parallel"])
    p.set_defaults(handler=lambda a: cmd_execute(load_config(a.project), a))

    p = sub.add_parser("verify"); add_project(p); p.add_argument("task")
    p.set_defaults(handler=lambda a: cmd_verify(load_config(a.project), a))

    # --- TDL (JSON как источник истины) ---
    from pipeline.tdl import cli as tdl_cli

    def tdl(proj):
        return load_config(proj)

    def _t(fn):
        def h(a):
            cfg = tdl(a.project)
            return fn(cfg, a)
        return h

    for name, fn in [("tdl-init", tdl_cli.tdl_init),
                     ("tdl-dispatch", tdl_cli.tdl_dispatch),
                     ("tdl-plan", tdl_cli.tdl_plan),
                     ("tdl-start", tdl_cli.tdl_start),
                     ("tdl-report", tdl_cli.tdl_report),
                     ("tdl-verify", tdl_cli.tdl_verify),
                     ("tdl-migrate", tdl_cli.tdl_migrate),
                     ("tdl-validate", tdl_cli.tdl_validate),
                     ("tdl-index", tdl_cli.tdl_index),
                     ("tdl-tree", tdl_cli.tdl_tree),
                     ("tdl-close-summaries", tdl_cli.tdl_close_summaries),
                     ("tdl-status", tdl_cli.tdl_status)]:
        p = sub.add_parser(name); add_project(p)
        if name == "tdl-dispatch":
            p.add_argument("file"); p.add_argument("--title"); p.add_argument("--priority")
            p.add_argument("--wbs"); p.add_argument("--parent-wbs"); p.add_argument("--module")
            p.add_argument("--class-name"); p.add_argument("--layer"); p.add_argument("--goal")
            p.add_argument("--requirements"); p.add_argument("--result"); p.add_argument("--remark")
        elif name == "tdl-plan":
            p.add_argument("file"); p.add_argument("--title")
        elif name == "tdl-report":
            p.add_argument("task"); p.add_argument("--final", action="store_true"); p.add_argument("--from-md")
        elif name in ("tdl-start", "tdl-verify"):
            p.add_argument("task")
        elif name == "tdl-validate":
            p.add_argument("task", nargs="?", default=None)
        elif name == "tdl-migrate":
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--allow-done-from-md", action="store_true")
        p.set_defaults(handler=_t(fn))

    args = ap.parse_args(argv)
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 0
    try:
        return args.handler(args)
    except ConfigError as e:
        print(f"ОШИБКА КОНФИГА: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
