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

## План-раннер (план ProjectsPalns — источник истины)

Track table (TDL) удалена. План-раннер исполняет карточки плана из репозитория
ProjectsPalns: выбор готовой карточки (статус `Открыто`, зависимости закрыты) →
MD-постановка → grill-фаза → субагент → механический вердикт → статус `Выполнено`
прямо в файле плана + коммит.

```bat
python -X utf8 -m agents.plan_runner --project <p> [--once] [--plan <файл>]
                     [--retries N] [--model <модель>] [--legacy] [--dry-run]
```

- **Grill-фаза**: субагент сначала изучает код/вики; блокирующие вопросы владельцу —
  через файл `Tasks\Вопросы\<CARD>_*.md` + `agents/wait_answer.py` (панель → «❓ Вопросы»).
  Тишина > `runner.question_timeout_sec` → работа по допущениям (ASSUMPTION в отчёте).
- **Верификация**: сборка+тесты+checks из pipeline.yaml → Вердикт
  `Tasks\Отчёты\<CARD>_Вердикт_*.md`; FAIL → ретраи с хвостом ошибки → card_failed.
- **Чекпоинты**: метка «Чекпоинт: да» на карточке или закрытие этапа → пауза до
  «✅ Одобрить / 🔄 Перезапустить» в панели («⏸ Чекпоинты», `/api/checkpoints`).
- **Состояние**: `Tasks\Конвейер\runner_state.json` (`GET /api/runner`), панель таблицы —
  `/api/plan/tasks` (совместимо со старой сеткой колонок).

Секция конфига проекта:

```yaml
plan:
  repo: [D:\Projects\ProjectsPalns]
  subdir: MepTaggingSolution
  file: 2026-08-21_leader-crossing-roomtag-fix.md   # пусто = новейший _current/*.md
runner:
  model: opencode-go/deepseek-v4-flash
  retries: 2
  question_timeout_sec: 1200
  checkpoint_stages: true
```

Поддерживаемые форматы карточек плана (см. `pipeline/plans.py`): «Карточка X.Y» с буллетами,
именованные `GEO-N` с DoD-чекбоксами, карточки-секции `# Карточка задачи X.Y`.

## Длительность карточек (факт)

Факт считается из событий конвейера (task_started/subagent_finished):
`GET /api/plan/durations`, кнопка «⏱ Время» в панели. Плановые оценки живут в самом плане
(«Сроки» карточки), сервер их не дублирует.

## Анти-зависание агентов

- **Сервер** (heartbeat): зомби-агенты (нет heartbeat > `PIPELINE_WATCH_MAX_AGE`, default 90 с)
  → событие `agent_offline`. Период проверки — `PIPELINE_WATCH_INTERVAL` (default 30 с).
- **Сессии**: серверный watchdog помечает `stalled` сессии без heartbeat
  дольше `PIPELINE_SESSION_MAX_AGE` (default 300 с) + событие `session_stalled`.
- **Сторож контролёра** (`agent_watch.py`): детектор `check_stalled` — задача в `in_progress`
  дольше порога без JSON-отчёта → пометка `stalled` в history + событие `task_stalled`.
  Порог: `--stall-timeout N` или env `TASK_STALL_TIMEOUT_SEC` (default 10800 = 3 ч).
- **Менеджер субагентов** (`agent_manager.py`): `opencode run` с таймаутом
  `SUBAGENT_TIMEOUT` (default 1800 с) — при зависании убивается всё дерево процесса
  (`taskkill /F /T`, без node-сирот); PID и время старта пишутся в
  `Tasks\Конвейер\logs\<A-NN>.pid`.
- **Сторож × PID-файлы** (`agent_watch.check_subagent_zombies`): PID-файл старше
  `SUBAGENT_MAX_AGE_SEC` (default 3600 с) при живом процессе — сирота (менеджер
  убит/завис): дерево убивается, файл удаляется, задача помечается `task_stalled`.
  Мёртвые PID-файлы удаляются. Модель субагента — `DEFAULT_MODEL` (default
  `opencode/deepseek-v4-flash`, стабильная; flash-free глючит на длинных промптах).
- Менеджер НЕ создаёт фейковый отчёт при `rc≠0`/пустом отчёте — вместо этого
  пометка `task_stalled` в history + событие (редиспатч контролёром).
