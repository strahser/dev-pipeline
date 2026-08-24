# runbook — как запускать dev-pipeline

## 1. Сервер координации

```powershell
cd E:\ПлагиныРевит\dev-pipeline
python -X utf8 -m server --host 127.0.0.1 --port 8787
```

- Панель: http://127.0.0.1:8787/ (dashboard.html).
- БД: `conveyor.db` (создаётся в корне dev-pipeline).
- Остановка: Ctrl+C (или Stop-Process по PID).
- Фоновый запуск (Windows):
  ```powershell
  $p = Start-Process python -ArgumentList @("-X","utf8","-m","server","--port","8787") `
       -WorkingDirectory "E:\ПлагиныРевит\dev-pipeline" -WindowStyle Hidden -PassThru
  $p.Id | Set-Content server.pid
  ```

## 2. CLI (без сервера — файлы+git, как v1)

```powershell
python -m pipeline.cli list
python -m pipeline.cli env
python -m pipeline.cli status heatlossrevit2
python -m pipeline.cli dispatch heatlossrevit2 <файл> --title "..." --priority высокий
python -m pipeline.cli verify heatlossrevit2 A-08
python -m pipeline.cli execute heatlossrevit2 A-08 --engine manual
```

## 3. Агенты (через сервер, SSE)

| Агент | Запуск | События |
|---|---|---|
| Контролёр (Агент-1) | `python -m agents.agent_watch --project heatlossrevit2 --watch-dispatch` | report_done, blocked, agent_offline |
| Исполнитель (Агент-2) | `python -m agents.executor_client --project heatlossrevit2` | task_assigned, instruction, fix_request |
| Браузер-мост (Агент-3) | `python -m agents.browser_client --project heatlossrevit2` | browser_task |

Фолбэк (сервер недоступен): добавьте `--polling-only` — агент поллит файлы, как v1.

## 4. Тесты фреймворка

```powershell
python -X utf8 tests/test_framework.py -v   # config/models/templates/checks
python -X utf8 tests/test_cli_smoke.py      # dispatch/status на временном проекте
python -X utf8 tests/test_server.py -v      # сервер: db/sse/heartbeat/events/messages
python -X utf8 tests/test_client.py -v      # client: ACK/recovery/фолбэк
python -X utf8 tests/run_all.py             # весь набор
```

## 4.1. План-раннер (основной сценарий v2)

```bat
:: Всё по плану проекта (до первой эскалации/черпка)
python -X utf8 -m agents.plan_runner --project meptaggingsolution

:: Одна карточка и выход (пилот)
python -X utf8 -m agents.plan_runner --project mepbimserver --once

:: Посмотреть, что раннер выбрал бы сейчас
python -X utf8 -m agents.plan_runner --project devpipeline --dry-run
```

Цикл карточки: выбор готовой (зависимости закрыты) → MD-постановка в Активные →
grill-фаза субагента (вопросы владельцу через `Tasks\Вопросы\*.md`, панель «❓ Вопросы») →
исполнение → механический вердикт → статус `Выполнено` в файле плана + коммит в ProjectsPalns.

- Чекпоинты: метка «Чекпоинт: да» или закрытие этапа → пауза; одобрение — панель
  («❓ Вопросы · ⏸ Чекпоинты») или `POST /api/checkpoints/<id>/approve`.
- Ретраи: FAIL вердикта повторяется с хвостом лога (`runner.retries`), затем card_failed и стоп.
- Состояние: `Tasks\Конвейер\runner_state.json` (`GET /api/runner`).
- Без сервера: добавьте `--legacy` (opencode run напрямую).

## 4.1.1. Crew: автономные сессии проекта (карточка 6.2)

```bat
:: Поднять crew: права opencode + сессия на каждую роль из crew.roles
python -X utf8 -m pipeline.cli up devpipeline

:: Цикл супервизора: порция -> handoff -> новая сессия (<= restart_policy)
python -X utf8 -m pipeline.cli supervise devpipeline --interval 30
```

- Конфиг: секции `crew:` (roles/model/permissions) и `restart_policy:`
  (max_restarts/cooldown_sec) в `examples\<проект>\pipeline.yaml`.
- Права чтения/записи — профиль `.opencode/permissions.json` в корне проекта;
  создаётся при первом `up`, существующий не перезаписывается.
- Handoff-цикл: воркер завершает порцию, пишет `Tasks\Конвейер\handoff\<SID>.md`
  (Репозитории и коммиты · Контекст · ГОТОВО · ЗАДАЧА · Цикл работы · Грабли)
  и выходит; супервизор поднимает новую сессию, чей промпт = инструкция + handoff.
- Бюджет: исчерпан `max_restarts` → событие `crew_exhausted`, автоперезапуск стоп.
- Панель: кнопка «▶ Поднять проект» (план-вью) вызывает тот же `/api/agents`.

## 4.2. Агент-менеджер (разовые задачи A-NN)

```powershell
# Миссия -> подзадачи A-NN -> субагенты (parallel)
python -X utf8 agents/agent_manager.py mission --project <p> --mission <ТЗ>.md --split 3

# Одна задача реальным субагентом
python -X utf8 agents/agent_manager.py task --project <p> --task A-05

