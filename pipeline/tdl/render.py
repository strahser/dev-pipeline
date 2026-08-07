# -*- coding: utf-8 -*-
"""TDL: генерация человекочитаемых Markdown-рендеров из JSON (вторичный слой)."""
from __future__ import annotations


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
        suf = f"  [{t.get('status', 'open')}" + (f"/{t.get('workflow_state')}" if t.get("workflow_state") else "") + "]"
        suf += f"  ({t.get('task_id', '')})"
        if meta:
            suf += f"  {'.'.join(meta)}"
        lines.append(f"{prefix}{wbs} {t.get('name', '')}{suf}")
    lines.append("```")
    return "\n".join(lines)


def render_task_card(task: dict) -> str:
    lines = [f"# Карточка задачи {task.get('wbs_code', '')}", "",
             f"- ID: {task.get('task_id')}",
             f"- WBS: {task.get('wbs_code')}",
             f"- Проект: {(task.get('project') or {}).get('name', task.get('project', ''))}",
             f"- Статус: {task.get('status_display', task.get('status'))}",
             f"- Workflow: {task.get('workflow_state')}",
             f"- Приоритет: {task.get('priority')}",
             f"- Тип: {task.get('task_kind')}",
             f"- Модуль: {task.get('module', '')}", "",
             "## Наименование", task.get("name", ""), "",
             "## Цель", task.get("goal", ""), "",
             "## Описание", task.get("description", ""), "",
             "## Критерии приёмки"]
    for c in task.get("acceptance_criteria", []):
        lines.append(f"- {c}")
    lines += ["", "## Сроки",
              f"- Начало: {task.get('dates', {}).get('start', '')}",
              f"- Завершение: {task.get('dates', {}).get('finish', '')}", "",
              "## Ссылки"]
    for l in task.get("links", []):
        href = l.get("href") or l.get("url") or l.get("ref")
        lines.append(f"- [{l.get('type')}]({href})" if href else f"- {l.get('type')}")
    lines += ["", "## Блокировки", task.get("blocker") or "Нет", "", "## История"]
    for h in task.get("history", []):
        lines.append(f"- {h.get('timestamp')} {h.get('action')} — {h.get('details')}")
    return "\n".join(lines)


def render_report_md(report: dict) -> str:
    lines = [f"# ОТЧЁТ: {report.get('report_id')}", "",
             f"**Дата:** {report.get('date')} | **Статус:** {report.get('report_status')}", "",
             "## Что было не так", report.get("problem", ""), "",
             "## Что сделано"]
    for w in report.get("work_done", []):
        lines.append(f"- {w}")
    lines += ["", "## Доказательства"]
    for e in report.get("evidence", []):
        cmd = e.get("command") or e.get("details") or e.get("name")
        lines.append(f"- [{e.get('type')}] {e.get('evidence_id')}: {cmd} (result={e.get('result')})")
    lines += ["", "## Числа до/после"]
    for m in report.get("metrics", []):
        lines.append(f"- {m.get('name')}: {m.get('before')} -> {m.get('after')}")
    lines += ["", "## Открытые вопросы"]
    for q in report.get("open_questions", []):
        lines.append(f"- {q}")
    lines += ["", "## Как пересобрать/проверить"]
    for c in report.get("verification_commands", []):
        lines.append(f"```\n{c}\n```")
    return "\n".join(lines)


def render_verdict_md(verdict: dict) -> str:
    lines = [f"# ВЕРДИКТ: {verdict.get('verdict_id')}", "",
             f"**Дата:** {verdict.get('date')} | **Результат:** {verdict.get('result')}",
             f"**Можно двигаться дальше:** {verdict.get('can_move_forward')}", "",
             "## Проверки", "",
             "| ID | Проверка | Статус | Ожидание | Факт |"]
    for c in verdict.get("checks", []):
        lines.append(f"| {c.get('check_id')} | {c.get('name')} | {c.get('status')} | "
                     f"{c.get('expected')} | {c.get('actual')} |")
    lines += ["", "## Обязательные исправления"]
    for f in verdict.get("required_fixes", []):
        lines.append(f"- {f}")
    lines += ["", "## Примечания"]
    for n in verdict.get("notes", []):
        lines.append(f"- {n}")
    return "\n".join(lines)