- Запуск: `python -X utf8 agents/agent_watch.py --project <p> [--stall-timeout 3600]`.

## Чат агентов (dashboard)

Кнопка «💬 Чат» в шапке панели — полноэкранная панель в 2 колонки (контакты + диалог,
размер изменяется за угол и сохраняется):

- **Список агентов** с живым статусом: 🟢 работает (есть текущая задача) / 🟡 online /
  🔴 offline / ⛔ спит (нет heartbeat > 90 с); показаны проект агента, время с последнего
  heartbeat, текущая задача (из событий task_started/subagent_finished).
  Фильтры: по проекту (`project` из heartbeat) и «только активные» (сохраняются).
- **Команды**: поле ввода → `POST /api/chat/command` (обёртка над `/messages`,
  сохраняется в очередь + публикуется в SSE-канал агента). Агент отвечает
  `send_message` — ответ появляется в диалоге в реальном времени (SSE-подписка на `feed`).
- **Кнопки**: «🫀 Проверить» (пинг/статус, PID), «📄 Запросить отчёт» (сводка по текущей
  задаче), «🔄 Перезапустить» (kill + запуск по сохранённой команде `cmd` из heartbeat),
  «⛔ Убить» (taskkill по PID), «🧹 Очистить» (диалог).
- Агенты отвечают на команды: `executor` — принял + текущая задача; `agent_watch` —
  сводка (всего/done/в работе/stalled).
- API: `GET /api/chat/agents`, `GET /api/chat/history?agent=X`, `POST /api/chat/command`,
  `POST /api/chat/agents/{name}/kill`, `POST /api/chat/agents/{name}/restart`.
- Heartbeat агентов передаёт `project`/`pid`/`cmd` (для kill/restart и фильтра по проекту);
  старые БД мигрируются автоматически (ALTER TABLE).

## Явные сессии субагентов (общение через сервер)

Субагенты исполнителей больше НЕ запускаются контролёром как слепые bash-субпроцессы
`opencode run` с гигантским промптом. Вместо этого — **явные сессии**: сервер держит
реестр сессий (`sessions` в БД), контролёр создаёт сессию через API с инструкцией (JSON:
task_file, report, log, prompt, model, skill), а тонкий `agents/session_worker.py`
подхватывает её:

```
контролёр (agent_manager)                    сервер                          session_worker
  POST /api/sessions {instruction} ──────►  session_created (SSE)
                                            ◄── GET /api/sessions/<id>   читает инструкцию
                                            ◄── POST .../start (pid/cmd)
  мониторинг: GET /api/sessions/<id>  ◄──   (heartbeat 30 с, статусы) ──► opencode run
  POST .../instruction "abort" ────────►    session_instruction → SSE-канал session-<id>
  POST .../kill ──────────────────────►    taskkill дерева
```

- **Инструкция — с сервера**, а не из bash-аргументов: worker читает `GET /api/sessions/<id>`
  и исполняет `opencode run` с промптом из инструкции (движок исполнения не меняется).
- **Статусы через сервер**: `POST /api/sessions/<id>/status` (done + report / failed + error),
  лента событий `session_created/session_started/session_status/session_stalled`.
- **Управление**: `POST /api/sessions/<id>/instruction` — инструкция в SSE-канал `session-<id>`
  (worker реагирует на abort/stop); `POST /api/sessions/<id>/kill` — сервер убивает дерево
  процесса (taskkill /F /T) по зарегистрированному pid.
- **Анти-зависание**: серверный watchdog (heartbeat.py) помечает сессии без heartbeat
  дольше `PIPELINE_SESSION_MAX_AGE` (default 300 с) как `stalled` + событие; heartbeat
  сессии — 30 с; таймаут исполнения — `SUBAGENT_TIMEOUT_SEC` (default 1800).
- **Агенты-помощники по ролям**: `POST /api/agents {role, project, model?, task?}` —
  создаёт сессию с предзагруженным скиллом роли (методика + роль): controller
  (pipeline-controller), executor (pipeline-executor), browser (pipeline-browser-bridge),
  reviewer (pipeline-reviewer), qwen (pipeline-qwen-worker), planner (pipeline-planner).
  Кнопка «➕ Новый агент» в панели «🗂 Сессии».
