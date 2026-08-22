# -*- coding: utf-8 -*-
"""Модели конвейера: задача, отчёт, вердикт, событие, сообщение."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

STATUSES = {"open", "in_progress", "done_report", "verified", "rejected", "closed"}

# Статусы доставки события/сообщения (для сервера)
DELIVERY = {"queued", "delivered", "acked", "handled", "failed"}


@dataclass
class Task:
    id: str
    file: Path
    status: str = "open"
    priority: str = "средний"
    meta: dict = field(default_factory=dict)

    @staticmethod
    def parse_frontmatter(text: str) -> dict:
        m = re.search(r"^---(.*?)^---$", text, re.S | re.M)
        meta: dict = {}
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
        return meta

    @classmethod
    def from_file(cls, path: Path) -> "Task":
        text = path.read_text(encoding="utf-8")
        meta = cls.parse_frontmatter(text)
        tid = meta.get("id") or path.name.split("_")[0]
        return cls(id=tid, file=path, status=meta.get("статус", "open"),
                   priority=meta.get("приоритет", "средний"), meta=meta)

    def set_status(self, new_status: str) -> None:
        if new_status not in STATUSES:
            raise ValueError(f"Неверный статус: {new_status}")
        text = self.file.read_text(encoding="utf-8")
        text = re.sub(r"статус:\s*\S+", f"статус: {new_status}", text, count=1)
        self.file.write_text(text, encoding="utf-8")
        self.status = new_status


@dataclass
class Event:
    """Событие для SSE/ленты. Сервер хранит только координацию."""
    id: int | None
    type: str
    from_: str
    to: str
    project: str
    payload: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    delivery: str = "queued"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "from": self.from_,
            "to": self.to,
            "project": self.project,
            "payload": self.payload,
            "created_at": self.created_at,
            "delivery": self.delivery,
        }


@dataclass
class Message:
    """Сообщение между агентами (аналог Уведомления\\*.txt, но через сервер)."""
    id: int | None
    from_: str
    to: str
    text: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    delivery: str = "queued"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "from": self.from_, "to": self.to,
            "text": self.text, "created_at": self.created_at, "delivery": self.delivery,
        }


def parse_tests_vstest(out: str) -> tuple:
    """Робастный парсинг vstest: берёт ПОСЛЕДНЕЕ вхождение каждой метрики.
    Возвращает (passed, total, failed)."""
    def last_num(*patterns):
        for p in patterns:
            ms = re.findall(rf"{p}\s*[.:]?\s*(\d+)", out)
            if ms:
                return int(ms[-1])
        return None

    passed = last_num(r"Пройдено", r"Passed")
    total = last_num(r"Всего тестов", r"Total tests", r"Total", r"Всего", r"Итого")
    failed = last_num(r"Не пройдено", r"Failed", r"Неуспешно")
    return passed, total, failed


def failed_test_names_vstest(out: str) -> list:
    """Имена не пройденных тестов из вывода vstest (рус/англ):
    'Не пройден Name [123 ms]' / 'Failed Name [123 ms]'.
    Имя может быть как 'Tests.Foo.Bar_Method', так и коротким 'Bar_Method'."""
    names = []
    for line in out.splitlines():
        m = re.search(r"(?:Не пройден|Failed)\s+([A-Za-z0-9_\.]+(?:\.[A-Za-z0-9_\.]+)*)", line)
        if m:
            names.append(m.group(1))
    return names


def parse_tests_dotnet(out: str) -> tuple:
    """Парсинг `dotnet test` (NUnit/xUnit):
    'не пройдено 7, пройдено 8, пропущено 0, всего 15' -> (8, 15, 7).
    Возвращает (passed, total, failed)."""
    def last_num(*patterns):
        for p in patterns:
            ms = re.findall(rf"{p}\s*:?\s*(\d+)", out)
            if ms:
                return int(ms[-1])
        return None

    passed = last_num(r"[Пп]ройдено", r"Passed")
    total = last_num(r"[Вв]сего", r"Total", r"Total tests")
    failed = last_num(r"[Нн]е пройдено", r"Failed", r"[Нн]еуспешно")
    return passed, total, failed


def parse_tests_pytest(out: str) -> tuple:
    """Парсинг `python -m pytest -q`: '3 failed, 26 passed, 2 skipped in 1.2s'.
    Возвращает (passed, total, failed); failed=0 при отсутствии совпадения."""
    def num(pattern: str):
        m = re.search(rf"(\d+)\s+{pattern}\b", out)
        return int(m.group(1)) if m else 0

    passed = num("passed")
    failed = num("failed") + num("error")
    skipped = num("skipped") + num("xfailed") + num("xpassed")
    total = passed + failed + skipped
    if total == 0:
        return None, None, None
    return passed, total, failed
