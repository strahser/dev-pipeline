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


def _opencode_cmd() -> str:
    """Путь к opencode: env OPENCODE_CMD, затем поиск в PATH/стандартных местах."""
    env = os.environ.get("OPENCODE_CMD")
    if env and os.path.exists(env):
        return env
    found = shutil.which("opencode")
    if found:
        return found
    # npm global on Windows
    npm = Path(os.environ.get("APPDATA", "")) / "npm" / "opencode.cmd"
    if npm.exists():
        return str(npm)
    return "opencode"


OPENCODE = _opencode_cmd()
DEFAULT_MODEL = "opencode/deepseek-v4-flash-free"  # экономия: free-модель по умолчанию
SERVER_URL = "http://127.0.0.1:8787"


def _publish(cfg, client, type_: str, task_id: str, payload: dict | None = None):
    """Опубликовать событие в сервер координации (опционально; молча при недоступности)."""
    if client is None:
        return
    try:
        client.notify(type_, to="feed", task=task_id, payload=payload or {})
    except Exception:
        pass


def _hb(client, name: str):
    """Отметить агента online на сервере (heartbeat) — чтобы панель показывала «работает»."""
    if client is None:
        return
    try:
        client._request("POST", "/heartbeat", body={"agent": name}, timeout=3.0)
    except Exception:
        pass
