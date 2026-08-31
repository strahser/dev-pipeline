# HeatLoss3 — как парсить план (для следующего агента)

**Файл плана:** `d:\Projects\ProjectsPalns\HeatLoss3\_current\2026-09-07_revitservices-adapter-evil-5.md` (источник истины, `plan.repo/subdir` в `examples/heatloss3/pipeline.yaml:10`)

**Скрипт:** `python -X utf8 scripts/parse_plan_heatloss3.py` → `WBS | STATUS | KIND | TITLE` + `progress` + `в работе N мин`
```bat
python -X utf8 scripts/parse_plan_heatloss3.py --json
python -X utf8 -m pipeline.cli status heatloss3
curl http://127.0.0.1:8787/api/plan?project=heatloss3
curl http://127.0.0.1:8787/project/heatloss3/plan  # explicit с общим header, этап 📦 + ⏱
```

**Git-правила парсинга:**
- `EPIC-*` → этап (summary, `is_summary` или `startswith EPIC`), `ARCH-*` → листы (`execution`)
- Заголовок `### Карточка ARCH-22 — ProjectSettings 44→12 ✅/⬜` + буллет `- **Статус:** В работе/Выполнено` (`pipeline/plans.py:151` поддерживает `**Статус:**` и `**Статус**:`)
- `- **Цель:**` / `- **Цель**:` → `card.goal` (до 2к), `- **Зависимости:** нет | ARCH-22, ARCH-23` → `deps`
- Прогресс `plan.progress() done/total`, `ready_cards()` — листы `open` с закрытыми `deps`
- Длительность `Tasks\Конвейер\runner_state.json:11` `updated` → `⏱` минуты (если >30 — проверь `logs\ARCH-*.log`), также `/api/plan/running`

**Explicit панель:** `http://127.0.0.1:8787/project/heatloss3/plan` — `📦 Этап EPIC` с описанием `§1` + таблица `WBS | ID | Задача (+Описание) | Статус | Workflow | ⏱ | Отчёт | Вердикт`
