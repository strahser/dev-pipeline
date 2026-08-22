# -*- coding: utf-8 -*-
"""Ожидание ответа пользователя на вопрос (grill-фаза).

Субагент создаёт файл вопроса Tasks\\Вопросы\\<CARD>_<время>.md с пустой секцией
«## Ответы» и вызывает:

    python -X utf8 agents/wait_answer.py "<файл>" --timeout 1200 [--poll 5]

Скрипт БЛОКИРУЕТСЯ до появления непустого раздела «## Ответы» или таймаута:
    rc=0  — ответ получен (можно продолжать);
    rc=1  — таймаут: продолжать по допущениям (помечать ASSUMPTION в отчёте);
    rc=2  — файл не найден / ошибка запуска.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ANSWERS_MARK = "## Ответы"


def has_answer(text: str) -> bool:
    if ANSWERS_MARK not in text:
        return False
    tail = text.split(ANSWERS_MARK, 1)[1].strip()
    return bool(tail)


def main() -> int:
    ap = argparse.ArgumentParser(prog="wait_answer")
    ap.add_argument("file", help="путь к файлу вопроса (.md)")
    ap.add_argument("--timeout", type=int, default=1200,
                    help="максимальное ожидание, сек (default 1200 = 20 мин)")
    ap.add_argument("--poll", type=int, default=5, help="интервал опроса, сек")
    a = ap.parse_args()

    p = Path(a.file)
    if not p.exists():
        print(f"[wait_answer] файл не найден: {p}")
        return 2

    deadline = time.time() + max(30, a.timeout)
    print(f"[wait_answer] жду ответ в {p.name} (до {int((deadline - time.time()) // 60)} мин)…",
          flush=True)
    while time.time() < deadline:
        try:
            if has_answer(p.read_text(encoding="utf-8", errors="replace")):
                print("[wait_answer] ответ получен — продолжаю")
                return 0
        except OSError:
            pass
        time.sleep(max(2, a.poll))
    print("[wait_answer] таймаут ожидания — работаю по допущениям (ASSUMPTION)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
