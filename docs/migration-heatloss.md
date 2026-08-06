# Миграция HeatLossRevit2 на dev-pipeline

Цель: перевести тестовый конвейер HeatLossRevit2 (`Tasks\Конвейер\pipeline.py` +
`executor_loop.ps1` + `browser_loop.ps1`) на фреймворк dev-pipeline, не ломая
работающий процесс (задачи A-01..A-09).

## Что уже сделано (шаги 1–2)

- `examples/heatlossrevit2/pipeline.yaml` — конфиг (пути msbuild/vstest/sln,
  проверки A-01..A-09, правила слоёв, audit_dirs).
- `pipeline/checks.py` — декларативные проверки (порты `verify_checks` из pipeline.py):
  build_grep, grep_dir, dir_exists, dir_exists_and_not, class_location, file_small,
  csproj_no_ref, layer_rules.
- `pipeline/cli.py` — обобщённые `status/dispatch/execute/verify`.
- Ключевые изменения vs v1:
  - циклические ссылки Projects/ProjectSettings → MainAppHeatLoss проверяются по
    `csproj_no_ref` (ProjectReference), а не namespace-grep (grep ловил собственный namespace);
  - `build_grep` → SKIP при пустом логе (нельзя судить без реальной сборки).

## Что осталось для полного перехода

### 1. Сервер + агенты (шаги 3–4)
- `server/` — FastAPI + SQLite + SSE (`/events`, `/messages`, `/heartbeat`, `/tasks`,
  dashboard `/`).
- `agents/` — `executor_client` (Агент-2), `agent_watch` (Агент-1), `browser_client` (Агент-3).

### 2. Замена сторожей
| v1 (HeatLossRevit2) | dev-pipeline |
|---|---|
| `Tasks\Конвейер\pipeline.py watch` | `python -m agents.agent_watch --project heatlossrevit2 --watch-dispatch` |
| `Tasks\Конвейер\executor_loop.ps1` | `python -m agents.executor_client --project heatlossrevit2` |
| `Tasks\Конвейер\browser_loop.ps1` | `python -m agents.browser_client --project heatlossrevit2` |

Старые .ps1 оставить как фолбэк (сервер недоступен) до полной проверки.

### 3. Протокол
- `docs/protocol.md` — обобщённый протокол; проект ссылается на него вместо копии.
- Скилы HeatLossRevit2 (`heatloss-executor`, `heatloss-browser-bridge`) заменить
  на `skills/pipeline-executor`, `skills/pipeline-browser-bridge` (обобщённые).

### 4. План-пилот
1. Запустить сервер.
2. Запустить `agent_watch` + `executor_client` (SSE).
3. Прогнать тестовую задачу A-10 через весь цикл.
4. Проверить ACK/зомби/фолбэк.

## Риски

- **Не дублировать стейт-машину в БД**: сервер хранит только события/сообщения;
  задачи — файлы+git. При расхождении файлов и БД — правда у файлов.
- **Конкурентная сборка**: verify (контролёр) и build (исполнитель) бьют по одним obj.
  Ограничить: исполнитель не запускает полную сборку sln параллельно с verify.
- **Два источника «кто взял задачу»**: статус в файле задачи — единственный авторитет.
  Событие `task_assigned` — только уведомление посмотреть файл.

## Приёмка перехода

- [ ] `python -m pipeline.cli status heatlossrevit2` — совпадает с v1.
- [ ] `python -m pipeline.cli verify heatlossrevit2 A-08` (или открытая задача) —
      сборка EXIT 0, тесты 109/109 (базовое), вердикт сформирован.
- [ ] `python -m agents.executor_client` получает событие по SSE и выполняет задачу.
- [ ] `python -m agents.agent_watch` ставит вердикт после отчёта.
- [ ] dashboard показывает задачи/агентов/ленту.
