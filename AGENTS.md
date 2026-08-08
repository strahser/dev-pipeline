# AGENTS.md — контекст и конвенции dev-pipeline

Репозиторий-фреймворк конвейера задач для проектов в `E:\ПлагиныРевит\`.

## Ключевые принципы

- **Файлы + git = источник правды** по задачам и артефактам. Сервер — только координация
  (лента событий, сообщения, heartbeat, панель). НЕ дублировать стейт-машину задач в БД.
- **Сервер (FastAPI + SQLite + SSE)** — push-уведомления агентам вместо поллинга.
- **Фолбэк на файлы**: сервер недоступен → файловые флаги `Tasks\Конвейер\Уведомления\`.
- **Агенты не общаются в чате** — только события сервера + файлы+git.

## Стек

- Python 3.13 (Windows), PyYAML, позже FastAPI/uvicorn/sse-starlette (уже установлены).
- `opencode run` — движок исполнения задач (сервер НЕ исполняет).

## Команды

- Список проектов: `python -m pipeline.cli list`
- Статус проекта: `python -m pipeline.cli status <project>`
- Диспатч: `python -m pipeline.cli dispatch <project> <файл> [--title ...] [--priority ...]`
- Верификация: `python -m pipeline.cli verify <project> <A-NN>`
- TDL: `python -m pipeline.cli tdl-status|tdl-tree|tdl-plan|tdl-verify <project> [A-NN]`
- Summary-закрытие: tdl-verify сам закрывает summary-предков при всех done-потомках;
  принудительно — `tdl-close-summaries <project>`
- Длительности: `python -m pipeline.cli tdl-verify` ставит `duration_sec`; оценки — `estimate_sec`
  в spec при `tdl-plan` (число ≤24 = часы; строки «2ч 30м», «3.5h», «45м», «1д»)
- API длительностей: `GET /api/tdl/durations?project=<p>` (план vs факт, summary-суммирование)
- Планировщик миссии (LLM-декомпозиция 1-го уровня): `python -m agents.agent_manager mission --project <p> --mission <файл> --plan` — пишет spec в `Tasks\Конвейер\планы\` и вызывает tdl-plan; фолбэк на `split_mission`
- **Явные сессии субагентов** (по умолчанию): `agent_manager task/mission` создаёт сессию на сервере
  (`POST /api/sessions`, инструкция JSON) и запускает тонкого `agents/session_worker.py` — он читает
  инструкцию С СЕРВЕРА, исполняет через opencode run, шлёт heartbeat/статусы через сервер
  (`/api/sessions/{id}/status`), контролёр мониторит по API и может прервать
  (`POST /api/sessions/{id}/instruction` → SSE-канал `session-<id>`, или kill). ВАЖНО: НЕ запускать
  субагентов напрямую bash-`opencode run` — только через сессию; `--legacy` — фолбэк без сервера
- API сессий: `POST /api/sessions` (создать), `GET /api/sessions` (список), `GET /api/sessions/{id}`,
  `POST /api/sessions/{id}/start|status|heartbeat|kill|instruction`; лента событий
  `session_created/session_started/session_status/session_stalled`
- Агенты-помощники по ролям: `POST /api/agents {role, project, model?, task?}` — сессия с
  предзагруженным скиллом роли (методика+роль): controller/executor/browser/reviewer/qwen/planner;
  в панели «🗂 Сессии» — кнопка «➕ Новый агент»; kill live opencode-сессий — `POST /api/sessions/live/{sid}/kill`
- Сырые задания пользователя: `POST /api/requests {project, text}` — БД (`requests`) + файл
  `Tasks\Входящие\` + git-коммит `inbox: ...`; `POST /api/requests/{id}/dispatch` — оформить
  в задачу (файл в Активные + коммит); панель «📥 Входящие»
- Анти-зависание: сервер — `PIPELINE_WATCH_INTERVAL`/`PIPELINE_WATCH_MAX_AGE`,
  сессии — `PIPELINE_SESSION_MAX_AGE` (default 300 с, heartbeat сессии 30 с; stale → `stalled` + событие);
  сторож — `python -m agents.agent_watch --project <p> [--stall-timeout N]` (env `TASK_STALL_TIMEOUT_SEC`, default 10800);
  субагенты — таймаут 1800 с + PID-файл `logs\<A-NN>.pid` (env `SUBAGENT_MAX_AGE_SEC` для убийства сирот сторожем);
  фейковый отчёт при rc≠0 не создаётся — пометка `task_stalled` (редиспатч)

## Правила

- Не коммитить: `.idea\`, `.opencode\`, `bin\obj`, `TestResults\`, `__pycache__\`, `*.db`, логи.
- Коммиты: `pipeline: ...` (каркас), `project/<имя>: ...` (конфиги примеров).
- Новый проект = новый `examples\<project>\pipeline.yaml` + проверка `list`/`status`.
- Пути проекта: `project.root` — строка ИЛИ список кандидатов (первый существующий выбирается
  автоматически), либо переменная `DEV_PIPELINE_PROJECTS_DIR` (базовая папка проектов на ПК).
- Скиллы агентов: `pipeline-planner` (декомпозиция миссии), `pipeline-executor`, `pipeline-controller`
  (включая реакцию на `task_stalled`), `pipeline-reviewer`, `pipeline-browser-bridge`, `pipeline-qwen-worker`.

## База знаний проекта (revit-skills)

- Общая база знаний по Revit-плагинам — репозиторий `strahser/revit-skills` (wiki + общие скилы),
  смотри скилл `knowledge-base`. Ссылка: `references.revit-skills` в `opencode.json`.
- Локально: `D:\Projects\revit-skills\` (рабочий ПК: `E:\ПлагиныРевит\revit-skills\`), ветка `main`.
- Wiki: `.opencode\wiki\` — архитектура, форматы, паттерны, MCP; читай `index.md` перед Revit-задачами.
- Общие скилы (не привязаны к агентам конвейера): `.opencode\skills\` — revit-api, revit-testing,
  revit-3d-export, threejs-viewer, cloud-ai-bridge и др. — справочник паттернов.
- **Правила пополнения** (обязательны для агентов): новое стабильное знание → запись в wiki
  revit-skills (не только в отчёт по задаче); коммиты `docs:` / `agent/A-NN: wiki: ...` делает
  контролёр; ссылки относительные; `index.md` обновлять при добавлении страницы.
- Не дублировать: dev-pipeline — задачи/отчёты/вердикты, revit-skills — знания.
