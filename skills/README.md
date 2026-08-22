# Skills — перемещены

Скилы конвейера dev-pipeline **перенесены** в общий репозиторий знаний:

```
D:\Projects\revit-skills\.opencode\skills\   (remote: strahser/agent-skills)
```

## Что было перемещено

| Скил | Роль |
|------|------|
| `pipeline-controller` | Агент-1: диспетчер/контролёр |
| `pipeline-executor` | Агент-2: сотрудник-исполнитель |
| `pipeline-reviewer` | Ревьюер (git-аудит, PASS/NEEDS_CHANGES/FAIL) |
| `pipeline-planner` | Планировщик (планы ProjectsPalns) |
| `pipeline-grill` | Grill-фаза: вопросы владельцу до работы (Q-файлы + wait_answer) |
| `pipeline-browser-bridge` | Агент-3: мост к облачному ИИ |
| `pipeline-qwen-worker` | Тяжёлый воркер (Qwen) |
| `pipeline-placement-expert` | Эксперт по расстановке (MepTaggingSolution) |
| `planning-with-files` | Планирование с файлами (task_plan/findings/progress) |
| `architect-review` | Архитектурное ревью (read-only) |
| `software-architecture` | Clean Architecture/DDD паттерны |
| `solid-principles` | SOLID-принципы |
| `knowledge-base` | База знаний (revit-skills) |

## Почему

- Скилы загружаются глобально из `revit-skills\.opencode\skills`
  (см. `skills.paths` в `opencode.json`).
- dev-pipeline остаётся **конвейером задач** (план-раннер, сервер, агенты),
  а знания/роли живут в общем репозитории.

## Что делать агентам

- `opencode.json` dev-pipeline уже указывает `skills.paths` на
  `D:\Projects\revit-skills\.opencode\skills` — скилы доступны.
- Роль и промпты агентов (controller/executor/…) определены в
  `opencode.json` (секция `agent`) — они ссылаются на скилы по имени,
  пути не нужны.
