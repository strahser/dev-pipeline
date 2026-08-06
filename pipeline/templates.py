# -*- coding: utf-8 -*-
"""Шаблоны задач, отчётов и вердиктов (обобщены из pipeline.py HeatLossRevit2)."""
from __future__ import annotations

from datetime import datetime

TASK_TEMPLATE = """---
id: {task_id}
приоритет: {priority}
статус: open
постановщик: контролёр (автоконвейер)
исполнитель: {executor}
дата: {date}
источник_запроса: {source}
замечание: {remark}
---

# ЗАДАЧА: {title}

## Контекст (зачем, что уже известно)
{context}

## Требования (критерии приёмки)
{requirements}

## Границы (что НЕ делать)
- Не менять архитектуру сверх задачи; не выносить кодовую базу за пределы замечания.
- Не коммитить: .idea\\.opencode\\, bin\\obj, TestResults\\.
- Не создавать сокеты/сервисы на портах (только файлы и git).
- Перед переносом папки: git mv (сохранить историю), затем убрать из старого csproj.
- Отчёт — в Tasks\\Отчёты\\{task_id}_Отчёт_<дата>.md по шаблону 00_Протокол_агентов.md; коммит с префиксом agent/{task_id}.

## Результат (куда положить артефакты)
{result}

## Ход работы (заполняет исполнитель)
- (задача выдана {date})
"""

REPORT_SECTIONS = ["Что сделано", "Доказательства", "Открытые вопросы"]

VERDICT_TEMPLATE = """# ВЕРДИКТ КОНТРОЛЁРА: {task_id} {title}

**Дата:** {date} | **Задача:** Tasks\\Активные\\{task_file} | **Отчёт исполнителя:** {report}

## 1. Общий вердикт
**{verdict}** (отправлять дальше: {sendable}) — уровень уверенности {confidence}

## 2. Механические проверки (автоконвейер)

| Проверка | Результат |
|---|---|
{checks}

## 3. Доказательства исполнителя
{evidence_status}

## 4. Обязательные исправления перед закрытием
{fixes}

## 5. Примечания контролёра
{notes}
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def task_content(task_id: str, title: str, priority: str, source: str,
                 remark: str, body: str, executor: str = "worker",
                 requirements: str | None = None, result: str | None = None) -> str:
    body = body.strip() or "(пусто)"
    return TASK_TEMPLATE.format(
        task_id=task_id, priority=priority, executor=executor,
        date=now(), source=source, remark=remark or "—",
        title=title, context=body[:3000],
        requirements=(requirements or body)[:3000],
        result=result or "Артефакты — в репозитории; отчёт — в Tasks\\Отчёты\\.",
    )


def verdict_content(task_id: str, title: str, task_file: str, report: str,
                    verdict: str, checks_rows: str, confidence: str = "Medium",
                    evidence_status: str = "—", fixes: str = "—", notes: str = "") -> str:
    sendable = "нет" if verdict == "FAIL" else ("да (с оговорками)" if verdict == "PARTIAL" else "да")
    return VERDICT_TEMPLATE.format(
        task_id=task_id, title=title, date=now(), task_file=task_file,
        report=report, verdict=verdict, sendable=sendable, confidence=confidence,
        checks=checks_rows, evidence_status=evidence_status, fixes=fixes, notes=notes,
    )
