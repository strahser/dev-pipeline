# -*- coding: utf-8 -*-
"""Автономный терминал агента: цикл порций с /new-эквивалентом (карточка 2.1).

opencode не умеет нажимать /new в ЧУЖОМ работающем TUI (`opencode session` —
только list/delete), но TUI запускается сразу с промптом:
    opencode <root> --prompt "..." --auto
Поэтому автономность делается циклом в ОДНОМ видимом окне:

  порция = свежая сессия opencode (чистый контекст, как после /new)
    -> агент дописал порцию и записал handoff-файл
       Tasks\\Конвейер\\handoff\\<метка>.md (формат 6.2)
    -> цикл подхватывает handoff и запускает СЛЕДУЮЩУЮ чистую сессию
       с базовым промптом + хвостом handoff
  нет handoff / падение сессии / лимит restart_policy -> цикл завершён.

Права write разворачиваются перед стартом (crew.ensure_permissions).
Запуск из панели: кнопка «🖥 Терминал» -> POST /api/chat/agents/terminal
(новое окно консоли, промпт через env PIPELINE_TUI_PROMPT). Вручную:
    python -X utf8 agents/tui_cycle.py --project <p> [--role executor] [--prompt "..."]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HANDOFF_TAIL_LIMIT = 6000

ROLE_STARTERS = {
    "executor": (
        "Ты АВТОНОМНЫЙ ИСПОЛНИТЕЛЬ проекта {project}. Работай по правилам AGENTS.md "
        "репозитория конвейера и цели проекта ниже. Порция = законченная правка: "
        "код -> сборка/тесты -> коммит (явные пути, префиксы из протокола). "
        "В КОНЦЕ каждой порции запиши handoff-файл "
        "Tasks\\Конвейер\\handoff\\<метка времени>.md (секции: ГОТОВО, ЗАДАЧА дальше, "
        "Грабли) и заверши работу — по нему начнётся следующая чистая сессия."),
    "controller": (
        "Ты КОНТРОЛЁР проекта {project}: ведёшь план ProjectsPalns, запускаешь "
        "карточки план-раннером, принимаешь вердикты, отвечаешь на вопросы агентов. "
        "Каждую порцию завершай handoff-файлом Tasks\\Конвейер\\handoff\\<метка>.md."),
    "creator": (
        "Ты КРЕАТОР интерфейсов проекта {project}: генерируешь идеи и конкретные "
        "UI-улучшения по референсам, оформляешь их в Tasks\\00_Референсы\\*.md "
        "(проблема -> предложение -> где в коде применить -> приоритет). Каждую "
        "порцию завершай handoff-файлом Tasks\\Конвейер\\handoff\\<метка>.md."),
    "reviewer": (
        "Ты НЕЗАВИСИМЫЙ РЕВЬЮЕР проекта {project}: проверяешь выполненную работу "
        "(git diff/log, тесты, соответствие цели), пишешь вердикты; код не правишь. "
        "Каждую порцию завершай handoff-файлом Tasks\\Конвейер\\handoff\\<метка>.md."),
    "planner": (
        "Ты ПЛАНИРОВЩИК проекта {project}: декомпозируешь цели в план ProjectsPalns "
        "(этапы -> карточки с критериями приёмки и зависимостями). Каждую порцию "
        "завершай handoff-файлом Tasks\\Конвейер\\handoff\\<метка>.md."),
    "browser": (
        "Ты ОБЛАЧНЫЙ МОСТ проекта {project}: забираешь задания из "
        "Tasks\\Конвейер\\Браузер\\*.txt, передаёшь промпты облачному ИИ (LocalAssitent) "
        "и сохраняешь ответы. Каждую порцию завершай handoff-файлом."),
    "manager": (
        "Ты ОБЩИЙ МЕНЕДЖЕР конвейера (НА ВСЕ проекты): следишь за всеми проектами "
        "(/api/pulse_all, /api/checkpoints, /api/sessions), принимаешь этапы. "
        "ПРИЁМКА ЧЕКПОИНТА: pending-чекпоинт -> прочитай GOAL.md проекта + вердикты + "
        "diff/log последнего коммита -> напиши файл Tasks\\Конвейер\\checkpoints\\"
        "<CARD>.decision.json {decision: approve|retry, comment, actor: manager}. "
        "СПОРНОЕ: если не хватает данных или решение неоднозначно — задай вопрос "
        "владельцу через Tasks\\Вопросы\\<CARD>_<время>.md. "
        "ГРАНИЦЫ: код НЕ правишь (только приёмка и вопросы), запись на диск — только "
        "файлы decision.json/вопросов по правилам протокола. Каждую порцию завершай "
        "handoff-файлом Tasks\\Конвейер\\handoff\\<метка>.md."),
}


def _starter(role: str) -> str:
    """Стартовый текст роли; неизвестные роли получают универсальный."""
    if role in ROLE_STARTERS:
        return ROLE_STARTERS[role]
    return (f"Ты АВТОНОМНЫЙ АГЕНТ роли «{role}» проекта {{project}}. Работай по "
            "правилам AGENTS.md репозитория конвейера и цели проекта ниже. Каждую "
            "порцию завершай handoff-файлом "
            "Tasks\\Конвейер\\handoff\\<метка времени>.md.")


def auto_task_manager() -> str:
    """Автозадание для ОБЩЕГО менеджера: сводка по ВСЕМ проектам (пульс, чекпоинты,
    вопросы, зависшие) — чтобы менеджер знал, где принимать этапы."""
    lines: list[str] = []
    client = None
    try:
        from pipeline.client import Client
        c = Client("manager", project="")
        if c.server_alive():
            client = c
    except Exception:
        client = None

    if client is not None:
        try:
            data = client._request("GET", "/api/pulse_all")
            for it in data.get("projects", []):
                prog = it.get("overall") or {}
                head = f"- {it['project']}: {prog.get('done', 0)}/{prog.get('total', 0)}"
                if it.get("plan_title"):
                    head += f" — {it['plan_title']}"
                lines.append(head)
                if it.get("checkpoints_open"):
                    lines.append(f"    ⏸ чекпоинтов к приёмке: {it['checkpoints_open']}")
                if it.get("questions_open"):
                    lines.append(f"    ❓ открытых вопросов: {it['questions_open']}")
                if it.get("stalled"):
                    lines.append(f"    ⚠ зависшие: {', '.join(it['stalled'])}")
        except Exception as e:
            lines.append(f"- pulse_all недоступен: {e}")
    else:
        lines.append("- сервер недоступен — сводка по файлам (см. Tasks\\Конвейер)")

    tail = ("ЗАДАНИЕ: прими этапы по чекпоинтам (pending -> прочитай GOAL.md + вердикты + "
            "diff/log -> decision.json approve/retry, actor: manager); спорное — вопрос "
            "владельцу через Tasks\\Вопросы. Код НЕ правишь.")
    return "\n".join(["ТЕКУЩЕЕ СОСТОЯНИЕ ПРОЕКТОВ:"] + (lines or ["- нет данных"]) + ["", tail])


def auto_task(cfg) -> str:
    """Автозадание для СУЩЕСТВУЮЩЕГО проекта: продолжить работу по контексту
    (панель запускает агента не «в пустоту», а с конкретным состоянием дел)."""
    lines: list[str] = []
    runner_active = (cfg.conveyor_dir() / "runner.lock").exists()
    if runner_active:
        lines.append("- ВНИМАНИЕ: план-раннер работает (runner.lock) — карточки "
                     "плана НЕ трогать, возможен конфликт")
    try:
        pf = cfg.find_plan_file()
        if pf:
            from pipeline.plans import load as load_plan
            prog = load_plan(pf).progress()
            lines.append(f"- план: {pf.name} (выполнено {prog['done']}/{prog['total']})")
            if not runner_active:
                ready = load_plan(pf).ready_cards()
                if ready:
                    c = ready[0]
                    lines.append(f"- следующая карточка: {c.id} — {c.title}")
                else:
                    lines.append("- готовых карточек нет (ждут зависимостей/владельца)")
        else:
            lines.append("- файла плана нет (ProjectsPalns\\<проект>\\_current пуст)")
    except Exception as e:
        lines.append(f"- план недоступен: {e}")
    try:
        active = cfg.abs_tasks_dir("active")
        open_tasks = []
        for tf in sorted(active.glob("*.md")) if active.is_dir() else []:
            try:
                head = tf.read_text(encoding="utf-8", errors="replace")[:500]
            except OSError:
                continue
            if "статус:" in head and any(
                    f"статус: {s}" in head for s in ("open", "in_progress")):
                open_tasks.append(tf.name)
        if open_tasks:
            lines.append("- открытые задачи: " + ", ".join(open_tasks[:5]))
    except Exception:
        pass
    tail = ("ЗАДАНИЕ: продолжи работу проекта по контексту выше — возьми верхнюю "
            "открытую задачу/карточку и доведи до критериев приёмки "
            "(правки -> сборка/тесты -> коммит по протоколу).")
    return "\n".join(["ТЕКУЩЕЕ СОСТОЯНИЕ ПРОЕКТА:"] + lines + ["", tail])


def build_base_prompt(cfg, role: str = "executor", user_prompt: str = "") -> str:
    """Базовый промпт каждой порции: роль + цель проекта + задание владельца.
    Без явного задания — автозадание по контексту существующего проекта."""
    starter = _starter(role)
    # replace, а не format: в промптах ролей есть JSON-скобки {decision: ...},
    # format принимает их за плейсхолдеры и падает KeyError (инцидент manager 2026-08-24)
    parts = [starter.replace("{project}", cfg.name)]
    try:
        from pipeline.brief import goal_section
        goal = goal_section(cfg)
        if goal:
            parts.append("ЦЕЛЬ ПРОЕКТА:\n" + goal)
    except Exception:
        pass
    parts.append(user_prompt if user_prompt else
                 (auto_task_manager() if role == "manager" else auto_task(cfg)))
    return "\n\n".join(parts)


def newest_handoff(cfg, after_ts: float) -> Path | None:
    """Новейший handoff-файл, появившийся ПОСЛЕ старта порции.
    Сравнение по st_mtime_ns (разрешение времени + тай-брейк по имени),
    чтобы при быстрых порциях не выбирался файл предыдущей итерации."""
    d = cfg.conveyor_dir() / "handoff"
    if not d.is_dir():
        return None
    after_ns = int(after_ts * 1e9)
    fresh = [p for p in d.glob("*.md")
             if p.stat().st_mtime_ns >= after_ns]
    if not fresh:
        return None
    return max(fresh, key=lambda p: (p.stat().st_mtime_ns, p.name))


def default_runner(cfg, role: str, registry: dict | None = None):
    """Реальный запуск: видимая сессия opencode TUI в текущей консоли.
    registry["proc"] — текущий процесс opencode (для «⛔ Убить» из панели)."""

    def _run(c, prompt: str) -> int:
        import subprocess

        from agents.session_worker import _opencode_base
        from pipeline.proc import no_window_flags
        cmd = _opencode_base() + [str(c.root), "--prompt", prompt]
        print(f"[tui] opencode-сессия запущена ({c.name}, {len(prompt)} символов "
              f"промпта) — работайте в окне; выход завершит порцию")
        proc = subprocess.Popen(cmd, cwd=str(c.root),
                                creationflags=no_window_flags())
        if registry is not None:
            registry["proc"] = proc
        return proc.wait()
    return _run


def _register_server_session(client, cfg, role: str, base_prompt: str):
    """Регистрация в панели: сессия с PID/heartbeat — видна и убиваема."""
    import os as _os
    import threading

    try:
        s = client.create_session(
            project=cfg.name, task=f"tui:{role}", agent=f"tui-{role}",
            role=role, model="", skill="",
            instruction={"mode": "tui_cycle", "prompt": base_prompt})
        if not s:
            return "", None
        sid = s.get("id", "")
        client.session_start(
            sid, pid=_os.getpid(),
            cmd=(f"{Path(sys.executable).name} -X utf8 agents/tui_cycle.py "
                 f"--project {cfg.name} --role {role}"))
        client.session_status(sid, "running", note="терминальный агент запущен")
    except Exception as e:
        print(f"[tui] регистрация сессии не удалась (работаю без панели): {e}")
        return "", None

    registry: dict = {}
    stop = threading.Event()

    def _hb():
        while not stop.is_set():
            try:
                client.session_heartbeat(sid)
            except Exception:
                pass
            stop.wait(20)

    def _on_event(ev):
        text = (ev.get("text") or "").lower()
        if ev.get("type") in ("session_instruction", "message") and \
                ("abort" in text or "stop" in text):
            print("[tui] инструкция из панели: прерываю")
            proc = registry.get("proc")
            if proc is not None and proc.poll() is None:
                proc.terminate()
        elif ev.get("type") in ("session_instruction", "message"):
            # Владелец пишет из панели (💬 чат -> агент tui-*): подтверждаем,
            # чтобы диалог был двусторонним, а не «только исходящие».
            src = ev.get("from") or "dashboard"
            try:
                client.send_message(
                    src, f"[tui-{role}] принял: {(ev.get('text') or '')[:200]} | "
                         f"статус: работаю в окне терминала; срочное — напишите "
                         f"прямо в окно opencode или Tasks\\Вопросы.")
            except Exception:
                pass

    threading.Thread(target=_hb, daemon=True, name=f"hb-{sid}").start()
    try:
        threading.Thread(target=client.subscribe,
                         args=(_on_event, stop),
                         daemon=True, name=f"sse-{sid}").start()
    except Exception:
        pass
    return sid, {"stop": stop, "registry": registry}


def run_cycle(cfg, *, role: str = "executor", user_prompt: str = "",
              runner=None, log=print, client=None) -> int:
    """Цикл порций. Возвращает число выполненных сессий.

    Стоп: нет handoff после порции (агент решил, что продолжать нечего),
    rc != 0 (падение сессии), лимит restart_policy.max_restarts + 1.
    client (опционально): регистрация в панели — сессия с PID видна в
    «🗂 Сессии», heartbeat держит её «живой», «⛔ Убить» прерывает opencode."""
    if runner is None:
        registry = {} if client is not None else None
        runner = default_runner(cfg, role, registry)
    else:
        registry = None
    base = build_base_prompt(cfg, role, user_prompt)

    sid, ctl = ("", None)
    if client is not None:
        sid, ctl = _register_server_session(client, cfg, role, base)

    limit = int(getattr(cfg, "restart_max", 3)) + 1
    prompt = base
    done = 0
    fails = 0
    misses = 0
    try:
        for i in range(limit):
            started = time.time()
            rc = runner(cfg, prompt)
            if rc != 0:
                # Провайдер ИИ периодически перегружен: НЕ умираем, а дёргаем
                # повтор порции (свежая сессия), пока не исчерпан лимит.
                fails += 1
                if fails <= int(getattr(cfg, "restart_max", 3)):
                    cd = min(120, max(5, int(getattr(
                        cfg, "restart_cooldown_sec", 30))))
                    log(f"[tui] порция {i + 1}: rc={rc} (перегрузка провайдера?) "
                        f"— повтор через {cd} с ({fails}/{limit})")
                    time.sleep(cd)
                    continue
                log(f"[tui] неудачных попыток подряд исчерпано — цикл окончен")
                break
            fails = 0
            done += 1
            h = newest_handoff(cfg, started - 1)
            if h is None:
                # Агент ответил, но протокол порции (handoff-файл) не выполнил:
                # «движуха» не должна гаснуть — повторяем ту же порцию.
                misses += 1
                if misses <= int(getattr(cfg, "restart_max", 3)):
                    cd = min(60, max(5, int(getattr(
                        cfg, "restart_cooldown_sec", 30)) // 2))
                    log(f"[tui] порция {i + 1}: handoff нет (ответил без "
                        f"протокола?) — повтор через {cd} с ({misses}/{limit})")
                    time.sleep(cd)
                    continue
                log(f"[tui] порций без handoff подряд исчерпано — цикл окончен")
                break
            misses = 0
            body = h.read_text(encoding="utf-8", errors="replace")[-HANDOFF_TAIL_LIMIT:]
            prompt = (base + "\n\nHANDOFF ПРЕДЫДУЩЕЙ СЕССИИ (продолжи с этого места):\n"
                      + body)
            log(f"[tui] порция {i + 1} готова, handoff: {h.name} — новая чистая "
                f"сессия (/new)")
            if client is not None and sid:
                try:
                    client.session_status(sid, "running",
                                          note=f"порций выполнено: {done}")
                except Exception:
                    pass
    finally:
        if client is not None and sid:
            try:
                client.session_status(sid, "done",
                                      note=f"цикл завершён, порций: {done}")
            except Exception:
                pass
            if ctl is not None:
                ctl["stop"].set()
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agents.tui_cycle",
                                 description="Автономный терминал агента "
                                             "(цикл порций, /new-эквивалент)")
    ap.add_argument("--project", default="",
                    help="проект (пусто допустимо только для --role manager)")
    ap.add_argument("--role", default="executor")
    ap.add_argument("--headless", action="store_true",
                    help="role=manager: страховочный python-цикл project_manager "
                         "вместо видимой opencode-сессии")
    ap.add_argument("--prompt", default="",
                    help="стартовое задание (иначе env PIPELINE_TUI_PROMPT)")
    a = ap.parse_args(argv)

    from pipeline.config import ConfigError, load_config
    if a.role == "manager" and a.headless:
        # Страховка: общий менеджер без opencode-сессии — python-цикл project_manager
        from agents import project_manager
        m_argv = ([f"--project={a.project}"] if a.project else [])
        return project_manager.main(m_argv)

    if a.role == "manager" and not a.project:
        # ОБЩИЙ менеджер на все проекты: cfg берём у первого проекта как рабочий
        # (для handoff/прав/конвейер-папки), сам промпт — сводка по всем проектам.
        from pipeline.config import list_projects
        names = list_projects()
        if not names:
            print("[tui] нет ни одного проекта — менеджеру нечего вести")
            return 2
        a.project = names[0]

    if not a.project:
        print(f"[tui] для роли {a.role} нужен --project (кроме manager)")
        return 2
    try:
        cfg = load_config(a.project)
    except ConfigError as e:
        print(f"[tui] проект не найден: {e}")
        return 2
    try:
        from pipeline.crew import ensure_permissions
        perm = ensure_permissions(cfg)
        print("[tui] права opencode: "
              + (f"шаблон создан: {perm}" if perm else f"уже есть ({cfg.crew_permissions})"))
    except Exception as e:
        print(f"[tui] профиль прав не проверён: {e}")

    prompt = a.prompt or __import__("os").environ.get("PIPELINE_TUI_PROMPT", "")

    client = None
    try:
        from pipeline.client import Client
        c = Client(f"tui-{a.role}", project=cfg.name)
        if c.server_alive():
            client = c
            print("[tui] регистрируюсь в панели (🗂 Сессии) — там виден PID и «Убить»")
    except Exception:
        client = None

    n = run_cycle(cfg, role=a.role, user_prompt=prompt, client=client)
    print(f"[tui] цикл завершён: сессий {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
