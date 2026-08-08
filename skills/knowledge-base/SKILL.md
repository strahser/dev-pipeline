---
name: knowledge-base
description: Общая база знаний по Revit-плагинам и 3D-вьюверу (репозиторий strahser/revit-skills): wiki проекта в .opencode/wiki/ + общие скилы в .opencode/skills/ (revit-api, revit-testing, revit-3d-export, threejs-viewer и др.), которые нельзя точно отнести к конкретному агенту конвейера. Use when starting work on a Revit-related task (нужен контекст архитектуры/форматов/паттернов), when learning something new about the project (обнови wiki), or when asked to maintain the project knowledge base.
---

# Knowledge Base (revit-skills)

Общая база знаний проектов Revit-разработки. Хранится в ОТДЕЛЬНОМ репозитории
`strahser/revit-skills` (это НЕ dev-pipeline и НЕ HeatLossRevit2 — это справочник).

## Что и где лежит

| Что | Путь (локальный) | Репозиторий |
|---|---|---|
| Wiki проекта | `D:\Projects\revit-skills\.opencode\wiki\` (рабочий ПК: `E:\ПлагиныРевит\revit-skills\.opencode\wiki\`) | strahser/revit-skills, main |
| Общие скилы | `D:\Projects\revit-skills\.opencode\skills\` | strahser/revit-skills, main |

## Wiki — страницы

| Страница | Содержание |
|---|---|
| `index.md` | Навигация по wiki (читай ПЕРВЫМ) |
| `clean-architecture-v10.md` | HeatLossRevit2: слои MainAppHeatLoss/Core/Base, HeatLossExport, правила миграции |
| `revit-export.md` | Спецификация JSON-экспорта геометрии Revit |
| `3d-viewer.md` | Архитектура Three.js вьювера, известные проблемы |
| `project-structure.md` | Структура репозиториев MepBimServer/revit-skills |
| `mcp-servers.md` | Документация MCP-серверов |
| `agent-workflow.md` | Самоуправление: добавление скилов, обновление wiki, git push |
| `revit-tunit-tests.md` | Unit-тесты Revit с подключением к процессу |
| `dashboard-opencontext.md` | Локальный дашборд и глобальная база знаний |

## Общие скилы (общая база, НЕ привязаны к агентам)

Скилы в revit-skills — справочник паттернов, а не роли конвейера. Используй их содержимое
как контекст, если задача касается Revit API, тестов или вьювера:

- `revit-api` — базовые паттерны Revit API: транзакции, сборщики, геометрия
- `revit-testing` / `revit-test-fixtures` / `revit-test-runner` — unit-тесты (Nice3point.TUnit.Revit)
- `revit-3d-export` / `revit-json-serialization` — экспорт геометрии и сериализация
- `threejs-viewer` — архитектура Three.js вьювера
- `mcp-setup` — настройка MCP-серверов
- `cloud-ai-bridge` — роутинг задач к облачному ИИ (ТЗ-шаблоны, контракт полноты)
- `revit-wiki` — описание wiki и самоуправления

## Когда использовать

1. **Перед Revit-задачей** — прочитай `wiki/index.md` и релевантную страницу (например,
   `clean-architecture-v10.md` для задач HeatLossRevit2): архитектура, пути данных, правила.
2. **Новая информация о проекте** (архитектура, баг, формат, конфигурация) — ОБНОВИ wiki.
3. **Новый полезный паттерн** — добавь в скил revit-skills или создай новый.

## Правила пополнения базы знаний (ОБЯЗАТЕЛЬНО для всех агентов конвейера)

Агенты конвейера (executor, controller, reviewer, qwen-worker) пополняют wiki проекта:

1. Узнал что-то новое и стабильное (не одноразовый факт) — записывай в wiki revit-skills,
   НЕ держи знания только в отчётах по задачам.
2. Git-дисциплина revit-skills: ветка `main`, коммиты делает контролёр (Агент-1)
   с префиксом `docs:` или `agent/A-NN: wiki: ...`; после пуша — уведомление.
   Если изменения готовы — оставь их в рабочем дереве revit-skills (или стейдж),
   сообщи контролёру файлом `Tasks\Конвейер\Уведомления\`.
3. Формат: markdown; заголовок H1; ссылки относительные; commit message на английском,
   краткий (до 80 символов).
4. Не удаляй существующие страницы и скилы без согласования.
5. `wiki/index.md` — обновляй ссылку при добавлении новой страницы.

## Разграничение с dev-pipeline

| dev-pipeline | revit-skills |
|---|---|
| Конвейер задач: TDL-задачи, отчёты, вердикты, статусы | База знаний: wiki + общие скилы |
| Источник истины по ЗАДАЧАМ | Источник истины по ЗНАНИЯМ (архитектура, паттерны, форматы) |
| Жизненный цикл A-NN | Жизненный цикл знаний (пополнение при новом знании) |

Не дублируй содержимое: отчёты задач остаются в dev-pipeline, знания — в revit-skills.
Ссылка из отчёта на wiki-страницу — приветствуется.
