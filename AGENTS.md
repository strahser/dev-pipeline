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

## Правила

- Не коммитить: `.idea\`, `.opencode\`, `bin\obj`, `TestResults\`, `__pycache__\`, `*.db`, логи.
- Коммиты: `pipeline: ...` (каркас), `project/<имя>: ...` (конфиги примеров).
- Новый проект = новый `examples\<project>\pipeline.yaml` + проверка `list`/`status`.
