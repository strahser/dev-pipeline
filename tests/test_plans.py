# -*- coding: utf-8 -*-
"""Тесты парсера планов ProjectsPalns (pipeline/plans.py).

Покрывает три формата карточек, обновление статусов и выборку готовых карточек.
Запуск: python -X utf8 tests/test_plans.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import plans  # noqa: E402

PLAN_SDR = """# План: Тест СДР

## Миссия
Сделать тест.

## Сводная таблица СДР

| СДР | Наименование | Тип | Статус |
|---|---|---|---|
| `1` | Этап 1 | summary | Открыто |
| `1.1` | Первая карточка | execution | Открыто |
| `1.2` | Вторая карточка | execution | Открыто |

---

## Карточки листовых задач

### Карточка 1.1 — Первая карточка

- **Слой**: `core` · **Модуль**: m1 · **Статус**: `open`
- **Цель**: сделать первое.
- **Критерии приёмки**:
  1. Файл X существует.
  2. Сборка зелёная.
- **Зависимости**: нет.
- **Сроки**: начало 2026-08-21, завершение 2026-08-22.

### Карточка 1.2 — Вторая карточка

- **Статус**: `open`
- **Цель**: сделать второе.
- **Критерии приёмки**:
  1. Тест Y проходит.
- **Зависимости**: 1.1 (нужен файл X).
"""

PLAN_GEO = """# КАРТОЧКИ ЭТАПА 1: Geometry

> ## 🔍 РЕВЬЮ (2026-08-21) — 🔄 АКТУАЛЬНО

## GEO-1 — Инъекция зависимости ⬜

**Что делать:** заменить new на инъекцию.

**DoD (машинный):**
- [ ] `rg "new Foo()" .` → 0 совпадений
- [ ] verify.ps1 → PASS

## GEO-2 — Вторая точка ⬜

**DoD (машинный):**
- [ ] `rg "new Bar()" .` → 0 совпадений
"""


class TestPlanSdr(unittest.TestCase):
    def setUp(self):
        self.p = tempfile.mkdtemp(prefix="plans_sdr_")
        self.path = Path(self.p) / "plan.md"
        self.path.write_text(PLAN_SDR, encoding="utf-8")

    def test_parse_cards_and_table(self):
        plan = plans.load(self.path)
        self.assertEqual(len(plan.cards), 2)
        self.assertEqual(set(plan.sdr_rows), {"1", "1.1", "1.2"})
        c = plan.card("1.1")
        self.assertEqual(c.status, "open")
        self.assertEqual(c.title, "Первая карточка")
        self.assertEqual(c.criteria, ["Файл X существует.", "Сборка зелёная."])
        self.assertEqual(c.deps, [])
        self.assertEqual(plan.card("1.2").deps, ["1.1"])

    def test_ready_cards_respect_deps(self):
        plan = plans.load(self.path)
        self.assertEqual([c.id for c in plan.ready_cards()], ["1.1"])

    def test_set_status_updates_bullet_and_table(self):
        plans.set_card_status(self.path, "1.1", "done")
        plan = plans.load(self.path)
        self.assertEqual(plan.card("1.1").status, "done")
        row = next(l for l in self.path.read_text(encoding="utf-8").splitlines()
                   if l.startswith("| `1.1`"))
        self.assertIn("Выполнено", row)
        # 1.2 стала готовой
        self.assertEqual([c.id for c in plans.load(self.path).ready_cards()], ["1.2"])

    def test_set_status_in_progress(self):
        plans.set_card_status(self.path, "1.1", "in_progress")
        plan = plans.load(self.path)
        self.assertEqual(plan.card("1.1").status, "in_progress")
        self.assertEqual(plan.progress(), {"total": 2, "done": 0, "left": 2})


class TestPlanGeo(unittest.TestCase):
    def setUp(self):
        self.p = tempfile.mkdtemp(prefix="plans_geo_")
        self.path = Path(self.p) / "geo.md"
        self.path.write_text(PLAN_GEO, encoding="utf-8")

    def test_parse_named_cards_with_dod(self):
        plan = plans.load(self.path)
        self.assertEqual([c.id for c in plan.cards], ["GEO-1", "GEO-2"])
        c = plan.card("GEO-1")
        self.assertEqual(c.status, "open")           # ⬜ в заголовке
        self.assertEqual(c.criteria,
                         ['`rg "new Foo()" .` → 0 совпадений', 'verify.ps1 → PASS'])

    def test_emoji_done_marks_card(self):
        text = PLAN_GEO.replace("GEO-1 — Инъекция зависимости ⬜",
                                "GEO-1 — Инъекция зависимости ✅")
        path = Path(self.p) / "geo2.md"
        path.write_text(text, encoding="utf-8")
        plan = plans.load(path)
        self.assertEqual(plan.card("GEO-1").status, "done")
        self.assertEqual([c.id for c in plan.ready_cards()], ["GEO-2"])


PLAN_SECTIONS = """# План: секционный формат

## Сводная таблица СДР

| СДР | Наименование | Тип | Статус |
|---|---|---|---|
| `1` | Этап 1 | summary | Открыто |
| `1.1` | Карточка-секция | execution | Выполнено |

---

## 6. Карточки задач

# Карточка задачи 1.1

## Основные данные
- СДР: 1.1; Родитель: 1; Уровень: 2; Проект: T
- Тип задачи: execution; Статус: Выполнено

## Наименование
Карточка-секция

## Цель
Сделать по секциям.

## Зависимости
- нет

## Критерии приёмки
1. Всё зелёное.
"""


class TestPlanSections(unittest.TestCase):
    def test_h1_cards_with_sections(self):
        p = tempfile.mkdtemp(prefix="plans_sec_")
        path = Path(p) / "sec.md"
        path.write_text(PLAN_SECTIONS, encoding="utf-8")
        plan = plans.load(path)
        self.assertEqual([c.id for c in plan.cards], ["1.1"])
        c = plan.card("1.1")
        self.assertEqual(c.status, "done")            # и из строки таблицы, и из «Основные данные»
        self.assertEqual(c.title, "Карточка-секция")
        self.assertEqual(c.criteria, ["Всё зелёное."])
        self.assertEqual(c.deps, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