- **Фолбэк**: сервер недоступен → `agent_manager` молча переключается на legacy
  `opencode run` напрямую (или флаг `--legacy`); файлы+git остаются источником истины.
- Dashboard: кнопка «🗂 Сессии» — список сессий (id, задача, роль, статус, PID, возраст,
  заметка) с живым обновлением и кнопкой «⛔ Убить»; секция открытых opencode-сессий
  (`/api/sessions/live`) с кнопкой «🗑 Убить» (opencode session delete).
- API сессий: `POST /api/sessions`, `GET /api/sessions?project=&task=&status=`,
  `GET /api/sessions/{id}`, `POST /api/sessions/{id}/start|status|heartbeat|instruction|kill`,
  `POST /api/agents` (агент-помощник с ролью и скиллом).
- Оповещения в чат: ключевые события конвейера (session_created/started/status/stalled,
  task_stalled, report_done, verdict, subagent_finished) приходят в SSE-ленту `feed`
  и показываются в открытом диалоге чата кратким системным сообщением «🛰 ...»;
  команды из чата — через `POST /api/chat/command` (очередь + SSE-канал агента).

## Сырые задания пользователя (Входящие)

Кнопка «📥 Входящие» в шапке панели — сырые задания пользователя (не оформленные
в задачи запросы) с фиксацией как у общения агентов (БД + файлы + git):

- `POST /api/requests {project, text}` — задание: запись в БД (`requests`),
  файл `Tasks\Входящие\<id>_<тема>.md` + git-коммит `inbox: сырое задание ...`
  в проекте (файлы+git — источник правды, БД — координация).
- `GET /api/requests?project=&status=` — список (new / dispatched).
- `POST /api/requests/{id}/dispatch` — оформить в задачу: файл уходит в
  `Tasks\Активные\`, статус → dispatched, git-коммит `agent/A-NN: задача оформлена`.
- Dashboard: форма «➕ Добавить» (задание фиксируется сразу), список с датой,
  файлом и коммитом, кнопка «📋 Оформить» для new.

## Статус

- [x] Шаг 1: каркас, общий пакет `pipeline/`
- [x] Шаг 2: конфиг HeatLossRevit2, перенос проверок в `pipeline/checks.py`
- [x] Шаг 3: сервер (FastAPI + SQLite + SSE, `/events`, `/messages`, `/heartbeat`, dashboard)
- [x] Шаг 4: клиенты агентов (`agents/`): agent_watch, executor_client, browser_client
- [x] Шаг 5: скилы (pipeline-executor/-controller/-reviewer/-browser-bridge), docs
- [x] **v2: TDL удалён; план ProjectsPalns — источник истины** (pipeline/plans.py:
  3 формата карточек, set_card_status; server/plan_api.py: /api/plan/*, вопросы,
  чекпоинты, состояние раннера)
- [x] **v2: план-раннер agents/plan_runner.py** (grill-фаза, wait_answer.py,
  механический вердикт, ретраи, чекпоинты этапов) + панель «❓ Вопросы · ⏸ Чекпоинты»
- [x] Гибкие пути проектов: `project.root` — список кандидатов (E:\ПлагиныРевит / D:\Projects),
      либо `DEV_PIPELINE_PROJECTS_DIR` (базовая папка проектов на ПК)
- [x] runners vstest|dotnet|pytest|none; msbuild none — для python/фронтенд-проектов
- [x] Анти-зависание: параметризуемый watchdog (env) + маркеры stalled в файлах
- [x] Чат агентов: /api/chat/agents, /api/chat/history, /api/chat/command, панель в dashboard,
      ответы агентов (executor/agent_watch) на команды
- [x] Явные сессии субагентов: реестр сессий на сервере (/api/sessions), session_worker
      (инструкция с сервера, heartbeat, статусы, abort), мониторинг контролёром по API,
      watchdog stalled, панель «🗂 Сессии», фолбэк на legacy --legacy
- [x] Входящие: сырые задания пользователя (БД + файл Tasks\Входящие + git),
      панель «📥 Входящие» с формой и кнопкой «📋 Оформить»
- [x] Полный unit-тест: `python -X utf8 tests/run_all.py` (121 тест)
- [ ] Пилот на реальной карточке через план-раннер (карточка 4.2 плана v2)
