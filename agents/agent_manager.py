# -*- coding: utf-8 -*-
"""Агент-менеджер (оркестратор): приём миссии/ТЗ -> декомпозиция на подзадачи ->
запуск субагентов (отдельные opencode-сессии) -> мониторинг -> сводка.

Это «толкающий» слой поверх сервера/файлов: менеджер НЕ исполняет задачи сам,
а поднимает отдельных агентов-исполнителей в отдельных opencode-сессиях
(каждая сессия подхватывает скиллы проекта и протокол конвейера).

Режимы запуска субагента:
  - parallel (по умолчанию): N субагентов одновременно (каждый = `opencode run`);
  - sequential: по одному (полезно при общих файлах/сборке);
  - demo: без реального opencode (генерирует заглушечный отчёт для проверки цикла).

Запуск:
  python -m agents.agent_manager --project meptaggingsolution --mission <ТЗ.md> [--split 3]
  python -m agents.agent_manager --project meptaggingsolution --task A-01 --subagent
  python -m agents.agent_manager --project meptaggingsolution --mission <ТЗ.md> --demo
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config         # noqa: E402
from pipeline.templates import now               # noqa: E402
from pipeline.cli import cmd_dispatch            # noqa: E402
import argparse as _ap                           # noqa: E402

OPENCODE = r"C:\Users\Strakhov\AppData\Roaming\npm\opencode.cmd"
SUBPROMPT = """Ты — субагент-исполнитель конвейера dev-pipeline. Твоя задача уже выдана:
файл задачи (обязателен к прочтению, НЕ ищи другие задачи и НЕ жди новых): {task_file}
Протокол (обязателен): {protocol}
Правила проекта (контролёр): {controller_prompt}
Инструкция исполнителя: {executor_instr}
Требования:
1. НЕ мониторь Tasks\\Активные и не ищи «открытые задачи». Работай ТОЛЬКО с файлом {task_file}.
2. Прочитай файл задачи целиком (контекст, требования, границы).
3. Выполни требования. Доказательства — реальные выводы: сборка EXIT 0,
   тесты (dotnet test / vstest N/M), grep-выводы, пути файлов, коммиты agent/{task_id}.
4. Отчёт ПО-РУССКИ в {report} по шаблону протокола
   (секции: «Что было не так», «Что сделано», «Доказательства»,
   «Числа до/после», «Открытые вопросы», «Как пересобрать/проверить»).
5. После отчёта: в шапке задачи замени 'статус: in_progress' на 'статус: done_report'.
6. Коммит: git commit -m "agent/{task_id}: отчёт исполнителя".
7. Не имитируй действия; при невозможности — честно blocked/NEED_DATA в отчёте.
8. Не создавай субагентов, не меняй чужие файлы, не закрывай задачу сам.
9. СДЕЛАЙ РАБОТУ ДО КОНЦА: создай отчётный файл {report} и только потом завершайся.
"""


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


def split_mission(text: str, n: int) -> list[str]:
    """Разбить текст ТЗ на n подзадач по заголовкам '## ' (или по абзацам)."""
    parts = re.split(r"(?m)^(?=##\s)", text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < n:
        # мало секций — режем по абзацам
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        parts = paras
    if len(parts) <= n:
        return parts
    # равномерно сливаем в n кусков
    base = len(parts) // n
    rem = len(parts) % n
    chunks = []
    i = 0
    for k in range(n):
        size = base + (1 if k < rem else 0)
        chunks.append("\n\n".join(parts[i:i + size]))
        i += size
    return chunks


def dispatch_chunk(cfg, chunk: str, idx: int, total: int, title: str) -> str:
    """Создать задачу A-NN в Активные из куска миссии. Возвращает id."""
    tid = next_task_id(cfg)
    task_file = f"{tid}_{slug(title)}.md"
    dst = cfg.abs_tasks_dir("active") / task_file
    body = chunk.strip()[:4000]
    content = f"""---
