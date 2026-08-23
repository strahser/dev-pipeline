# -*- coding: utf-8 -*-
"""Агент-менеджер (оркестратор): приём миссии/ТЗ -> декомпозиция на подзадачи ->
запуск субагентов -> мониторинг -> сводка.

Это «толкающий» слой поверх сервера/файлов: менеджер НЕ исполняет задачи сам,
а поднимает отдельных агентов-исполнителей в отдельных ЯВНЫХ СЕССИЯХ на сервере
(каждая сессия = запись /api/sessions + тонкий session_worker.py, который читает
инструкцию с сервера и отчитывается через сервер; скиллы проекта и протокол
конвейера — как и раньше).

Режимы запуска субагента:
  - явная сессия (по умолчанию): POST /api/sessions + session_worker.py
    (инструкция/статусы/kill/abort — через сервер);
  - legacy: сервер недоступен (или --legacy) -> bash-`opencode run` напрямую;
  - parallel (по умолчанию): N субагентов одновременно;
  - sequential: по одному (полезно при общих файлах/сборке);
  - demo: без реального opencode (генерирует заглушечный отчёт для проверки цикла).

Запуск:
  python -m agents.agent_manager --project meptaggingsolution --mission <ТЗ.md> [--split 3]
  python -m agents.agent_manager --project meptaggingsolution --task A-01 --subagent
  python -m agents.agent_manager --project meptaggingsolution --mission <ТЗ.md> --demo
"""
from __future__ import annotations

