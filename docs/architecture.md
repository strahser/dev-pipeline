# Архитектура dev-pipeline

Версия: 1.0 (2026-08-06). Основано на `Архитектура_конвейера_v1.md` и
`Архитектура_конвейера_v2_Qwen.md` (HeatLossRevit2, `Tasks\00_Референсы\`).

## Решения

0. **v2 (2026-08-22): track table удалена.** Источник истины о работах — файл плана
   в ProjectsPalns (`pipeline/plans.py` читает карточки, пишет статусы). План-раннер
   (`agents/plan_runner.py`) ведёт цикл карточка → grill → субагент → вердикт → статус в плане.
   Сервер отдаёт планы/вопросы/чекпоинты через `server/plan_api.py` (`/api/plan/*`,
   `/api/questions`, `/api/checkpoints`, `/api/runner`).

1. **Файлы + git = источник правды** о задачах (v1 §5.2: «файлы как память»). Сервер НЕ
   дублирует стейт-машину задач в БД — только координацию.
2. **Сервер (FastAPI + SQLite + SSE)** вместо файловых сторожей (по v1 §3 и выбору пользователя):
   push-уведомления, ACK, heartbeats, единая лента, web-панель.
3. **Гибрид**: сервер недоступен → агенты фолбэкают на файловый поллинг
   (`Tasks\Конвейер\Уведомления\`). Единый интерфейс `notify()/subscribe()`.
4. **opencode run** — движок исполнения; сервер НЕ исполняет задачи.
5. **Облачный движок** (Qwen/DeepSeek через LocalAssitent) — опция исполнителя.

## Компоненты

```
dev-pipeline\
├─ pipeline\        # общий Python-пакет: config, models, templates, checks, cli, client
├─ server\          # FastAPI: /events /messages /heartbeat /tasks + dashboard
├─ agents\          # тонкие клиенты агентов (замена сторожей): agent_watch, executor, browser
├─ skills\          # общие ролевые скилы
├─ examples\<project>\pipeline.yaml
└─ docs\
```

## Обмен сообщениями агентов (SSE)

Полное описание каналов, формата события, ACK и фолбэка — в ответе разработчика;
схема ниже.

```
Агент-2 (executor)              FastAPI сервер                    Агент-1 (controller)
   GET /events/stream?agent=executor
   (долгое HTTP-соединение, push)
                                    <- POST /events {task_assigned A-10}
                                    <- запись в SQLite, публикация в SSE
   data: {type:task_assigned, task:A-10}
   POST /events/{id}/ack            (взял, доставлено)
   opencode run <A-10>
   POST /events {report_done}     -> data: {report_done}
                                   -> agent_watch -> verify A-10
                                    <- POST /events {verdict PASS}
   data: {verdict PASS}
```

### Явные сессии субагентов (общение через сервер)

Контролёр (agent_manager) НЕ запускает субагентов как слепые bash-субпроцессы
`opencode run <гигантский промпт>`. Вместо этого сессия — явный объект сервера:

```
контролёр                                сервер (sessions)              session_worker
  POST /api/sessions {instruction} ───►  session_created (SSE)
                                         ◄── GET /api/sessions/<id>  инструкция (JSON)
                                         ◄── POST .../start (pid/cmd)
  GET /api/sessions/<id> (поллинг) ◄──   (heartbeat 30 с) ──────────► opencode run (промпт из сессии)
  POST .../instruction "abort" ──────►   session_instruction → SSE session-<id>
  POST .../kill ────────────────────►    taskkill дерева
```

- `sessions` в БД: id, project, task, agent, role, model, skill, status
  (created/running/done/failed/killed/stalled), pid, cmd, instruction(JSON), note,
  report, error, created/started/finished/heartbeat.
- Инструкция (task_file, report, log, prompt, model, skill) — на сервере, worker читает её,
  а не получает из bash-аргументов. Статусы — через `POST /api/sessions/<id>/status`.
- Управление: instruction в SSE-канал `session-<id>` (abort/stop), kill — сервер убивает
  дерево процесса по pid. Watchdog: сессия без heartbeat > `PIPELINE_SESSION_MAX_AGE`
  (default 300 с) → `stalled` + событие `session_stalled`.
- Фолбэк: сервер недоступен → `agent_manager` переключается на legacy `opencode run`
  напрямую (флаг `--legacy`), файлы+git остаются источником истины.

### Каналы

| Канал | Слушатель | События |
|---|---|---|
| `executor` | Агент-2 | task_assigned, instruction, fix_request |
| `controller` | Агент-1 | report_done, agent_offline, blocker, question |
| `browser` | Агент-3 | browser_task |
| `feed` | dashboard, все | все события (лента) |

### Формат события (JSON)

```json
{
  "id": 123, "type": "task_assigned", "from": "controller", "to": "executor",
  "project": "HeatLossRevit2", "task": "A-10",
  "payload": {"path": "Tasks\\Активные\\A-10_*.md", "priority": "high"},
  "created_at": "2026-08-06T12:00:00", "delivery": "queued"
}
```

`delivery`: `queued → delivered → acked → handled` / `failed`.

### Устойчивость

- **ACK** — `POST /events/{id}/ack`; без ACK за N секунд событие возвращается в inbox адресата.
- **Recovery** — переподключившийся агент тянет `GET /messages?agent=<имя>&undelivered=true`.
- **Reconnect** — SSE с `Last-Event-ID` (пропущенное не теряется).
- **Heartbeat** — `POST /heartbeat {agent}` каждые 30 с; нет heartbeat > 90 с →
  событие `agent_offline` в канал контролёра (детектор зомби).
- **Фолбэк** — сервер недоступен → файловые флаги в `Tasks\Конвейер\Уведомления\`.

## Схема БД (server, conveyor.db)

Таблицы:
- `events` — id, type, from, to, project, payload(JSON), created_at, delivery.
- `messages` — id, from, to, text, created_at, delivery (inbox агентов).
- `agents` — name, last_seen (heartbeat), status (online/offline), project, pid, cmd.
- `sessions` — явные сессии субагентов (см. выше).

Файлы+git остаются источником правды о задачах; БД — только координация и лента.

## Проверки (pipeline/checks.py)

Декларативные, из `pipeline.yaml`:
- `build_grep` — проверка в логе сборки (SKIP, если лога нет).
- `grep_dir` — число .cs с pattern в каталоге == expect.
- `dir_exists` / `dir_exists_and_not` — наличие/удаление папок.
- `class_location` — класс определён только в указанных каталогах.
- `file_small` — файл существует и короче лимита строк.
- `csproj_no_ref` — проект не ссылается ProjectReference на другой (запрет циклов).
- `layer_rules` — grep-правила слоёв (применяются на каждой verify).

## Этапы

- [x] Шаг 1: каркас, общий пакет `pipeline/`.
- [x] Шаг 2: конфиг HeatLossRevit2, перенос проверок в `pipeline/checks.py`.
- [x] Шаг 3: сервер (SSE/сообщения/heartbeat) + dashboard.
- [x] Шаг 4: клиенты агентов (замена сторожей).
- [x] Шаг 5: скилы (в revit-skills), документация.
- [x] Шаг 6: пилот на тестовой задаче (MepTaggingSolution, 2026-08-06).
- [x] v2: план-раннер + grill-фаза + чекпоинты (см. Решения 0).
- [ ] Пилот план-раннера на реальной карточке (карточка 4.2 плана v2).
