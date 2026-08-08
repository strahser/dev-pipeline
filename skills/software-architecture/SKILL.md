---
name: software-architecture
description: Руководство по качественной архитектуре ПО (Clean Architecture, DDD) для проектов конвейера (C# .NET Framework, Python). Use when writing code, designing architecture, analyzing code, decomposing long classes, or refactoring for separation of concerns. Учитывает границы слоёв HeatLossRevit2 (Core → RevitServices → MainAppHeatLoss).
source: https://github.com/davila7/claude-code-templates/tree/main/cli-tool/components/skills/development/software-architecture (адаптировано под C#-стек)
---

# Software Architecture Development Skill

Руководство по качественной разработке и архитектуре: Clean Architecture + Domain-Driven Design.

## Правила стиля кода

### Общие принципы

- **Early return**: ранние выходы вместо вложенных условий (лучше читаемость)
- Избегай дублирования: переиспользуемые функции и модули
- Декомпозиция: функции/методы длиннее ~80 строк — дроби; файлы длиннее ~200 строк — на несколько файлов
- Маленькие связные методы вместо монолитов

### Best Practices

#### Library-First Approach

- **ВСЕГДА ищи существующие решения перед написанием своего кода** (NuGet для C#, PyPI для Python)
- Оценивай готовые библиотеки/API для стандартной функциональности
- Свой код — только когда:
  - уникальная бизнес-логика предметной области (теплотехнические расчёты, Revit-интеграция)
  - критичные по производительности пути
  - внешние зависимости избыточны
  - требования безопасности требуют полного контроля
- Пример: не пиши свой DI-контейнер — используй Autofac (уже в проекте); не пиши свой парсер — бери готовую библиотеку

#### Архитектура и дизайн

- **Clean Architecture & DDD:**
  - Бизнес-логика — в Core, независимо от фреймворков (Revit API НЕ в Core)
  - Доменные сущности отделены от инфраструктурных забот
  - Use cases изолированы и ясны
- **Именование:**
  - **ИЗБЕГАЙ** generic-имён: `utils`, `helpers`, `common`, `shared`, `Manager`
  - **ИСПОЛЬЗУЙ** доменные имена: `SurfaceHeatLossCalculator`, `RoomParameterReader`, `HeatLossReportGenerator`
  - У каждого модуля одна ясная цель
- **Разделение забот:**
  - НЕ смешивай бизнес-логику с UI
  - SQL-запросы/чтение параметров — не в контроллерах/командах, а в специализированных сервисах
  - Чёткие границы контекстов

#### Антипаттерны (избегать)

- **NIH (Not Invented Here):** свой ретрай-логик, свой валидатор, свой DI — когда есть проверенная библиотека
- **Плохие архитектурные решения:**
  - бизнес-логика в UI-коде (Windows/ViewModel)
  - чтение параметров Revit прямо в классах расчёта Core
  - отсутствие разделения забот
- **Generic-имена:** `Utils.cs` с 50 несвязанными методами; `Common/`, `Helpers/` как свалка
- Помни: каждая строка своего кода — обязательство (сопровождение, тесты, документация)

#### Качество кода (C#)

- Правильная обработка ошибок: `try/catch` по типу, без глотания исключений (`catch { }` — запрещено)
- Сложную логику — на маленькие переиспользуемые методы
- Глубина вложенности — max 3 уровня
- Методы — до ~50 строк; файлы — до ~200 строк
- Инварианты и входные параметры проверяй через `Guard`/`ArgumentNullException`

## Применение к слоям HeatLossRevit2 (приоритетно)

Архитектура плагина жёстко задана (проверяется контролёром через grep):

| Слой | Что может | Что НЕ может |
|---|---|---|
| `Base` | только сам себя | — |
| `Core` | `Base` | Autodesk.Revit.*, RevitServices, MainAppHeatLoss, HeatLossExport |
| `HeatLossExport` | `Base`, `Core` | Autodesk.Revit.*, RevitServices, MainAppHeatLoss |
| `RevitServices` | `Base`, `Core` | MainAppHeatLoss |
| `MainAppHeatLoss.Projects` / `.ProjectSettings` | `Base`, `Core`, `RevitServices`, `HeatLossRevit.UIResources`, `HeatLossExport` | `MainAppHeatLoss` (цикл!) |
| `MainAppHeatLoss` | всё (composition root) | — |

- Если архитектурное улучшение требует ссылки из Core на Autodesk.Revit — это ЗАПРЕЩЕНО: решай через интерфейсы (снимки данных в Core, чтение параметров — в RevitServices).
- Новые фичи: регистрация в DI (Autofac) через интерфейсы, а не `switch` по типу в ядре.
- Снимок → расчёт → запись: Revit читает модель → снимок в Core → Core считает → RevitServices записывает/рисует.

## Use cases

- Декомпозиция длинных классов (экспортёры 500+ строк)
- Выбор библиотек (NuGet) вместо своего кода
- Структурирование новых фич по Clean Architecture
- Рефакторинг легаси для читаемости и поддерживаемости

## Try saying

- «Как структурировать этот новый модуль?»
- «Помоги отрефакторить длинный метод»
- «Есть ли библиотека для этой функции?»
- «Спроектируй архитектуру для этой фичи»