import argparse
import datetime
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
from pipeline.proc import no_window_flags        # noqa: E402
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
DEFAULT_MODEL = "opencode-go/deepseek-v4-flash"  # стабильная модель (2x usage); flash-free глючит на длинных промптах
SUBAGENT_TIMEOUT = 1800  # анти-зависание: субагент без результата > 30 мин убивается
SERVER_URL = "http://127.0.0.1:8787"
DEV_PIPELINE_DIR = Path(__file__).resolve().parent.parent  # корень dev-pipeline (не хардкод E:\)


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс (Windows: tasklist; иначе os.kill(pid, 0))."""
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, errors="replace",
                                 timeout=10, creationflags=no_window_flags()).stdout or ""
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_tree(pid: int) -> None:
    """Убить процесс и всё его дерево (Windows: taskkill /F /T; иначе SIGKILL).

    opencode.cmd порождает node.exe — без /T умирает только обёртка cmd,
    а node-процесс остаётся сиротой и висит (случай A-12, ~1 ГБ памяти)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, text=True, errors="replace",
                           timeout=10, creationflags=no_window_flags())
            return
        except Exception:
            pass
    try:
        os.kill(pid, 9)
    except OSError:
        pass

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
SUBPROMPT = """ТЕБЕ ВЫДАНА КОНКРЕТНАЯ ЗАДАЧА: {task_file}

НЕ задавай вопросов, НЕ спрашивай «какую задачу выполнять», НЕ ищи задачи в Tasks\\Активные —
начинай работу немедленно с шага 0.

ПОРЯДОК РАБОТЫ (строго):
1. Прочитай {task_file} (контекст, требования, границы).
2. СРАЗУ применяй изменения в проекте: edit/write файлов. Не пиши план, не описывай намерения — редактируй.
3. После правок запусти сборку: dotnet build Core.Tests/Core.Tests.csproj --nologo -v q  (cwd = корень проекта). Убедись EXIT 0.
4. Запусти тесты: dotnet test Core.Tests/Core.Tests.csproj --nologo -v q. Убедись, что не хуже базового состояния
   (baseline в pipeline.yaml проекта; до правок обычно 8/15 — укажи фактическое в отчёте).
5. Создай отчёт ПО-РУССКИ в {report}: секции «Что было не так», «Что сделано» (пути файлов),
   «Доказательства» (выводы сборки/тестов), «Числа до/после», «Открытые вопросы», «Как пересобрать/проверить».
6. В шапке задачи {task_file} замени 'статус: in_progress' на 'статус: done_report'.
7. Коммит: git add -A; git commit -m "agent/{task_id}: отчёт исполнителя".

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


def _build_subprompt(cfg, task_id: str, task_file: Path, report_path: Path,
                     skill: str = "", worker: str = "",
                     prompt_override: str = "") -> str:
    """Собрать промпт субагента (общий для legacy и сессионного режима).

    prompt_override — полная замена базового SUBPROMPT (используется план-раннером)."""
    skill_line = (f"Загрузи скилл '{skill}' ({DEV_PIPELINE_DIR / 'skills' / skill / 'SKILL.md'}) "
                  f"для ролевых правил.\n"
                  f"ВАЖНО: твоя задача УЖЕ ВЫДАНА и прикреплена вложением: {task_file}. "
                  f"НЕ жди указаний, НЕ спрашивай 'какую задачу выполнять' и НЕ открывай "
                  f"Tasks\\Активные в поисках других задач — сразу приступай к шагам из промпта.\n"
                  if skill else "") + ""

    if prompt_override:
        # Безопасное форматирование: неизвестные/лишние {placeholder} в тексте
        # карточки (например, /buildings/{id}) не должны ронять раннер KeyError'ом.
        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"

        prompt = prompt_override.format_map(
            _SafeDict(task_file=task_file,
                      report=report_path,
                      task_id=task_id,
                      project=cfg.name)
        )
    else:
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
        qwen_bridge = DEV_PIPELINE_DIR / "agents" / "qwen_bridge.py"
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
            "  ШАГ 6. Создай отчёт (см. SUBPROMPT ниже).\n"
            "Прочитай скилл pipeline-qwen-worker (D:\\Projects\\revit-skills\\.opencode\\skills\\pipeline-qwen-worker\\SKILL.md) — "
            "там схема работы и команды моста.\n"
        )
        prompt = qwen_block + prompt
        if not skill:
            skill = qwen_skill
    if skill_line:
        prompt = skill_line + prompt
    return prompt


def _find_task_file(cfg, task_id: str) -> Path | None:
    """MD-файл задачи в Активные."""
    for f in glob.glob(str(cfg.abs_tasks_dir("active") / (task_id + "_*.md"))):
        return Path(f)
    return None


def run_subagent_legacy(cfg, task_id: str, report_path: Path, log_path: Path,
                        model: str = "", agent: str = "", skill: str = "", client=None,
                        worker: str = "", prompt_override: str = "") -> int:
    """Legacy-режим: opencode run напрямую из bash-процесса (фолбэк без сервера)."""
    task_file = _find_task_file(cfg, task_id)
    if not task_file:
        print(f"  [manager] задача {task_id} не найдена в Активные и в TDL JSON")
        return 2

    # open -> in_progress
    from pipeline.models import Task
    t = Task.from_file(task_file)
    if t.status == "open":
        t.set_status("in_progress")
        _publish(cfg, client, "task_started", task_id, {"file": task_file.name})
    _hb(client, f"subagent-{task_id}")

    prompt = _build_subprompt(cfg, task_id, task_file, report_path, worker=worker,
                              prompt_override=prompt_override)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [manager] субагент {task_id}: opencode run (legacy)"
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
    # PID-файл субагента: сторож может обнаружить и убить зависший процесс
    pid_file = log_path.parent / f"{task_id}.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(cmd, cwd=str(cfg.root),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=no_window_flags())
        # PID-файл: строка 1 — PID, строка 2 — время старта (unix).
        # Сторож (agent_watch) по нему находит сирот: менеджер убит/завис,
        # а субагент продолжает висеть.
        pid_file.write_text(f"{proc.pid}\n{int(time.time())}", encoding="utf-8")
        try:
            out, err = proc.communicate(timeout=SUBAGENT_TIMEOUT)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            # зависший субагент: убить всё дерево (без /T остаётся node-сирота)
            _kill_tree(proc.pid)
            try:
                out, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                out, err = "", ""
            rc = 124
        log_path.write_text((out or "") + (err or ""), encoding="utf-8")
        return rc
    except Exception as e:
        log_path.write_text(f"ОШИБКА ЗАПУСКА: {e}", encoding="utf-8")
        return 3
    finally:
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass


def run_subagent_session(cfg, task_id: str, report_path: Path, log_path: Path,
                         model: str = "", agent: str = "", skill: str = "", client=None,
                         worker: str = "", poll_sec: int = 20, prompt_override: str = "") -> int:
    """Явная сессия: инструкция/статус — через сервер (общение, не bash).

    Создаёт сессию на сервере (POST /api/sessions) с полной инструкцией,
    запускает тонкого session_worker.py (он читает инструкцию с сервера и
    отчитывается через сервер), мониторит статус сессии по API. Возвращает rc:
    0 — done+отчёт; 1 — failed; 124 — killed/stalled/таймаут; 2 — нет задачи."""
    task_file = _find_task_file(cfg, task_id)
    if not task_file:
        print(f"  [manager] задача {task_id} не найдена в Активные и в TDL JSON")
        return 2

    from pipeline.models import Task
    t = Task.from_file(task_file)
    if t.status == "open":
        t.set_status("in_progress")
        _publish(cfg, client, "task_started", task_id, {"file": task_file.name})

    prompt = _build_subprompt(cfg, task_id, task_file, report_path, worker=worker,
                              prompt_override=prompt_override)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    session = client.create_session(
        project=cfg.name, task=task_id, agent=f"session-{task_id}",
        role="qwen" if worker == "qwen" else "worker",
        model=model or DEFAULT_MODEL, skill=skill or worker,
        instruction={
            "task_file": str(task_file), "report": str(report_path),
            "log": str(log_path), "prompt": prompt, "model": model or DEFAULT_MODEL,
            "skill": skill or worker, "agent": agent or "", "worker": worker,
            "task_id": task_id,
        })
    if not session:
        print(f"  [manager] сессия {task_id} НЕ создана (сервер недоступен?) — legacy")
        return run_subagent_legacy(cfg, task_id, report_path, log_path,
                                   model=model, agent=agent, skill=skill,
                                   client=client, worker=worker,
                                   prompt_override=prompt_override)
    sid = session["id"]
    print(f"  [manager] субагент {task_id}: сессия {sid}"
          + (f" (model={session.get('model')})" if session.get("model") else "")
          + (f" (skill={skill})" if skill else "")
          + (f" (worker={worker})" if worker else ""))

    worker_script = DEV_PIPELINE_DIR / "agents" / "session_worker.py"
    cmd = [sys.executable, "-X", "utf8", str(worker_script),
           "--session", sid, "--url", SERVER_URL,
           "--project", cfg.name, "--cwd", str(cfg.root)]
    pid_file = log_path.parent / f"{task_id}.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    proc = None
    try:
        proc = subprocess.Popen(cmd, cwd=str(cfg.root),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=no_window_flags())
        pid_file.write_text(f"{proc.pid}\n{int(time.time())}", encoding="utf-8")
        # мониторинг через сервер (не через stdout процесса)
        deadline = time.time() + SUBAGENT_TIMEOUT
        terminal = ("done", "failed", "killed", "stalled")
        status = "created"
        while time.time() < deadline:
            cur = client.get_session(sid)
            if not cur:
                print(f"  [manager] {task_id}: сессия {sid} исчезла с сервера")
                break
            status = cur.get("status", "created")
            if status in terminal:
                break
            time.sleep(poll_sec)
        else:
            print(f"  [manager] {task_id}: таймаут {SUBAGENT_TIMEOUT} с — убиваю сессию {sid}")
            client.session_kill(sid)
            _kill_tree(proc.pid)
            status = "killed"
        cur = client.get_session(sid) or {}
        final = cur.get("status", status)
        note = (cur.get("note") or "")[:300]
        if note:
            print(f"  [manager] {task_id}: {note}")
        if final == "done":
            return 0
        if final in ("failed", "killed", "stalled"):
            err = (cur.get("error") or "")[:400]
            print(f"  [manager] {task_id}: сессия {final}" + (f" ({err})" if err else ""))
            return 124 if final in ("killed", "stalled") else 1
        print(f"  [manager] {task_id}: сессия завершилась со статусом {final}")
        return 124
    finally:
        if proc is not None:
            try:
                proc.wait(timeout=10)
            except Exception:
                _kill_tree(proc.pid)
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass


def run_subagent(cfg, task_id: str, report_path: Path, log_path: Path,
                 model: str = "", agent: str = "", skill: str = "", client=None,
                 worker: str = "", prompt_override: str = "") -> int:
    """Запустить субагента. Если сервер доступен — через ЯВНУЮ СЕССИЮ
    (инструкция и статусы через сервер), иначе legacy opencode run напрямую."""
    if client is not None and client.server_alive(timeout=3.0):
        return run_subagent_session(cfg, task_id, report_path, log_path,
                                    model=model, agent=agent, skill=skill,
                                    client=client, worker=worker,
                                    prompt_override=prompt_override)
    print(f"  [manager] сервер недоступен — legacy opencode run ({task_id})")
    return run_subagent_legacy(cfg, task_id, report_path, log_path,
                               model=model, agent=agent, skill=skill,
                               client=client, worker=worker,
                               prompt_override=prompt_override)


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
    for d in ("active", "reports"):
        cfg.abs_tasks_dir(d).mkdir(parents=True, exist_ok=True)
    title = args.title or mission.stem

    text = mission.read_text(encoding="utf-8")
    chunks = split_mission(text, args.split)
    total = len(chunks)
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
    """Запустить субагентов по задачам (parallel/sequential/demo).

    По умолчанию — ЯВНЫЕ СЕССИИ через сервер (инструкция и статусы через
    API; session_worker.py — тонкий клиент). --legacy или недоступный сервер —
    фолбэк на bash opencode run напрямую."""
    reports_dir = cfg.abs_tasks_dir("reports")
    logs_dir = cfg.root / "Tasks" / "Конвейер" / "logs"
    results = {}
    model = getattr(args, "model", "") or DEFAULT_MODEL
    agent = getattr(args, "agent", "")
    skill = getattr(args, "skill", "")
    worker = getattr(args, "worker", "")
    legacy = bool(getattr(args, "legacy", False))
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

    def _one_subagent(tid):
        report = reports_dir / f"{tid}_Отчёт_{now()}.md"
        log = logs_dir / f"{tid}_run.log"
        if legacy:
            rc = run_subagent_legacy(cfg, tid, report, log, model=model, agent=agent,
                                     skill=skill, client=client, worker=worker)
        else:
            rc = run_subagent(cfg, tid, report, log, model=model, agent=agent, skill=skill,
                              client=client, worker=worker)
        ok = _ensure_report(cfg, tid, rc)
        _publish(cfg, client, "subagent_finished", tid, {"rc": rc, "report": ok})
        return tid, rc, ok

    if args.sequential:
        for tid, rc, ok in (_one_subagent(tid) for tid in ids):
            results[tid] = rc
            print(f"  [manager] {tid}: rc={rc}, отчёт={'есть' if ok else 'НЕТ'}")
    else:
        # parallel: запускаем все разом, ждём по очереди
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
            for tid, rc, ok in ex.map(_one_subagent, ids):
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


def _ensure_report(cfg, tid: str, rc: int) -> bool:
    """Проверяет наличие отчёта исполнителя. Фейковый отчёт НЕ создаёт —
    он маскирует обрывы/зависания субагента. При rc != 0 или отсутствии
    отчёта — помечает задачу stalled (TDL history) и возвращает False."""
    reports_dir = cfg.abs_tasks_dir("reports")
    if glob.glob(str(reports_dir / (tid + "_Отчёт_*"))):
        return True
    _mark_stalled(cfg, tid,
                  f"субагент rc={rc} без отчёта — обрыв/зависание сессии, нужен редиспатч "
                  f"(лог: Tasks\\Конвейер\\logs\\{tid}_run.log)")
    return False


def _mark_stalled(cfg, tid: str, reason: str):
    """Пометка зависания: файл-маркер Tasks\\Конвейер\\stalled\\<tid>.txt (файлы = источник правды)."""
    try:
        d = cfg.root / "Tasks" / "Конвейер" / "stalled"
        d.mkdir(parents=True, exist_ok=True)
        marker = d / f"{tid}.txt"
        if not marker.exists():
            marker.write_text(f"{now()}\n{reason}\n", encoding="utf-8")
        print(f"  [manager] {tid}: пометка task_stalled — {reason}")
    except Exception as e:
        print(f"  [manager] stalled-пометка {tid} не сохранена: {e}")


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
    p.add_argument("--legacy", action="store_true",
                   help="без явной сессии: opencode run напрямую из bash (фолбэк)")
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
    p.add_argument("--legacy", action="store_true",
                   help="без явной сессии: opencode run напрямую из bash (фолбэк)")
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
