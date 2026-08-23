# AGENTS.md — контекст и конвенции dev-pipeline

Репозиторий-фреймворк конвейера задач для проектов в `D:\Projects\` (и `E:\ПлагиныРевит\`
на втором рабочем месте).

## Что это (v2)

**План-раннер**: агент исполняет план из репозитория ProjectsPalns карточка за карточкой.
Track table удалена — источник истины о статусах: **сам файл плана**
(статусы карточек «Открыто/Выполнено» обновляются прямо в нём + git-коммит).

Цикл одной карточки:

```
план (_current/*.md) → выбор готовой карточки (зависимости закрыты)
  → MD-постановка в Tasks\Активные\<CARD>_*.md
  → GRILL-фаза: субагент изучает код/вики; блокирующие вопросы владельцу через
    Tasks\Вопросы\<CARD>_*.md + agents/wait_answer.py (таймаут → работа по допущениям)
  → субагент (явная сессия на сервере или legacy opencode run) правит код, собирает, тестирует
  → механический вердикт (сборка+тесты+checks) в Tasks\Отчёты\<CARD>_Вердикт_*.md
  → PASS: статус done в плане + коммит в ProjectsPalns (`plan/<CARD>: …`)
  → FAIL: ретрай с хвостом ошибки (≤ runner.retries) → эскалация владельцу (card_failed)
```

Чекпоинты: после PASS карточки с меткой «Чекпоинт: да» или при закрытии целого этапа
раннер встаёт на паузу (`Tasks\Конвейер\checkpoints\<CARD>.pending.json`) до кнопки
«Одобрить/Перезапустить» в панели (`/api/checkpoints`).

## Ключевые принципы

- **Файлы + git = источник правды**: план, отчёты, вердикты, вопросы — файлы; сервер —
  только координация (лента событий, сессии, чат, панель).
- **Сервер (FastAPI + SQLite + SSE)**: push-уведомления агентам вместо поллинга.
- **Фолбэк на файлы**: сервер недоступен → файловые флаги `Tasks\Конвейер\Уведомления\`,
  раннер работает без него (--legacy).
- **Агенты не общаются в чате между собой** — события сервера + файлы+git.

## Стек

- Python 3.13+ (Windows), PyYAML, FastAPI/uvicorn/sse-starlette.
- `opencode run` — движок исполнения задач (сервер НЕ исполняет).

## Команды

- Список проектов / статус: `python -m pipeline.cli list|status <project>`
- Диспатч задачи из файла: `python -m pipeline.cli dispatch <project> <файл> [--title ...]`
- Верификация задачи A-NN: `python -m pipeline.cli verify <project> <A-NN>`
- **План-раннер** (основной сценарий):
  ```
  python -X utf8 -m agents.plan_runner --project <p> [--once] [--plan <файл>]
         [--retries N] [--model <модель>] [--legacy] [--dry-run]
  ```
- Ожидание ответа на вопрос (используется субагентом):
  `python -X utf8 agents/wait_answer.py "<Q-файл>" --timeout 1200`

## API панели (v2)

- Планы: `GET /api/plan`, `/api/plan/tasks|filters|task/{id}|running|durations`
- Вопросы: `GET /api/questions`, `POST /api/questions/{qid}/answer {project,text}`
- Чекпоинты: `GET /api/checkpoints`, `POST /api/checkpoints/{cid}/approve|retry {project}`
- Раннер: `GET /api/runner` (состояние из `Tasks\Конвейер\runner_state.json`)
- Сессии/чат/входящие — как раньше (`/api/sessions*`, `/api/chat/*`, `/api/requests*`)

## Анти-зависание

- Субагенты: таймаут 1800 с + убийство дерева процесса (`taskkill /F /T`), PID-файлы
  `logs\<CARD>.pid`; сторож убивает сирот (`SUBAGENT_MAX_AGE_SEC`).
- Задача in_progress без отчёта > `TASK_STALL_TIMEOUT_SEC` (3 ч) → маркер
  `Tasks\Конвейер\stalled\<ID>.txt` + событие task_stalled.
- Вопрос владельцу: тишина > `runner.question_timeout_sec` (20 мин) → ASSUMPTION,
  конвейер не стоит.
- Карточка FAIL: ретраи ≤ `runner.retries` с логом ошибки в промпте → card_failed + стоп.

## Правила

- **Субагентов создаёт только агент (opencode)** — и только когда они ему реально
  нужны для задачи (grill-фаза, параллельная работа, изоляция контекста). Владелец
  и панель субагентов вручную не запускают и в их работу не вмешиваются; контроль —
  только через штатные механизмы (heartbeat/сторож, kill зависших, crew-политика).
- Не коммитить: `.idea\`, `.opencode\`, `bin\obj`, `TestResults\`, `__pycache__\`, `*.db`, логи.
- Коммиты: `pipeline: ...` (каркас), `project/<имя>: ...` (конфиги примеров),
  в планах — `plan/<CARD>: ...`. Полная конвенция (префиксы, ветки, версии,
  `Refs: <CARD>`, запрет add -A в общих репо) — protocol.md §7.
- Цель проекта: `ProjectsPalns\<Проект>\GOAL.md` (секции «Цель»/«Результат»/
  «Не делать») — автоматически входит в бриф и промпт каждой карточки;
  создаёт/обновляет владелец при подключении и смене фокуса проекта.
- `runner.stage_approver: reviewer` — этап принимает ревьюер-сессия
  (GOAL.md + план + diff/log + отчёты); владелец утверждает продукт тегом `stage/<N>-done`.
- `runner.semantic_review: true` — после механического PASS карточку проверяет
  независимый ревьюер во временном git worktree (GOAL.md + план + diff/log
  последнего коммита); заключение дописывается в Вердикт секцией «Независимое ревью»,
  FAIL ревьюера = штатный ретрай, недоступность — мягкий режим (предупреждение,
  карточка не блокируется). Прототип; стиль механизма — как у stage_approver.
- Новый проект = `examples\<имя>\pipeline.yaml` (секции project/build/tests/plan/runner)
  + проверка `list`/`status`.

## Пути проекта

- `project.root` — строка ИЛИ список кандидатов; либо env `DEV_PIPELINE_PROJECTS_DIR`.
- План: секция `plan:` — `repo` (репозиторий ProjectsPalns), `subdir` (<Проект>),
  `file` (конкретный план; пусто = новейший `_current/*.md`). Env-альтернатива базы:
  `DEV_PIPELINE_PLANS_DIR`.

## Подключение НОВОГО проекта (инструкция агенту — ОБЯЗАТЕЛЬНО к исполнению)

Если ты (агент) получил ссылку на dev-pipeline и задачу «подключить/вести проект <Имя>»:

1. Создай конфиг `examples\<имя>\pipeline.yaml` (шаблоны: `examples\mepbimserver` —
   минимальный, `examples\heatlossrevit2` — полный с checks/layer_rules):
   - `project.name/root/branch`;
   - `tasks.*` — стандартная структура `Tasks\...`;
   - `build` — msbuild/dotnet/**none** (нет сборки); `tests` — runner
     vstest|dotnet|pytest|**none** + реальные baseline-цифры;
   - `plan:` — repo/subdir/file на ProjectsPalns;
   - `runner:` — model/retries/question_timeout_sec/checkpoint_stages;
   - `checks`/`layer_rules` — только реально проходящие проверки.
2. Проверь: `python -m pipeline.cli list|status <имя>`; dry-run раннера
   (`--dry-run`) показывает первую карточку.
3. Коммит `project/<имя>: регистрация в конвейере`; пуш после подтверждения.

## База знаний проекта (agent-skills / revit-skills)

- Общий хаб знаний — `D:\Projects\revit-skills` (remote strahser/agent-skills).
- Скилы конвейера: `revit-skills\.opencode\skills\pipeline-*` — включая **pipeline-grill**
  (методика вопросов), pipeline-executor/-controller/-reviewer/-planner.
- Вики: `revit-skills\.opencode\wiki\index.md` + локальные вики проектов.
- Новое стабильное знание → запись в вики (скилл knowledge-base); коммиты `docs:` делает контролёр.
- Не дублировать: dev-pipeline — задачи/отчёты/вердикты; revit-skills — знания.
