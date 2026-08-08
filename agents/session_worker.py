# -*- coding: utf-8 -*-
"""Рабочий явной сессии субагента: инструкция и статус — через сервер.

Контролёр (agent_manager) создаёт сессию на сервере (POST /api/sessions) с
инструкцией (JSON: task_file, report, log, prompt, model, skill...). Этот
рабочий подхватывает сессию:

  1. GET /api/sessions/<sid>   — читает инструкцию с СЕРВЕРА (не из bash-аргументов);
  2. POST /api/sessions/<sid>/start — регистрирует pid/cmd;
  3. запускает opencode run (движок исполнения) с промптом из инструкции;
  4. фоновый heartbeat сессии (живость для watchdog);
  5. SSE-подписка на канал session-<sid>: инструкция 'abort'/'stop' — убить opencode;
  6. POST /api/sessions/<sid>/status — done (report) или failed (error).

Фолбэк: сервер недоступен -> rc=4 и код завершения, менеджер пометит stalled.

Запуск (из agent_manager):
    python -X utf8 agents/session_worker.py --session S-XXXX --url http://127.0.0.1:8787
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.client import Client  # noqa: E402
from pipeline.proc import no_window_flags  # noqa: E402

HEARTBEAT_SEC = 30
SUBAGENT_TIMEOUT = int(os.environ.get("SUBAGENT_TIMEOUT_SEC", "1800"))


def _find_opencode() -> str:
    """Найти opencode: env OPENCODE_CMD, PATH, npm global (как в agent_manager)."""
    import shutil
    env = os.environ.get("OPENCODE_CMD")
    if env and os.path.exists(env):
        return env
    found = shutil.which("opencode")
    if found:
        return found
    npm = Path(os.environ.get("APPDATA", "")) / "npm" / "opencode.cmd"
    if npm.exists():
        return str(npm)
    return "opencode"


OPENCODE = os.environ.get("OPENCODE_CMD") or _find_opencode()


def _pid_alive(pid: int) -> bool:
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


def _opencode_base() -> list[str]:
    """OPENCODE_CMD: 'opencode' или полная команда. На Windows пути с пробелами
    — в кавычках; backslash НЕ экранируем (posix=False ломает пути)."""
    env = OPENCODE.strip()
    if not env:
        return ["opencode"]
    if env.startswith('"') and env.endswith('"'):
        return [env[1:-1]]
    # простой split по пробелам вне кавычек (Windows-пути без \x-экранирования)
    parts, cur, inq = [], "", False
    for ch in env:
        if ch == '"':
            inq = not inq
        elif ch == " " and not inq:
            if cur:
                parts.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts or ["opencode"]


def _hb_loop(client: Client, sid: str, stop: threading.Event):
    """Фоновый heartbeat сессии: сервер знает, что субагент жив."""
    while not (stop and stop.is_set()):
        client.session_heartbeat(sid)
        time.sleep(HEARTBEAT_SEC)


def run_session(client: Client, sid: str, cwd: str) -> int:
    """Исполнить сессию: инструкция с сервера -> opencode -> статус на сервер."""
    s = client.get_session(sid)
    if not s:
        print(f"[session_worker] сессия {sid} не найдена на сервере")
        return 4
    instr = s.get("instruction") or {}
    task_file = instr.get("task_file", "")
    prompt = instr.get("prompt", "")
    report = instr.get("report", "")
    log_path = instr.get("log", "")
    model = instr.get("model", "")
    skill = instr.get("skill", "")
    agent_role = instr.get("agent", "")
    task_id = s.get("task") or instr.get("task_id", "")
    if not prompt:
        print(f"[session_worker] сессия {sid} без промпта в инструкции")
        client.session_status(sid, "failed", error="инструкция без prompt")
        return 4

    # 1. зарегистрировать себя: pid/cmd -> kill/restart из панели
    cmdline = "python -X utf8 " + " ".join(f'"{a}"' if " " in a else a for a in sys.argv)
    s = client.session_start(sid, pid=os.getpid(), cmd=cmdline) or s
    print(f"[session_worker] сессия {sid} (задача {task_id}) запущена, pid={os.getpid()}")
    if task_file:
        print(f"  задача: {task_file}")

    # 2. opencode run — движок исполнения (промпт из инструкции сервера)
    cmd = _opencode_base() + ["run", prompt]
    if model:
        cmd += ["-m", model]
    if agent_role:
        cmd += ["--agent", agent_role]
    if task_file and Path(task_file).exists():
        cmd += ["-f", task_file]
    cmd += ["--auto"]

    stop_hb = threading.Event()
    threading.Thread(target=_hb_loop, args=(client, sid, stop_hb), daemon=True).start()

    proc = None
    abort = threading.Event()

    def on_event(ev):
        etype = ev.get("type", "")
        text = (ev.get("text") or "").strip().lower()
        if etype in ("session_instruction", "message") and ("abort" in text or "stop" in text):
            print(f"[session_worker] инструкция {sid}: {text[:80]} — прерываю")
            abort.set()
            if proc and proc.poll() is None:
                _kill_tree(proc.pid)

    # 3. SSE-подписка на канал сессии: контролёр может прервать
    sse_stop = threading.Event()
    threading.Thread(target=client.subscribe, args=(on_event, sse_stop),
                     daemon=True, name=f"sse-{sid}").start()
    try:
        proc = subprocess.Popen(cmd, cwd=cwd,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=no_window_flags())
        try:
            out, err = proc.communicate(timeout=SUBAGENT_TIMEOUT)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            try:
                out, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                out, err = "", ""
            rc = 124
    except Exception as e:
        out, err = "", f"ОШИБКА ЗАПУСКА opencode: {e}"
        rc = 3
    finally:
        stop_hb.set()
        sse_stop.set()

    if log_path:
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text((out or "") + "\n" + (err or ""), encoding="utf-8")
        except Exception:
            pass

    # 4. статус на сервер
    if rc == 0 and report and Path(report).exists():
        print(f"[session_worker] сессия {sid}: done, отчёт {report}")
        client.session_status(sid, "done", note="отчёт готов", report=report)
        return 0
    err_tail = (err or out or "")[-2000:]
    reason = (f"rc={rc}" + (", abort" if abort.is_set() else "")
              + (f"; {err_tail}" if err_tail else ""))[:2500]
    print(f"[session_worker] сессия {sid}: {reason}")
    client.session_status(sid, "failed", error=reason)
    return 1


def main():
    ap = argparse.ArgumentParser(prog="agents.session_worker")
    ap.add_argument("--session", required=True, help="id сессии (S-XXXX)")
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--project", default="")
    ap.add_argument("--cwd", default="")
    a = ap.parse_args()

    client = Client(f"session-{a.session}", project=a.project, base_url=a.url)
    if not client.server_alive(timeout=3.0):
        print(f"[session_worker] сервер {a.url} недоступен — фолбэк невозможен, rc=4")
        return 4
    cwd = a.cwd or str(Path.cwd())
    return run_session(client, a.session, cwd)


if __name__ == "__main__":
    sys.exit(main())
