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

## Статус

- [x] Шаг 1: каркас, общий пакет `pipeline/`
- [x] Шаг 2: конфиг HeatLossRevit2, перенос проверок в `pipeline/checks.py`
- [ ] Шаг 3: сервер (SSE/сообщения/heartbeat) + dashboard
- [ ] Шаг 4: клиенты агентов (замена сторожей)
- [ ] Шаг 5: скилы, документация
- [ ] Шаг 6: пилот на тестовой задаче
