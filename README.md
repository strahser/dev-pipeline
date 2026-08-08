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
python -m pipeline.cli tdl-close-summaries <project>      # summary закрываются при всех done-потомках
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

Оценки в спецификации: `estimate_sec` (или `estimate`) на этапе/классе/листе —
число в секундах, `<= 24` трактуется как часы, либо строка «2ч 30м» / «3.5h» / «45м» / «1д».

## Планировщик миссии (LLM-декомпозиция 1-го уровня)

Наивный `split_mission` (рез по `##`) заменяется LLM-планировщиком:

```bat
python -X utf8 agents/agent_manager.py mission --project heatlossrevit2 ^
    --mission "%HLR_MISSION%" --plan [--model opencode-go/qwen3.8-max]
```

- Запускает `opencode run` со скиллом `pipeline-planner`: миссия → этапы → классы → листовые
  задачи с goal, acceptance_criteria и estimate_sec (первая фаза — «Анализ и подготовка»).
- Пишет spec.json в `Tasks\Конвейер\планы\<имя>_<дата>.spec.json`, валидирует и вызывает `tdl-plan`.
- При сбое — фолбэк на старый `split_mission` (без иерархии).

## Длительность задач (план vs факт)

- TDL-задача: `dates.estimate_sec` (план, задаётся при tdl-plan), `dates.duration_sec` (факт,
  вычисляется при tdl-verify из start→finish; `tdl-start` фиксирует `start`).
- API: `GET /api/tdl/durations?project=<p>` — план/факт/Δ по всем задачам, для summary —
  суммирование по потомкам WBS, флаг `over_plan` (превышение >50%), сводка.
- Dashboard: кнопка «⏱ Время» — модальное окно с таблицей (WBS/задача/start→finish/план/факт/Δ/статус).

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
- **Фолбэк**: сервер недоступен → `agent_manager` молча переключается на legacy
  `opencode run` напрямую (или флаг `--legacy`); файлы+git остаются источником истины.
- Dashboard: кнопка «🗂 Сессии» — список сессий (id, задача, роль, статус, PID, возраст,
  заметка) с живым обновлением и кнопкой «⛔ Убить».
- API сессий: `POST /api/sessions`, `GET /api/sessions?project=&task=&status=`,
  `GET /api/sessions/{id}`, `POST /api/sessions/{id}/start|status|heartbeat|instruction|kill`.

## Статус

- [x] Шаг 1: каркас, общий пакет `pipeline/`
- [x] Шаг 2: конфиг HeatLossRevit2, перенос проверок в `pipeline/checks.py`
- [x] Шаг 3: сервер (FastAPI + SQLite + SSE, `/events`, `/messages`, `/heartbeat`, dashboard)
- [x] Шаг 4: клиенты агентов (`agents/`): agent_watch, executor_client, browser_client
- [x] Шаг 5: скилы (pipeline-executor/-controller/-reviewer/-browser-bridge), docs
- [x] TDL: JSON-конвейер (task/report/verdict), иерархия миссии (tdl-plan/tdl-tree), dashboard
- [x] Гибкие пути проектов: `project.root` — список кандидатов (E:\ПлагиныРевит / D:\Projects),
      либо `DEV_PIPELINE_PROJECTS_DIR` (базовая папка проектов на ПК)
- [x] Длительность задач: estimate/duration в TDL, `/api/tdl/durations`, модальное окно в dashboard
- [x] Планировщик миссии `--plan` (скилл pipeline-planner) + скилл
- [x] Анти-зависание: параметризуемый watchdog (env) + детектор task_stalled в agent_watch
- [x] Чат агентов: /api/chat/agents, /api/chat/history, /api/chat/command, панель в dashboard,
      ответы агентов (executor/agent_watch) на команды
- [x] Явные сессии субагентов: реестр сессий на сервере (/api/sessions), session_worker
      (инструкция с сервера, heartbeat, статусы, abort), мониторинг контролёром по API,
      watchdog stalled, панель «🗂 Сессии», фолбэк на legacy --legacy
- [x] Summary-закрытие: tdl-verify автоматически закрывает summary-предков (этапы/классы/
      миссию) при всех done-потомках; команда tdl-close-summaries
- [x] Полный unit-тест: `python -X utf8 tests/run_all.py` (125 тестов)
- [ ] Пилот на тестовой задаче через сервер
