# dev-pipeline — обобщённый конвейер задач для проектов (файлы+git+сервер)

Мультипроектный конвейер «запрос → задание → исполнитель → отчёт → проверка → вердикт»,
обобщённый из тестового запуска HeatLossRevit2 (`Tasks\Конвейер\pipeline.py`).

## Принципы

1. **Файлы + git = источник правды** по задачам (`Tasks\Активные\`, `Tasks\Отчёты\`,
   `Tasks\Архив\`). Сервер хранит только координацию: ленту событий и сообщения.
2. **Сервер (FastAPI + SQLite + SSE)** — вместо файловых сторожей: push-уведомления агентам,
   ACK, heartbeats, единая лента «кто что сделал», web-панель.
3. **Фолбэк на файлы**: сервер недоступен → агенты переключаются на файловые флаги
   в `Tasks\Конвейер\Уведомления\` (единый интерфейс `notify()/subscribe()`).
4. **Облачный движок** (Qwen/DeepSeek через LocalAssitent) — опция исполнителя для тяжёлых ТЗ.

## Структура

```
dev-pipeline\
├─ pipeline\             # общий Python-пакет (config, checks, cli, templates, client)
├─ server\               # FastAPI: /events /messages /heartbeat /tasks + dashboard
├─ agents\               # тонкие клиенты агентов (agent_watch, executor, browser)
├─ skills\               # общие ролевые скилы (pipeline-executor / -controller / -reviewer / -browser-bridge)
├─ examples\<project>\pipeline.yaml   # конфигурация проекта
└─ docs\                 # protocol.md, architecture.md, runbook.md, migration-<project>.md
```

## Подключение проекта

1. Создать `examples\<project>\pipeline.yaml` (см. `examples\heatlossrevit2\pipeline.yaml`).
2. Положить в проект папку `Tasks\` по протоколу (`docs\protocol.md`).
3. Запустить CLI: `python -m pipeline.cli <команда> <project>`.
4. Запустить сервер + агентов (шаг 3, в разработке).

## TDL (JSON — источник истины)

Иерархия «миссия → этап → класс → лист» (СДР до 4 уровней), только leaf-задачи
исполняются, закрытие — только при отчёте (evidence) + вердикте (pass).

```text
python -m pipeline.cli tdl-init      <project>            # каталоги + индекс
python -m pipeline.cli tdl-plan      <project> <spec.json> # построить иерархию миссии
python -m pipeline.cli tdl-dispatch  <project> <файл> --title ... --module ... --class-name ... --layer ...
python -m pipeline.cli tdl-start     <project> <A-NN>     # in_progress
python -m pipeline.cli tdl-report    <project> <A-NN> --from-md <отчёт>  # отчёт исполнителя
python -m pipeline.cli tdl-verify    <project> <A-NN>     # сборка+тесты → вердикт → done
python -m pipeline.cli tdl-tree      <project>            # дерево WBS
python -m pipeline.cli tdl-status    <project>            # статусы
python -m pipeline.cli tdl-validate  <project>            # проверка схемы
```

Спецификация для `tdl-plan` — JSON/YAML:

```json
{
  "mission": { "name": "Исправление HeatLossRevit2", "goal": "..." },
  "phases": [
    {
      "name": "Дефекты пользователя", "module": "MainAppHeatLoss",
      "packages": [
        { "name": "Комбобокс уровень", "class_name": "ViewModel", "layer": "ui",
          "tasks": ["текст листовой задачи", "..."] }
      ]
    }
  ]
}
```

## Статус

- [x] Шаг 1: каркас, общий пакет `pipeline/`
- [x] Шаг 2: конфиг HeatLossRevit2, перенос проверок в `pipeline/checks.py`
- [x] Шаг 3: сервер (FastAPI + SQLite + SSE, `/events`, `/messages`, `/heartbeat`, dashboard)
- [x] Шаг 4: клиенты агентов (`agents/`): agent_watch, executor_client, browser_client
- [x] Шаг 5: скилы (pipeline-executor/-controller/-reviewer/-browser-bridge), docs
- [x] TDL: JSON-конвейер (task/report/verdict), иерархия миссии (tdl-plan/tdl-tree), dashboard
- [x] Полный unit-тест: `python -X utf8 tests/run_all.py` (85 тестов: framework, CLI, server, client, TDL)
- [ ] Пилот на тестовой задаче через сервер
