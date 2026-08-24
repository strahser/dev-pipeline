# -*- coding: utf-8 -*-
"""Контракт пауз конвейера: видимые ожидания решения по карточке.

Любой агент (не только план-раннер) перед паузой обязан сделать её видимой:

    python -X utf8 agents/checkpoint.py create <project> <CARD> --reason "..."
    python -X utf8 agents/checkpoint.py wait   <project> <CARD> [--timeout N]

create пишет Tasks\\Конвейер\\checkpoints\\<CARD>.pending.json + событие
checkpoint_pending в ленту — панель («⏸ Чекпоинты») показывает ожидание.
wait блокируется до появления <CARD>.decision.json (кнопки панели
«✅ Одобрить / 🔄 Перезапустить»):
    rc=0  — решение получено (approve/retry напечатано);
    rc=1  — таймаут;
    rc=2  — проект/конфиг не найден.

Межагентная передача (карточка в плане адресована другому агенту):
    python -X utf8 agents/checkpoint.py handoff <project> <CARD> --to <agent> --text "..."
— сообщение адресату + событие task_handoff в ленту; владелец не курьер.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_POLL_SEC = 5


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def cp_dir(cfg) -> Path:
    return cfg.conveyor_dir() / "checkpoints"


def _emit(notify, ev_type: str, task: str, payload: dict, client=None) -> None:
    if notify is not None:
        notify(ev_type, task=task, payload=payload)
        return
    print(f"[checkpoint] {ev_type} {task} {payload}".rstrip())
    if client is not None:
        try:
            client.notify(ev_type, to="feed", task=task, payload=payload)
        except Exception:
            pass


def create_pending(cfg, card_id: str, reason: str = "", title: str = "",
                   client=None, notify=None) -> Path:
    """Объявить ожидание решения: pending.json + событие checkpoint_pending."""
    d = cp_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{card_id}.pending.json"
    p.write_text(json.dumps({"card": card_id, "title": title, "reason": reason,
                             "created": _now_iso()},
                            ensure_ascii=False, indent=2),
                 encoding="utf-8")
    _emit(notify, "checkpoint_pending", card_id,
          {"reason": reason, "checkpoint": card_id}, client)
    return p


def take_decision(cfg, card_id: str):
    """(action, comment, actor) из decision.json с удалением файла; None — решения нет."""
    p = cp_dir(cfg) / f"{card_id}.decision.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    action = "retry" if data.get("decision") == "retry" else "approve"
    comment = str(data.get("comment", ""))
    actor = str(data.get("actor", "")).strip() or "owner"
    p.unlink(missing_ok=True)
    return action, comment, actor


class _RealClock:
    def time(self) -> float:
        return time.time()

    def sleep(self, sec: float) -> None:
        time.sleep(sec)


def wait_decision(cfg, card_id: str, *, poll_sec: int = DEFAULT_POLL_SEC,
                  remind_sec: int | None = None, timeout: int | None = None,
                  client=None, notify=None, clock=None):
    """Ждёт решение ('approve'/'retry') или None при таймауте.

    Каждые remind_sec секунд ожидания уходит напоминание checkpoint_waiting
    с временем ожидания; автопродолжения нет — только файл решения."""
    clock = clock or _RealClock()
    started = clock.time()
    last_remind = started
    deadline = (started + timeout) if timeout else None
    while True:
        got = take_decision(cfg, card_id)
        if got is not None:
            action, comment, actor = got
            _emit(notify, "checkpoint_decided", card_id,
                  {"action": action, "comment": comment[:200], "actor": actor},
                  client)
            return action
        now = clock.time()
        if deadline is not None and now >= deadline:
            return None
        if remind_sec and now - last_remind >= remind_sec:
            _emit(notify, "checkpoint_waiting", card_id,
                  {"waiting_sec": int(now - started), "remind_sec": remind_sec},
                  client)
            last_remind = now
        clock.sleep(max(1, poll_sec))


def handoff(cfg, card_id: str, to: str, text: str, client=None, notify=None) -> bool:
    """Карточка адресована другому агенту: сообщение адресату + task_handoff."""
    delivered = False
    if client is not None:
        try:
            msg = client.send_message(to, text)
            delivered = bool(msg)
        except Exception:
            delivered = False
    _emit(notify, "task_handoff", card_id, {"to": to, "text": text[:200]}, client)
    return delivered


def main() -> int:
    ap = argparse.ArgumentParser(prog="checkpoint",
                                 description="Видимые паузы конвейера")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s_create = sub.add_parser("create", help="объявить ожидание решения")
    s_create.add_argument("project")
    s_create.add_argument("card")
    s_create.add_argument("--reason", default="")
    s_create.add_argument("--title", default="")

    s_wait = sub.add_parser("wait", help="ждать решение владельца/ревьюера")
    s_wait.add_argument("project")
    s_wait.add_argument("card")
    s_wait.add_argument("--timeout", type=int, default=0,
                        help="сек; 0 = ждать вечно")
    s_wait.add_argument("--remind", type=int, default=600,
                        help="напоминание каждые N сек")

    s_hand = sub.add_parser("handoff", help="передать карточку другому агенту")
    s_hand.add_argument("project")
    s_hand.add_argument("card")
    s_hand.add_argument("--to", required=True, help="имя агента-адресата")
    s_hand.add_argument("--text", required=True)

    a = ap.parse_args()

    from pipeline.config import ConfigError, load_config
    try:
        cfg = load_config(a.project)
    except ConfigError as e:
        print(f"[checkpoint] проект не найден: {e}")
        return 2

    client = None
    try:
        from pipeline.client import Client
        c = Client("checkpoint", project=cfg.name)
        if c.server_alive():
            client = c
    except Exception:
        client = None

    def _print_notify(ev_type, task="", payload=None):
        print(f"[checkpoint] {ev_type} {task} {payload or ''}".rstrip())

    if a.cmd == "create":
        p = create_pending(cfg, a.card, a.reason, a.title, client=client,
                           notify=_print_notify)
        print(f"[checkpoint] ожидание объявлено: {p}")
        return 0
    if a.cmd == "wait":
        action = wait_decision(cfg, a.card, poll_sec=DEFAULT_POLL_SEC,
                               remind_sec=(a.remind or None),
                               timeout=(a.timeout or None),
                               client=client, notify=_print_notify)
        if action is None:
            print("[checkpoint] таймаут ожидания решения")
            return 1
        print(f"[checkpoint] решение: {action}")
        return 0

    if client is None:
        print("[checkpoint] сервер недоступен — передать сообщение некому")
        return 1
    ok = handoff(cfg, a.card, a.to, a.text, client=client, notify=_print_notify)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