# Демо-цикл без реального opencode (проверка механики)
python -X utf8 agents/agent_manager.py mission --project <p> --mission <ТЗ>.md --demo
```

**Уроки пилота (MepTaggingSolution, 2026-08-06):**
- `opencode run` субагентам нужен `--auto` (иначе останавливаются на запросе записи файла).
- Неинтерактивный субагент может **не дописать отчёт**: менеджер НЕ создаёт фейковый отчёт —
  ставит маркер stalled (`Tasks\Конвейер\stalled\<ID>.txt`) для редиспатча.
- Промпт субагента должен ЯВНО запрещать поиск «открытых задач» (иначе скилл-исполнитель
  конфликтует: менеджер уже перевёл задачу в in_progress).
- verify сравнивает тесты с `baseline_passed/baseline_total` из pipeline.yaml:
  не хуже базы = PASS (задача не про тесты), а не «0 фейлов».

## 4.3. Общий менеджер (кнопка «🛡 Менеджер»)

«Общий менеджер» — НАСТОЯЩАЯ терминальная сессия opencode (роль `manager`) на ВСЕ проекты:
следит за проектами (`/api/pulse_all`, `/api/checkpoints`, `/api/sessions`), принимает этапы
(пишет `Tasks\Конвейер\checkpoints\<CARD>.decision.json`) и задаёт вопросы владельцу.
Python-цикл `project_manager` остаётся страховкой (`--headless`).

```bat
:: Кнопка «🛡 Менеджер» в панели (role=manager, без проекта = все проекты) ->
::    POST /api/chat/agents/terminal -> agents/tui_cycle.py --role manager
:: Вручную:
python -X utf8 agents/tui_cycle.py --role manager                 :: все проекты
python -X utf8 agents/tui_cycle.py --role manager --project <p>   :: один проект
python -X utf8 agents/tui_cycle.py --role manager --headless      :: страховочный python-цикл project_manager
```

- **Приёмка чекпоинта**: pending → прочитай GOAL.md + вердикты + diff/log → напиши
  `<CARD>.decision.json` `{decision: approve|retry, comment, actor: manager}`;
  панель («⏸ Чекпоинты») и `wait_decision` подхватят решение.
- **Спорное** — вопрос владельцу через `Tasks\Вопросы\<CARD>_<время>.md` + `wait_answer.py`.
- **Границы**: код НЕ правит; запись — только decision.json / вопросы / handoff.
- **Handoff-цикл**: порция → handoff `Tasks\Конвейер\handoff\<метка>.md` → новая чистая сессия.
- **Страховка**: если opencode-сессия недоступна — `--headless` запускает старый
  python-цикл `project_manager.py` (приёмка done_report без вердикта + восстановление сессий).
- Автозадание: `auto_task_manager` собирает сводку всех проектов из `/api/pulse_all`.


## 5. Типовые сценарии

### Контроль целей проекта (отчёт менеджера)

```powershell
python -X utf8 agents/agent_manager.py report --project meptaggingsolution
# -> Tasks\Отчёт_менеджера.md: входящие/активные/архив, вердикты, рекомендации
```

Отчёт показывает цели (задачи по статусам) и что делать дальше. Проверяйте его
перед запуском новой миссии и после завершения субагентов.

### Панель «Что происходит» (UI)

Откройте http://127.0.0.1:8787/ — вкладка **«Что происходит»**:
- **Кто сейчас работает** — агенты/субагенты (heartbeat, online/offline).
- **Цели в работе** — активные задачи проекта (выбор проекта — в шапке).
- **Что происходит** — человекочитаемая лента событий («Субагент взялся за задачу A-04»,
  «Субагент закончил — отчёт готов»), без сырых индексов.
- **Как вмешаться** — если агент делает глупость (застрял, крутится, портит код):
  остановите субагента (Ctrl+C в терминале менеджера), переведите задачу в `rejected`
  или напишите замечание в файл задачи. Панель обновляется каждые 10 с и по SSE.

### DXF-отчёты (оценка прогресса расстановки)

Для тестового проекта MepTaggingSolution генерируйте DXF по фикстуре — план с комнатами,
элементами (трубы/воздуховоды), марками/предложениями, занятыми областями и сдвигами:

```powershell
python -X utf8 agents/dump_suggestions.py --project meptaggingsolution --dxf --out Tasks\Эксперт\View1.dxf
# Открыть в AutoCAD/DWG TrueView — оценить, как легли марки, где коллизии.
# Слои: Rooms, Elements, TagSuggestions, OccupiedTagsSpaces, NewTagPositions, OldTagPositions.
```

Сводка по комнатам и коллизии:
```powershell
python -X utf8 agents/dump_suggestions.py --project meptaggingsolution --summary --verify
```

### Полный цикл задачи через сервер
1. Контролёр: `dispatch <файл>` → задача A-NN (статус open) + коммит.
2. Контролёр (или automaton) шлёт событие `task_assigned` Агенту-2.
3. Исполнитель (SSE) берёт задачу → opencode run → отчёт → `report_done`.
4. Контролёр: `verify A-NN` → вердикт PASS/FAIL/PARTIAL + событие `verdict`.
5. PASS → задача в Архив (verified). FAIL/PARTIAL → `fix_request` исполнителю.

### Диагностика
```powershell
Invoke-RestMethod http://127.0.0.1:8787/healthz
Invoke-RestMethod http://127.0.0.1:8787/api/stats
Invoke-RestMethod http://127.0.0.1:8787/events?project=HeatLossRevit2
```

## 6. Устранение неполадок

| Симптом | Причина | Решение |
|---|---|---|
| `/healthz` недоступен | сервер не запущен | запусти `python -X utf8 -m server` |
| агенты не получают события | канал `to` не совпадает с `agent=` подписчика | проверь событие: `to` = "executor"/"controller"/"browser" |
| `conveyor.db` забит | много тестовых событий | удали `conveyor.db` (пересоздастся) |
| сборка/тесты FAIL в verify | конфликт obj между агентами | не запускать сборку параллельно |