id: {tid}
приоритет: высокий
статус: open
постановщик: агент-менеджер
исполнитель: subagent
дата: {now()}
источник_запроса: миссия (часть {idx}/{total})
замечание: миссия {title}
---

# ЗАДАЧА: {title} (часть {idx} из {total})

## Контекст (зачем, что уже известно)
{body}

## Требования (критерии приёмки)
Выполнить часть миссии из контекста. Каждое «сделано» — с доказательством
(лог сборки/тестов/grep, пути файлов, коммиты agent/{tid}).

## Границы (что НЕ делать)
- Не менять архитектуру сверх задачи; не трогать файлы вне своей части.
- Не коммитить: .idea\\, .opencode\\, bin\\obj, TestResults\\.
- Не создавать субагентов; задачу самому не закрывать (Архив — только контролёр).

## Результат (куда положить артефакты)
Отчёт — Tasks\\Отчёты\\{tid}_Отчёт_<дата>.md по шаблону протокола;
коммит agent/{tid}.

## Ход работы (заполняет исполнитель)
- (задача выдана {now()})
"""
    dst.write_text(content, encoding="utf-8")
    print(f"  [manager] задача {tid}: {task_file}")
    return tid


def subagent_env(cfg):
    """Строки окружения, которые субагент обязан прочитать."""
    return (
        f"task_file={cfg.abs_tasks_dir('active')}",
        f"protocol={cfg.resolve(cfg.protocol)}",
        f"controller_prompt={cfg.root / 'Tasks' / '00_Контролёр_промпт' / 'ControlerPromptv1.txt'}",
        f"executor_instr={cfg.root / 'Tasks' / 'Конвейер' / 'ИНСТРУКЦИЯ_исполнителю.md'}",
    )


def run_subagent(cfg, task_id: str, report_path: Path, log_path: Path,
                 model: str = "", agent: str = "", skill: str = "") -> int:
    """Запустить субагента (opencode run) в отдельной сессии. Возвращает rc.
    model — провайдер/модель (напр. opencode/deepseek-v4-flash-free);
    agent — роль opencode (--agent); skill — скилл, который субагент обязан загрузить."""
    task_file = None
    for f in glob.glob(str(cfg.abs_tasks_dir("active") / (task_id + "_*.md"))):
        task_file = Path(f)
        break
    if not task_file:
        print(f"  [manager] задача {task_id} не найдена в Активные")
        return 2

    # open -> in_progress
    from pipeline.models import Task
    t = Task.from_file(task_file)
    if t.status == "open":
        t.set_status("in_progress")

    skill_line = (f"Загрузи скилл '{skill}' (E:\\ПлагиныРевит\\dev-pipeline\\skills\\{skill}\\SKILL.md),\n"
                  if skill else "") + ""

    prompt = SUBPROMPT.format(
        task_file=task_file,
        protocol=cfg.resolve(cfg.protocol),
        controller_prompt=str(cfg.root / "Tasks" / "00_Контролёр_промпт" / "ControlerPromptv1.txt"),
        executor_instr=str(cfg.root / "Tasks" / "Конвейер" / "ИНСТРУКЦИЯ_исполнителю.md"),
        report=report_path,
        task_id=task_id,
    )
    if skill_line:
        prompt = skill_line + prompt
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [manager] субагент {task_id}: opencode run"
          + (f" (model={model})" if model else "")
          + (f" (agent={agent})" if agent else "")
          + (f" (skill={skill})" if skill else ""))
    cmd = [OPENCODE, "run", prompt]
    if model:
        cmd += ["-m", model]
    if agent:
        cmd += ["--agent", agent]
    # --auto: авто-подтверждение разрешений (иначе неинтерактивный субагент
    # останавливается на запросе записи файла и не завершает задачу)
    cmd += ["--auto"]
    try:
        r = subprocess.run(cmd, cwd=str(cfg.root),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=7200)
        log_path.write_text((r.stdout or "") + (r.stderr or ""), encoding="utf-8")
        return r.returncode
    except Exception as e:
        log_path.write_text(f"ОШИБКА ЗАПУСКА: {e}", encoding="utf-8")
        return 3


def run_subagent_demo(cfg, task_id: str, report_path: Path):
    """Заглушка для проверки цикла без реального opencode."""
    from pipeline.models import Task
    task_file = None
    for f in glob.glob(str(cfg.abs_tasks_dir("active") / (task_id + "_*.md"))):
        task_file = Path(f)
        break
    if not task_file:
        return 2
    t = Task.from_file(task_file)
    if t.status == "open":
        t.set_status("in_progress")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        f"# ОТЧЁТ: {task_id} — демо-субагент (проверка цикла)\n\n"
        f"**Дата:** {now()} | **Статус:** done (демо)\n\n"
        "## Что было не так\nДемо-режим: реальное выполнение не запускалось.\n\n"
        "## Что сделано\nПроверен цикл менеджера (задача → субагент → отчёт).\n\n"
        "## Доказательства\nДемо: файл отчёта создан; команды сборки не запускались.\n\n"
        "## Открытые вопросы\nРеальное выполнение — через реального субагента.\n\n"
        "## Как пересобрать/проверить\npython -m agents.agent_manager --project ... --mission ...",
        encoding="utf-8")
    t.set_status("done_report")
    print(f"  [manager] демо-отчёт {task_id}: {report_path}")
    return 0


def cmd_mission(args):
    cfg = load_config(args.project)
    mission = Path(args.mission)
    if not mission.exists():
        print("МИССИЯ НЕ НАЙДЕНА:", mission)
        return 1
    text = mission.read_text(encoding="utf-8")
    for d in ("active", "reports"):
        cfg.abs_tasks_dir(d).mkdir(parents=True, exist_ok=True)
    chunks = split_mission(text, args.split)
    total = len(chunks)
    title = args.title or mission.stem
    print(f"[manager] миссия '{title}': {total} подзадач")

    ids = []
    for i, chunk in enumerate(chunks, start=1):
        tid = dispatch_chunk(cfg, chunk, i, total, title)
        ids.append(tid)

    _run_batch(cfg, ids, args)
    return 0


def cmd_task(args):
    cfg = load_config(args.project)
    for d in ("active", "reports"):
        cfg.abs_tasks_dir(d).mkdir(parents=True, exist_ok=True)
    _run_batch(cfg, [args.task], args)
    return 0


def _run_batch(cfg, ids, args):
    """Запустить субагентов по задачам (parallel/sequential/demo)."""
    reports_dir = cfg.abs_tasks_dir("reports")
    logs_dir = cfg.root / "Tasks" / "Конвейер" / "logs"
    results = {}
    model = getattr(args, "model", "")
    agent = getattr(args, "agent", "")
    skill = getattr(args, "skill", "")

    if args.demo:
        for tid in ids:
            report = reports_dir / f"{tid}_Отчёт_{now()}.md"
            rc = run_subagent_demo(cfg, tid, report)
            results[tid] = rc
        _print_summary(cfg, ids, results)
        return

    if args.sequential:
        for tid in ids:
            report = reports_dir / f"{tid}_Отчёт_{now()}.md"
            log = logs_dir / f"{tid}_run.log"
            rc = run_subagent(cfg, tid, report, log, model=model, agent=agent, skill=skill)
            results[tid] = rc
            ok = _ensure_report(cfg, tid)
            print(f"  [manager] {tid}: rc={rc}, отчёт={'есть' if ok else 'НЕТ'}")
    else:
        # parallel: запускаем все разом, ждём по очереди
        from concurrent.futures import ThreadPoolExecutor
        def _one(tid):
            report = reports_dir / f"{tid}_Отчёт_{now()}.md"
            log = logs_dir / f"{tid}_run.log"
            rc = run_subagent(cfg, tid, report, log, model=model, agent=agent, skill=skill)
            return tid, rc, _ensure_report(cfg, tid)
        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
            for tid, rc, ok in ex.map(_one, ids):
                results[tid] = rc
                print(f"  [manager] {tid}: rc={rc}, отчёт={'есть' if ok else 'НЕТ'}")

    _print_summary(cfg, ids, results)


def _print_summary(cfg, ids, results):
    print("\n=== СВОДКА МЕНЕДЖЕРА ===")
    reports_dir = cfg.abs_tasks_dir("reports")
    for tid in ids:
        rc = results.get(tid)
        report = sorted(glob.glob(str(reports_dir / (tid + "_Отчёт_*"))))
        status = "ОК" if (rc == 0 and report) else (f"rc={rc}" if rc else "нет отчёта")
        print(f"  {tid}: {status}" + (f" -> {os.path.basename(report[-1])}" if report else ""))
    print("Дальше: контролёр запускает verify по каждой задаче.")


def _ensure_report(cfg, tid: str) -> bool:
    """Если субагент не создал отчёт, но задача была взята (in_progress) —
    сгенерировать отчёт-заглушку «работа начата, отчёт не завершён».
    Возвращает True, если отчёт есть (создан субагентом или менеджером)."""
    reports_dir = cfg.abs_tasks_dir("reports")
    if glob.glob(str(reports_dir / (tid + "_Отчёт_*"))):
        return True
    # задача была взята? (in_progress / done_report)
    task_file = None
    for f in glob.glob(str(cfg.abs_tasks_dir("active") / (tid + "_*.md"))):
        task_file = Path(f)
        break
    if not task_file:
        return False
    from pipeline.models import Task
    t = Task.from_file(task_file)
    if t.status not in ("in_progress", "done_report"):
        return False
    report = reports_dir / f"{tid}_Отчёт_{now()}.md"
    report.write_text(
        f"# ОТЧЁТ: {tid} — субагент не завершил отчёт (менеджер)\n\n"
        f"**Дата:** {now()} | **Статус:** partial\n\n"
        "## Что было не так\nСубагент (opencode run) не создал отчётный файл — обрыв сессии "
        "после выполнения работы (лог в Tasks\\Конвейер\\logs\\).\n\n"
        "## Что сделано\nЗадача была взята (статус in_progress); фактические изменения "
        "нужно проверить по git diff и логу субагента.\n\n"
        "## Доказательства\nЛог субагента: Tasks\\Конвейер\\logs\\%s_run.log\n\n"
        "## Открытые вопросы\nПроверить изменения вручную (git status/diff), запустить verify.\n\n"
        "## Как пересобрать/проверить\npython -m pipeline.cli verify <project> %s" % (tid, tid),
        encoding="utf-8")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agents.agent_manager")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("mission")
    p.add_argument("--project", default="meptaggingsolution")
    p.add_argument("--mission", required=True)
    p.add_argument("--split", type=int, default=3)
    p.add_argument("--title")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--sequential", action="store_true")
    p.add_argument("--parallel", type=int, default=2)
    p.add_argument("--model", default="", help="opencode-модель (напр. opencode/deepseek-v4-flash-free)")
    p.add_argument("--agent", default="", help="роль opencode (--agent)")
    p.add_argument("--skill", default="", help="скилл, который субагент обязан загрузить")
    p.set_defaults(handler=cmd_mission)

    p = sub.add_parser("task")
    p.add_argument("--project", default="meptaggingsolution")
    p.add_argument("--task", required=True)
    p.add_argument("--demo", action="store_true")
    p.add_argument("--sequential", action="store_true")
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--model", default="")
    p.add_argument("--agent", default="")
    p.add_argument("--skill", default="")
    p.set_defaults(handler=cmd_task)

    args = ap.parse_args(argv)
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 0
    try:
        return args.handler(args)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"ОШИБКА: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
