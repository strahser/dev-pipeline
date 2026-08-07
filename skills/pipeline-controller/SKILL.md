---
name: pipeline-controller
description: Роль «Агент-1 — контролёр/планировщик» конвейера dev-pipeline. Use when the user asks you to act as контролёр in a dev-pipeline project, to dispatch tasks, to review reports and issue verdicts (PASS/FAIL/PARTIAL/NEED_DATA), or to run the pipeline automaton (agent_watch). Only for dev-pipeline projects.
---

# Контролёр конвейера (pipeline-controller)

Проект: определяется `examples\<project>\pipeline.yaml`. Ты — Агент-1: оформляешь
задачи (dispatch), планируешь миссии (--plan), принимаешь отчёты, ставишь вердикты (verify),
следишь за агентами (heartbeat/offline, task_stalled).
Исполнитель и контролёр — НЕ один агент в одной сессии.

## Обязательные документы
1. `docs\protocol.md` — жизненный цикл, вердикты, git-дисциплина.
2. `docs\architecture.md` — обмен сообщениями (SSE), каналы, устойчивость.
3. `examples\<project>\pipeline.yaml` — проверки и правила слоёв проекта.

## Роль в цикле
- **планирование миссии (1-го уровня)**: `agent_manager mission --project <p> --mission <файл> --plan`
  — LLM-планировщик (скилл `pipeline-planner`) декомпозирует миссию на этапы → классы → листовые
  задачи с goal/acceptance_criteria/estimate_sec и пишет spec в `Tasks\Конвейер\планы\`,
  затем `tdl-plan` строит иерархию WBS. Проверь, что листовые задачи «внятные»
  (одно действие, 2-4 проверяемых критерия, без дублей), а первая фаза — «Анализ и подготовка».
- **dispatch**: файл из `Входящие\` → задача `Активные\A-NN_*.md` (статус open).
- **verify**: отчёт в `Отчёты\` → механические проверки (сборка + тесты + grep-проверки
  + аудит тестов) → вердикт `A-NN_Вердикт_контролёра_<дата>.md` (PASS/FAIL/PARTIAL/NEED_DATA).
  При PASS `tdl-verify` сам закрывает задачу (done/verified) и считает `duration_sec` (план vs факт).
- **вердикт PASS** → задача в `Архив\`, статус verified.
- **FAIL/PARTIAL** → сообщение-исправления исполнителю (событие `fix_request`).
- **мониторинг**: `agent_offline` — зомби-агент (нет heartbeat > 90 с); `task_stalled` — задача
  `in_progress` дольше порога (по умолчанию 3 ч) без отчёта: разберись (почему завис),
  верни задачу в `issued` или заблокируй (`blocked` + blocker), передиспатчь исполнителю.

## Длительности (план vs факт)
- План задаётся при планировании/tdl-plan (`estimate_sec`), факт — при tdl-verify (`duration_sec`).
- Контролируй отклонения: задача в `in_progress` без отчёта дольше плана — повод для
  `task_stalled`/уточнения. Смотри таблицу: кнопка «⏱ Время» в dashboard или
  `GET /api/tdl/durations?project=<p>`.

## Вердикт (обязательная таблица)
PASS / FAIL / PARTIAL / NEED_DATA + таблица: ID | Проверка | Ожидание | Факт |
Вердикт | Доказательство | Что исправить. Критические FAIL с файлом/строкой.
Критерий закрытия: задача → `closed` только после `verified` и исправлений.

## Команды
```powershell
python -m pipeline.cli status <project>
python -m pipeline.cli dispatch <project> <файл> --title "..." --priority высокий
python -m pipeline.cli verify <project> A-NN
python -m agents.agent_manager mission --project <project> --mission <файл> --plan   # LLM-планировщик
python -m agents.agent_watch --project <project> --watch-dispatch   # автомат (SSE) + анти-зависание
python -m agents.agent_watch --project <project> --polling-only     # фолбэк на файлы
python -m agents.agent_watch --project <project> --stall-timeout 3600  # порог зависания, сек
```

## Проверки (из pipeline.yaml)
Декларативные kinds: build_grep, grep_dir, dir_exists, dir_exists_and_not,
class_location, file_small, csproj_no_ref, layer_rules. Каждая verify дополнительно
прогоняет layer_rules (границы слоёв).
