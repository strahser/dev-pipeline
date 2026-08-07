# -*- coding: utf-8 -*-
"""TDL: генерация человекочитаемых Markdown-рендеров из JSON (вторичный слой)."""
from __future__ import annotations

import json


def _s(v) -> str:
    """Строковое представление значения: строки как есть, dict/list -> JSON."""
    if isinstance(v, str):
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else str(v)


def _wbs_depth(wbs: str) -> int:
    return len([x for x in str(wbs).split(".") if x])


def render_tree(tasks: list[dict]) -> str:
    """Отрисовать дерево TDL по WBS (миссия -> этапы -> классы -> листья).

    tasks — список словарей task (или index-строк) с полями wbs_code, name,
    is_summary, status, module, class_name, layer, task_id.
    """
    lines = ["# Дерево TDL", "", "```text"]
    for t in sorted(tasks, key=lambda x: [int(p) for p in str(x.get("wbs_code", "0")).split(".") if p.isdigit()]):
        wbs = t.get("wbs_code", "")
        depth = _wbs_depth(wbs)
        prefix = "  " * (depth - 1) + ("• " if not t.get("is_summary") else "- ")
        meta = []
        if t.get("module"):
            meta.append(t["module"])
        if t.get("class_name"):
            meta.append(t["class_name"])
        if t.get("layer"):
            meta.append(t["layer"])
        suffix = f"  [{_s(t.get('status', 'open'))}" + (f"/{_s(t.get('workflow_state'))}" if t.get("workflow_state") else "") + "]"
        suffix += f"  ({_s(t.get('task_id', ''))})"
        if meta:
            suffix += f"  {'.'.join(meta)}"
        lines.append(f"{prefix}{wbs} {_s(t.get('name', ''))}{suffix}")
    lines.append("```")
    return "\n".join(lines)


def _fmt_dur(sec) -> str:
    """Человекочитаемая длительность из секунд (или '' если нет)."""
    if not sec:
        return ""
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = []
    if d:
        parts.append(f"{d} дн")
    if h:
        parts.append(f"{h} ч")
    if m:
        parts.append(f"{m} мин")
    if not parts:
        parts.append(f"{sec} с")
    return " ".join(parts[:2])


def render_task_card(task: dict) -> str:
    lines = [f"# Карточка задачи {_s(task.get('wbs_code', ''))}", "",
             f"- ID: {_s(task.get('task_id'))}",
             f"- WBS: {_s(task.get('wbs_code'))}",
             f"- Проект: {_s((task.get('project') or {}).get('name', task.get('project', '')))}",
             f"- Статус: {_s(task.get('status_display', task.get('status')))}",
             f"- Workflow: {_s(task.get('workflow_state'))}",
             f"- Приоритет: {_s(task.get('priority'))}",
             f"- Тип: {_s(task.get('task_kind'))}",
             f"- Модуль: {_s(task.get('module', ''))}", "",
             "## Наименование", _s(task.get("name", "")), "",
             "## Цель", _s(task.get("goal", "")), "",
             "## Описание", _s(task.get("description", "")), "",
             "## Критерии приёмки"]
    for c in task.get("acceptance_criteria", []):
        lines.append(f"- {_s(c)}")
    dates = task.get("dates", {}) or {}
    est = _fmt_dur(dates.get("estimate_sec"))
    dur = _fmt_dur(dates.get("duration_sec"))
    lines += ["", "## Сроки",
              f"- Начало: {_s(dates.get('start', ''))}",
              f"- Завершение: {_s(dates.get('finish', ''))}"]
    if est:
        lines.append(f"- Оценка (план): {est}")
    if dur:
        lines.append(f"- Факт (длительность): {dur}")
    lines += ["", "## Ссылки"]
    for l in task.get("links", []):
        href = l.get("href") or l.get("url") or l.get("ref")
        lines.append(f"- [{l.get('type')}]({href})" if href else f"- {l.get('type')}")
    lines += ["", "## Блокировки", _s(task.get("blocker") or "Нет"), "", "## История"]
    for h in task.get("history", []):
        lines.append(f"- {_s(h.get('timestamp'))} {_s(h.get('action'))} — {_s(h.get('details'))}")
    return "\n".join(lines)


def render_report_md(report: dict) -> str:
    lines = [f"# ОТЧЁТ: {_s(report.get('report_id'))}", "",
             f"**Дата:** {_s(report.get('date'))} | **Статус:** {_s(report.get('report_status'))}", "",
             "## Что было не так", _s(report.get("problem", "")), "",
             "## Что сделано"]
    for w in report.get("work_done", []):
        lines.append(f"- {_s(w)}")
    lines += ["", "## Доказательства"]
    for e in report.get("evidence", []):
        cmd = _s(e.get("command") or e.get("details") or e.get("name"))
        lines.append(f"- [{_s(e.get('type'))}] {_s(e.get('evidence_id'))}: {cmd} (result={_s(e.get('result'))})")
    lines += ["", "## Числа до/после"]
    for m in report.get("metrics", []):
        lines.append(f"- {_s(m.get('name'))}: {_s(m.get('before'))} -> {_s(m.get('after'))}")
    lines += ["", "## Открытые вопросы"]
    for q in report.get("open_questions", []):
        lines.append(f"- {_s(q)}")
    lines += ["", "## Как пересобрать/проверить"]
    for c in report.get("verification_commands", []):
        lines.append(f"```\n{_s(c)}\n```")
    return "\n".join(lines)


def render_verdict_md(verdict: dict) -> str:
    lines = [f"# ВЕРДИКТ: {_s(verdict.get('verdict_id'))}", "",
             f"**Дата:** {_s(verdict.get('date'))} | **Результат:** {_s(verdict.get('result'))}",
             f"**Можно двигаться дальше:** {_s(verdict.get('can_move_forward'))}", "",
             "## Проверки", "",
             "| ID | Проверка | Статус | Ожидание | Факт |"]
    for c in verdict.get("checks", []):
        lines.append(f"| {_s(c.get('check_id'))} | {_s(c.get('name'))} | {_s(c.get('status'))} | "
                     f"{_s(c.get('expected'))} | {_s(c.get('actual'))} |")
    lines += ["", "## Обязательные исправления"]
    for f in verdict.get("required_fixes", []):
        lines.append(f"- {_s(f)}")
    lines += ["", "## Примечания"]
    for n in verdict.get("notes", []):
        lines.append(f"- {_s(n)}")
    return "\n".join(lines)
