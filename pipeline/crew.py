# -*- coding: utf-8 -*-
"""Crew проекта: поднятие автономных сессий и handoff-цикл (карточка 6.2).

Секция pipeline.yaml:
    crew:
      roles: [executor]        # роли из AGENT_ROLES сервера
      model: ""                # пусто = дефолт сервера
      permissions: write       # профиль opencode: read | write
    restart_policy:
      max_restarts: 3          # рестартов НА ЦЕПОЧКУ порций одной работы
      cooldown_sec: 300        # пауза между рестартами цепочки

Команды:
    python -m pipeline.cli up <project>         # поднять crew один раз
    python -m pipeline.cli supervise <project>  # цикл: порция -> handoff -> рестарт

Правило «раннер не мешает процессу»: supervisor перезапускает только сессии,
которые сами попросили продолжения (done c заметкой handoff:<путь>) либо
упали (failed/stalled); исчерпание бюджета — событие crew_exhausted.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HANDOFF_MARK = "handoff:"
RESTARTS_KEY = "_restarts"      # счётчик рестартов цепочки в instruction сессии
RESTART_TS_KEY = "_restart_ts"  # время последнего рестарта цепочки


def load_crew(cfg) -> dict:
    """Нормализованный crew из конфига проекта."""
    return {
        "roles": list(cfg.crew_roles or ["executor"]),
        "model": str(cfg.crew_model or ""),
        "permissions": str(cfg.crew_permissions or "write"),
        "policy": {"max_restarts": int(cfg.restart_max),
                   "cooldown_sec": int(cfg.restart_cooldown_sec)},
    }


def permissions_template(mode: str) -> dict:
    """Профиль прав opencode для .opencode/permissions.json."""
    base = {"edit": "deny", "bash": {"*": "deny"}, "webfetch": "allow"}
    if mode == "write":
        base["edit"] = "allow"
        base["bash"] = {"*": "allow", "rm -rf *": "deny"}
    return {"permissions": base}


def ensure_permissions(cfg) -> Path | None:
    """Создать шаблон прав при первом up; существующий не трогаем."""
    target = cfg.root / ".opencode" / "permissions.json"
    if target.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(permissions_template(cfg.crew_permissions),
                                 ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return target


def up_project(cfg, client) -> list[dict]:
    """Поднять crew: по сессии на роль через POST /api/agents."""
    if client is None:
        print("[crew] сервер недоступен — поднятие невозможно")
        return []
    crew = load_crew(cfg)
    out = []
    for role in crew["roles"]:
        s = client.create_agent(role, project=cfg.name, model=crew["model"])
        ok = bool(s)
        print(f"[crew] роль {role}: {'OK ' + s.get('id', '') if ok else 'ОТКАЗ'}")
        out.append({"role": role, "session": s, "ok": ok})
    return out


def _chain_state(s: dict, counters: dict) -> dict:
    """Состояние ЦЕПОЧКИ рестартов для сессии s (карточка 1.2).

    Счётчик и время последнего рестарта едут с сессией в instruction
    (_restarts/_restart_ts): каждый рестарт создаёт НОВЫЙ sid, поэтому
    учёт по sid давал свежий счётчик каждому поколению и лимит был
    недостижим. Родословная сильнее локального кэша counters (тот мог
    потеряться при перезапуске супервизора); counters[sid] синхронизируется
    и служит кэшем + местом для флагов (handled)."""
    instr = s.get("instruction") or {}
    cnt = counters.setdefault(s.get("id", ""), {"count": 0, "last_ts": 0.0})
    if RESTARTS_KEY in instr:
        try:
            cnt["count"] = int(instr[RESTARTS_KEY])
        except (TypeError, ValueError):
            pass
    if RESTART_TS_KEY in instr:
        try:
            cnt["last_ts"] = float(instr[RESTART_TS_KEY])
        except (TypeError, ValueError):
            pass
    return cnt


def plan_restarts(sessions: list[dict], policy: dict, counters: dict,
                  now: float | None = None) -> list[dict]:
    """Решения о перезапуске. sessions: id/status/note/task/role/model/instruction.

    - done только с заметкой handoff:<путь> (порция просит продолжения);
    - failed/stalled — свежая попытка;
    - бюджет max_restarts считается ПО ЦЕПОЧКЕ поколений одной работы
      (счётчик наследуется через instruction._restarts), а не по отдельному sid;
    - cooldown от последнего рестарта цепочки;
    - исчерпан бюджет цепочки -> exhausted; сессия, уже породившая преемника
      или объявленная exhausted (флаг handled), больше не рассматривается."""
    now = time.time() if now is None else now
    max_n = int(policy.get("max_restarts", 3))
    cooldown = int(policy.get("cooldown_sec", 300))
    out = []
    for s in sessions:
        sid = s.get("id", "")
        status = s.get("status", "")
        note = str(s.get("note", "") or "")
        if status == "done" and not note.startswith(HANDOFF_MARK):
            continue
        if status not in ("done", "failed", "stalled"):
            continue
        cnt = _chain_state(s, counters)
        if cnt.get("handled"):
            continue
        if int(cnt["count"]) >= max_n:
            out.append({"sid": sid, "action": "exhausted",
                        "task": s.get("task", ""), "reason": "max_restarts"})
            continue
        if now - float(cnt["last_ts"]) < cooldown and int(cnt["count"]) > 0:
            out.append({"sid": sid, "action": "cooldown",
                        "task": s.get("task", ""), "reason": "cooldown_sec"})
            continue
        out.append({"sid": sid, "action": "restart",
                    "task": s.get("task", ""),
                    "handoff": note[len(HANDOFF_MARK):].strip()
                    if note.startswith(HANDOFF_MARK) else "",
                    "role": s.get("role", ""), "model": s.get("model", ""),
                    "instruction": s.get("instruction") or {}})
    return out


def _read_handoff_prompt(root: Path, handoff_rel: str, instruction: dict) -> str:
    """Промпт новой сессии: исходная инструкция + содержимое handoff-файла."""
    prompt = str(instruction.get("prompt", ""))
    body = ""
    if handoff_rel:
        for cand in (root / handoff_rel, Path(handoff_rel)):
            try:
                if cand.is_file():
                    body = cand.read_text(encoding="utf-8", errors="replace")
                    break
            except OSError:
                continue
    tail = ("\n\nHANDOFF ПРЕДЫДУЩЕЙ СЕССИИ (продолжи с этого места):\n"
            + body[-6000:]) if body else ""
    return prompt + tail


def supervise_once(cfg, client, counters, *, now: float | None = None,
                   spawn=None, notify=None) -> list[dict]:
    """Один проход супервизора: решения + поднятие новых сессий."""
    if client is None:
        return []
    crew = load_crew(cfg)
    policy = crew["policy"]
    decisions = plan_restarts(client.list_sessions(project=cfg.name),
                              policy, counters, now=now)
    summary = []
    for d in decisions:
        if d["action"] != "restart":
            if d["action"] == "exhausted" and client is not None:
                cnt = counters.setdefault(d["sid"], {"count": 0, "last_ts": 0.0})
                if not cnt.get("handled"):        # один раз на цепочку
                    cnt["handled"] = True
                    try:
                        client.notify("crew_exhausted", to="feed",
                                      task=d.get("task", ""),
                                      payload={"session_id": d["sid"],
                                               "reason": d.get("reason", "")})
                    except Exception:
                        pass
            summary.append(d)
            continue
        s = client.get_session(d["sid"]) or {}
        instr = s.get("instruction") or {}
        prompt = _read_handoff_prompt(Path(cfg.root), d.get("handoff", ""), instr)
        ts = time.time() if now is None else now
        cnt = counters[d["sid"]]
        new_count = int(cnt.get("count", 0)) + 1  # бюджет едет с сессией
        new_instr = {**instr, "prompt": prompt, "continues": True,
                     RESTARTS_KEY: new_count, RESTART_TS_KEY: ts}
        new_id = client.create_session(
            project=cfg.name, task=d["task"],
            agent=f"crew-{s.get('role') or 'worker'}-{d['sid'][-4:]}",
            role=s.get("role") or "worker", model=s.get("model", ""),
            skill=instr.get("skill", ""),
            instruction=new_instr) or {}
        new_sid = new_id.get("id", "")
        cnt["count"] = new_count
        cnt["last_ts"] = ts
        if new_sid:
            # преемник создан: это поколение больше решений не порождает,
            # иначе дубли порций на каждом проходе (шторм сессий)
            cnt["handled"] = True
            counters[new_sid] = {"count": new_count, "last_ts": ts}
        if spawn is not None:
            spawn({"id": new_sid, "task": d["task"], "instruction": new_instr})
        summary.append({**d, "new_sid": new_sid})
    return summary


if __name__ == "__main__":
    print("модуль pipeline.crew: используйте python -m pipeline.cli "
          "up|supervise <project>")