SUBPROMPT = """Выполни задачу из файла: {task_file}

ПОРЯДОК РАБОТЫ (строго):
0. Если есть TDL-задача Tasks\\JSON\\Active\\{task_id}.task.json — прочитай её ПЕРВОЙ
   (goal, acceptance_criteria, verification.commands) — это источник истины.
1. Прочитай {task_file} (контекст, требования, границы).
2. СРАЗУ применяй изменения в проекте: edit/write файлов. Не пиши план, не описывай намерения — редактируй.
3. После правок запусти сборку: dotnet build Core.Tests/Core.Tests.csproj --nologo -v q  (cwd = корень проекта). Убедись EXIT 0.
4. Запусти тесты: dotnet test Core.Tests/Core.Tests.csproj --nologo -v q. Убедись, что не хуже базового состояния
   (baseline в pipeline.yaml проекта; до правок обычно 8/15 — укажи фактическое в отчёте).
5. Создай отчёт ПО-РУССКИ в {report}: секции «Что было не так», «Что сделано» (пути файлов),
   «Доказательства» (выводы сборки/тестов), «Числа до/после», «Открытые вопросы», «Как пересобрать/проверить».
6. Если есть TDL-задача — создай также JSON-отчёт:
   python -X utf8 -m pipeline.cli tdl-report {project} {task_id} --from-md {report}
   (команда tdl-report в dev-pipeline; {project} = имя проекта, {task_id} = id задачи)
7. В шапке задачи {task_file} замени 'статус: in_progress' на 'статус: done_report'.
8. Коммит: git add -A; git commit -m "agent/{task_id}: отчёт исполнителя".

Правила:
- Временные файлы (логи тестов и т.п.) пиши В ПРОЕКТ (папка Tasks\\Конвейер\\logs\\), НЕ в %TEMP% —
  доступ к Temp может быть ограничен. Если запускаешь команду с редиректом в файл — используй
  путь внутри проекта.
- Не выдумывай выводы (сборка/тесты — реальные); не трогай файлы вне задачи.
- Не создавай субагентов; не закрывай задачу.
- ОТЧЁТНЫЙ ФАЙЛ {report} — ПОСЛЕДНИЙ ШАГ И ОБЯЗАТЕЛЕН. Не завершай сессию, пока файл {report}
  не создан и не содержит все секции. Проверь в конце, что файл существует (Test-Path).
- Если что-то не получается — пиши честно blocked/NEED_DATA в отчёте, но сначала сделай максимум изменений.
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
    """Создать задачу A-NN в Активные из куска миссии. Возвращает id.
    Если TDL включён — дополнительно создаёт JSON-задачу (wbs 1.idx, goal, criteria)."""
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

    # TDL JSON-задача (источник истины, если включено)
    if getattr(cfg, "tdl_enabled", True):
        try:
            from pipeline.tdl._tpl import make_task
            from pipeline.tdl import store as tdl_store
            wbs = f"1.{idx:02d}"
            t = make_task(
                task_id=tid, project=cfg.name, name=f"{title} (часть {idx}/{total})",
                wbs=wbs, priority="высокий", goal=body,
                acceptance=[body[:2000]],
                commands=_tdl_commands(cfg),
                source=f"миссия {title} (часть {idx}/{total})",
            )
            tdl_store.save_task(cfg, t)
            tdl_store.rebuild_index(cfg)
        except Exception as e:
            print(f"  [manager] TDL-задача {tid} не создана: {e}")

    print(f"  [manager] задача {tid}: {task_file}")
    return tid


def _tdl_commands(cfg) -> list:
    """Команды проверки из конфига проекта (build + test)."""
    cmds = []
    if cfg.msbuild.lower() == "dotnet" and cfg.sln:
        cmds.append(f"dotnet build {cfg.sln} --nologo -v q")
        cmds.append(f"dotnet test {cfg.sln} --nologo -v q")
    elif cfg.msbuild:
        cmds.append(f"\"{cfg.msbuild}\" {cfg.root / cfg.sln} /t:Build /p:Configuration={cfg.configuration} /p:Platform=\"{cfg.platform}\"")
    return cmds


def subagent_env(cfg):
    """Строки окружения, которые субагент обязан прочитать."""
    return (
        f"task_file={cfg.abs_tasks_dir('active')}",
        f"protocol={cfg.resolve(cfg.protocol)}",
        f"controller_prompt={cfg.root / 'Tasks' / '00_Контролёр_промпт' / 'ControlerPromptv1.txt'}",
        f"executor_instr={cfg.root / 'Tasks' / 'Конвейер' / 'ИНСТРУКЦИЯ_исполнителю.md'}",
    )


def run_subagent(cfg, task_id: str, report_path: Path, log_path: Path,
                 model: str = "", agent: str = "", skill: str = "", client=None,
                 worker: str = "") -> int:
    """Запустить субагента (opencode run) в отдельной сессии. Возвращает rc.
    model — провайдер/модель (напр. opencode/deepseek-v4-flash-free);
    agent — роль opencode (--agent); skill — скилл, который субагент обязан загрузить;
    client — Client сервера (опционально) для публикации событий;
    worker — 'qwen': субагент-рабочий, тяжёлую генерацию делает облачный Qwen через qwen_bridge."""
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
        _publish(cfg, client, "task_started", task_id, {"file": task_file.name})
    _hb(client, f"subagent-{task_id}")

    skill_line = (f"Загрузи скилл '{skill}' (E:\\ПлагиныРевит\\dev-pipeline\\skills\\{skill}\\SKILL.md),\n"
                  if skill else "") + ""

    prompt = SUBPROMPT.format(
        task_file=task_file,
        protocol=cfg.resolve(cfg.protocol),
        controller_prompt=str(cfg.root / "Tasks" / "00_Контролёр_промпт" / "ControlerPromptv1.txt"),
        executor_instr=str(cfg.root / "Tasks" / "Конвейер" / "ИНСТРУКЦИЯ_исполнителю.md"),
        report=report_path,
        task_id=task_id,
        project=cfg.name,
    )
    if worker == "qwen":
        qwen_skill = "pipeline-qwen-worker"
        qwen_bridge = r"E:\ПлагиныРевит\dev-pipeline\agents\qwen_bridge.py"
        qwen_block = (
            "ЗАПРЕТ: НЕ загружай и НЕ используй скиллы cloud-ai-bridge, revit-api, revit-3d-export, "
            "threejs-viewer и любые ДРУГИЕ скиллы, кроме pipeline-qwen-worker. Работай строго по шагам ниже.\n"
            "ДОПОЛНИТЕЛЬНО (режим бесплатного рабочего): тяжёлую генерацию файлов делает "
            "облачный Qwen через мост. ТВОЯ ЗАДАЧА УЖЕ ВЫДАНА — файл:\n"
            f"  {task_file}\n"
            "НЕ ищи задачи со статусом open в Tasks\\Активные, НЕ открывай общую беседу. "
            "Работай ТОЛЬКО с этим файлом задачи. Порядок (строго, каждый шаг реальной командой):\n"
            f"  ШАГ 1. Прочитай файл задачи {task_file} — это постановка.\n"
            "  ШАГ 2. Собери контекст: прочитай нужные файлы проекта (read/grep/glob), "
            "определи файлы, которые надо исправить.\n"
            f"  ШАГ 3. Вызови мост (один вызов, question в одну строку):\n"
            f"    python -X utf8 \"{qwen_bridge}\" --task \"{task_file}\" "
            "--context <пути через запятую> --out Tasks\\00_Референсы\\Qwen_<тема>.md\n"
            f"  ШАГ 4. Примени файлы, которые Qwen «написал»:\n"
            f"    python -X utf8 \"{qwen_bridge}\" --task \"{task_file}\" "
            "--out Tasks\\00_Референсы\\Qwen_<тема>.md --apply --dir \"<корень проекта>\"\n"
            "  ШАГ 5. Проверь сборку и тесты (см. команды в задаче/конфиге). При ошибках — "
            "отправь лог Qwen на исправление (повторный вызов моста с логом в --context).\n"
            "  ШАГ 6. Создай отчёт (см. SUBPROMPT ниже) и JSON-отчёт.\n"
            "Прочитай скилл pipeline-qwen-worker (E:\\ПлагиныРевит\\dev-pipeline\\skills\\pipeline-qwen-worker\\SKILL.md) — "
            "там схема работы и команды моста.\n"
        )
        prompt = qwen_block + prompt
        if not skill:
            skill = qwen_skill
    if skill_line:
        prompt = skill_line + prompt
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [manager] субагент {task_id}: opencode run"
          + (f" (model={model})" if model else "")
          + (f" (agent={agent})" if agent else "")
          + (f" (skill={skill})" if skill else "")
          + (f" (worker={worker})" if worker else ""))
    cmd = [OPENCODE, "run", prompt]
    if model:
        cmd += ["-m", model]
    if agent:
        cmd += ["--agent", agent]
    # Прикрепить файл задачи как вложение: субагент гарантированно видит постановку
    # (иначе при загрузке скилла путается «какой файл?»).
    if task_file and task_file.exists():
        cmd += ["-f", str(task_file)]
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
    model = getattr(args, "model", "") or DEFAULT_MODEL
    agent = getattr(args, "agent", "")
    skill = getattr(args, "skill", "")
    worker = getattr(args, "worker", "")
    # Публикация событий в сервер (опционально): позволяет панели показывать ход задач.
    client = None
    try:
        from pipeline.client import Client
        client = Client("agent-manager", project=cfg.name, base_url=SERVER_URL,
                        notif_dir=str(cfg.resolve(cfg.notif)))
    except Exception:
        client = None

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
            rc = run_subagent(cfg, tid, report, log, model=model, agent=agent, skill=skill,
                              client=client, worker=worker)
            results[tid] = rc
            ok = _ensure_report(cfg, tid)
            _publish(cfg, client, "subagent_finished", tid,
                     {"rc": rc, "report": ok})
            print(f"  [manager] {tid}: rc={rc}, отчёт={'есть' if ok else 'НЕТ'}")
    else:
        # parallel: запускаем все разом, ждём по очереди
        from concurrent.futures import ThreadPoolExecutor
        def _one(tid):
            report = reports_dir / f"{tid}_Отчёт_{now()}.md"
            log = logs_dir / f"{tid}_run.log"
            rc = run_subagent(cfg, tid, report, log, model=model, agent=agent, skill=skill,
                              client=client, worker=worker)
            ok = _ensure_report(cfg, tid)
            _publish(cfg, client, "subagent_finished", tid,
                     {"rc": rc, "report": ok})
            return tid, rc, ok
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
    # TDL JSON-отчёт (источник истины)
    if getattr(cfg, "tdl_enabled", True):
        try:
            from pipeline.tdl import cli as tdl_cli
            from pipeline.tdl import store as tdl_store
            if tdl_store.load_task(cfg, tid):
                tdl_cli.tdl_report(cfg, _ap.Namespace(
                    task=tid, final=False, from_md=str(report)))
        except Exception as e:
            print(f"  [manager] TDL-отчёт {tid} не создан: {e}")
    return True


def cmd_report(args):
    """Отчёт менеджера: сводка по целям проекта (задачи по статусам, вердикты,
    рекомендации). Используется для контроля целей проекта по отчёту менеджера."""
    from pipeline.models import Task
    cfg = load_config(args.project)
    lines = [f"# ОТЧЁТ МЕНЕДЖЕРА: {cfg.name} — {now()}", ""]
    for key, label in [("inbox", "Входящие"), ("active", "Активные"),
                       ("archive", "Архив"), ("reports", "Отчёты")]:
        d = cfg.abs_tasks_dir(key)
        files = sorted(os.listdir(d)) if d.is_dir() else []
        a = [f for f in files if f.startswith("A-")]
        lines.append(f"## {label}: {len(a)}")
        for f in a:
            if key in ("inbox", "reports"):
                lines.append(f"- {f}")
                continue
            try:
                meta = Task.parse_frontmatter((d / f).read_text(encoding="utf-8"))
                lines.append(f"- {f} [статус: {meta.get('статус', '?')}]")
            except Exception:
                lines.append(f"- {f}")
        lines.append("")
    # Вердикты и статусы
    reports_dir = cfg.abs_tasks_dir("reports")
    verdicts = sorted(glob.glob(str(reports_dir / "*_Вердикт_*")), reverse=True)
    lines.append(f"## Вердикты: {len(verdicts)}")
    for f in verdicts[:10]:
        lines.append(f"- {os.path.basename(f)}")
    lines.append("")
    # Рекомендации
    active = cfg.abs_tasks_dir("active")
    open_tasks = [f for f in os.listdir(active) if f.startswith("A-")] if active.is_dir() else []
    lines.append("## Рекомендации")
    if not open_tasks:
        lines.append("- Нет активных задач. Можно запустить новую миссию.")
    else:
        lines.append(f"- Активных задач: {len(open_tasks)}. Запусти субагентов: "
                     f"python -m agents.agent_manager task --project {cfg.name} --task A-XX --sequential")
    text = "\n".join(lines)
    out = cfg.resolve(cfg.status).with_name("Отчёт_менеджера.md") \
        if Path(cfg.status).name == "Статус_конвейера.md" else cfg.resolve(cfg.status)
    Path(out).write_text(text, encoding="utf-8")
    print(text)
    return 0


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
    p.add_argument("--worker", default="", choices=["", "qwen"],
                   help="qwen — бесплатный рабочий: генерацию файлов делает облачный Qwen")
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
    p.add_argument("--worker", default="", choices=["", "qwen"],
                   help="qwen — бесплатный рабочий: генерацию файлов делает облачный Qwen")
    p.set_defaults(handler=cmd_task)

    p = sub.add_parser("report")
    p.add_argument("--project", default="meptaggingsolution")
    p.set_defaults(handler=cmd_report)

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
