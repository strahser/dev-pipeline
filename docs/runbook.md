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

## 4.1. Агент-менеджер (запуск субагентов)

```powershell
# Миссия → подзадачи A-NN → субагенты (parallel)
python -X utf8 agents/agent_manager.py mission --project <p> --mission <ТЗ.md> --split 3 ^
    --model opencode/deepseek-v4-flash-free --skill pipeline-executor

# Одна задача реальным субагентом
python -X utf8 agents/agent_manager.py task --project <p> --task A-05 ^
    --model opencode/deepseek-v4-flash-free

# Демо-цикл без реального opencode (проверка механики)
python -X utf8 agents/agent_manager.py mission --project <p> --mission <ТЗ.md> --demo
```

**Уроки пилота (MepTaggingSolution, 2026-08-06):**
- `opencode run` субагентам нужен `--auto` (иначе останавливаются на запросе записи файла).
- Неинтерактивный субагент может **не дописать отчёт** после успешной работы → менеджер
  авто-генерирует отчёт (`_ensure_report`), если задача была взята (in_progress).
- Промпт субагента должен ЯВНО запрещать поиск «открытых задач» (иначе скилл-исполнитель
  конфликтует: менеджер уже перевёл задачу в in_progress).
- verify сравнивает тесты с `baseline_passed/baseline_total` из pipeline.yaml:
  не хуже базы = PASS (задача не про тесты), а не «0 фейлов».

## 5. Типовые сценарии

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
