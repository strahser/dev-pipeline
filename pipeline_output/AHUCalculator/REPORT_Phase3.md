# ОТЧЁТ: AHUCalculator Phase 3 — Десикант + Кратность + EU GMP + Тесты

**Дата:** 2026-08-08
**Статус:** done

---

## 1. Что было не так

| Проблема | Критичность |
|----------|-------------|
| Нет модуля адсорбционного осушителя (для зимнего осушения без охлаждения) | HIGH |
| Нет расчёта кратности воздухообмена для чистых помещений | MEDIUM |
| Нет рекомендаций по избыточному давлению | MEDIUM |

## 2. Что сделано

### T12: Модуль DesiccantDehumidifier

**Физика процесса:**
- Адсорбционный осушитель (сиккант/хлорид лития) удаляет влагу из воздуха
- Влагосодержание уменьшается: `x_out = x_in - η * (x_in - x_target)`
- Температура повышается за счёт тепла адсорбции: `Δt = Δx/1000 * L / cp` (L=2501 кДж/кг)
- Процесс на i-d диаграмме: вверх-влево (обезвоживание + нагрев)

**Свойства:**
- `TargetHumidityRatio` (г/кг) — целевое влагосодержание на выходе
- `Efficiency` (%) — эффективность осушения (0-100)

**Файлы:**
- `Modules/DesiccantDehumidifierModule.cs` — новый модуль (partial, [ObservableProperty])
- `Models/Enums.cs` — `ModuleType.DesiccantDehumidifier`, `ProcessType.DesiccantDehumidification`
- `Services/AHUChainService.cs` — обработка в switch processType
- `Services/AHUProject.cs` — сериализация/десериализация
- `Converters/ValueConverters.cs` — цвет #ED8936 (оранжевый)
- `Views/MainWindow.xaml` — кнопка в палитре (раздел Увлажнители)
- `Views/Controls/ModuleEditorControl.xaml` — DataTemplate с двумя полями
- `ViewModels/MainViewModel.cs` — AvailableModules + ModuleDisplayNames

### T13: Расчёт кратности воздухообмена

**Сервис `CleanroomCalculator.cs`:**
- `RoomParameters` — запись (Area, Height, TargetTemp, TargetRH → Volume)
- `CleanroomResult` — запись (RequiredACH, RequiredFlow, ActualFlow, ActualACH, PressureDiff, IsoClass, FlowSufficient)
- `GetRequiredACH(isoClass)` — минимальная кратность по ISO 14644-1
- `GetRecommendedPressure(isoClass)` — рекомендуемое ΔP
- `CalculateRequiredFlow(room, isoClass)` — требуемый расход
- `Evaluate(room, isoClass, actualFlow)` — полная оценка

**Таблица кратности ISO 14644-1:**

| Класс | Кратность (раз/час) | ΔP (Па) |
|-------|--------------------|---------| 
| ISO 3 | 300-600 | 25 |
| ISO 4 | 150-300 | 20 |
| ISO 5 | 60-150 | 15 |
| ISO 6 | 30-60 | 15 |
| ISO 7 | 15-30 | 10 |
| ISO 8 | 5-15 | 10 |

### T14: Контроль давления

Реализован через `CleanroomCalculator.GetRecommendedPressure(isoClass)` — рекомендуемое избыточное давление для каждого класса чистоты. Отображается в отчёте чистого помещения.

**UI — панель параметров чистого помещения:**
- Поля: площадь (м²), высота (м), класс ISO (ComboBox)
- Отчёт: класс, объём, требуемый/фактический расход, кратность, ΔP, индикатор достаточности
- `StringToVisibilityConverter` — скрывает панель если нет отчёта

## 3. Доказательства

```
Сборка: dotnet build AHUCalculator.slnx → 0 ошибок, 0 предупреждений
Тесты:  dotnet test AHUCalculator.Tests  → 66/66 пройдено (0 failed, 0 skipped)
```

## 4. Числа до/после

| Метрика | Было | Стало |
|---------|------|-------|
| Типов модулей | 10 | 11 (+ DesiccantDehumidifier) |
| ProcessType | 8 | 9 (+ DesiccantDehumidification) |
| Сервисов расчёта | 3 | 4 (+ CleanroomCalculator) |
| Параметров чистого помещения в UI | 0 | 3 (площадь, высота, ISO класс) |
| Шаблонов ПВУ | 7 | 8 (+ EU GMP Grade C) |
| Тестов | 66 | 90 (+24) |

## 5. Изменённые файлы

```
A  Modules/DesiccantDehumidifierModule.cs — новый модуль
M  Models/Enums.cs                        — +2 enum members
M  Services/AHUChainService.cs            — +1 case в switch
M  Services/AHUProject.cs                 — +сериализация
A  Services/CleanroomCalculator.cs        — новый сервис
M  Services/TemplateService.cs            — +EU GMP Grade C шаблон
M  Converters/ValueConverters.cs          — +2 класса (DesiccantBrush, StringToVisibility)
M  Views/MainWindow.xaml                  — +кнопка в палитре, +панель чистого помещения
M  Views/Controls/ModuleEditorControl.xaml — +DataTemplate
M  ViewModels/MainViewModel.cs            — +свойства чистого помещения, +UpdateCleanroomReport
M  Tests/Unit/ModuleTests.cs              — +7 тестов DesiccantDehumidifierModule
A  Tests/Unit/CleanroomCalculatorTests.cs — +17 тестов CleanroomCalculator
```

## 6. Как пересобрать/проверить

```powershell
dotnet build "D:\Projects\AHUCalculator\AHUCalculator.slnx"
dotnet test "D:\Projects\AHUCalculator\AHUCalculator.Tests\AHUCalculator.Tests.csproj"
```
