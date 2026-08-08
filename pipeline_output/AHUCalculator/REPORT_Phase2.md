# ОТЧЁТ: AHUCalculator Phase 2 — Два расчётных периода + Чистые помещения

**Дата:** 2026-08-08
**Статус:** done

---

## 1. Что было не так

| Проблема | Критичность |
|----------|-------------|
| Один набор параметров наружного воздуха — нельзя моделировать зиму и лето одновременно | HIGH |
| Нет сезонности модулей — все модули работают в любом контексте | HIGH |
| Нет шаблона для чистых помещений | MEDIUM |
| Нет переключения между зимним и летним расчётом | MEDIUM |

## 2. Что сделано

### T7: Season enum и свойство модулей

- `Enums.cs` — добавлены `Season` (AllYear/WinterOnly/SummerOnly) и `CalculationPeriod` (Winter/Summer)
- `IProcessModule` — добавлено свойство `Season`
- `ProcessModuleBase` — `Season` property с `SetProperty()` (INPC)
- Все 10 модулей — `CloneModule()` копирует `Season`
- Сериализация `AHUProject` — `Season` сохраняется/восстанавливается

### T8: Два набора параметров OutdoorAirModule

- `OutdoorAirModule` — 6 новых свойств: `WinterTemperature`, `WinterRelativeHumidity`, `WinterVolumeFlow`, `SummerTemperature`, `SummerRelativeHumidity`, `SummerVolumeFlow`
- Метод `ApplyPeriod(CalculationPeriod)` — устанавливает активные параметры в зависимости от периода
- Дефолты: зима (-25°C, 80%, 10000 м³/ч), лето (30°C, 60%, 10000 м³/ч)

### T9: Двухпериодная логика AHUChainService

- `AHUChainService` — новое свойство `Period` (CalculationPeriod)
- `CalculateChain()` — вызывает `outdoor.ApplyPeriod(Period)` перед расчётом
- Фильтрация модулей: `Season.WinterOnly` пропускается в летнем периоде, `Season.SummerOnly` — в зимнем
- `SeasonMatches()` — приватный метод с pattern matching

### T10: UI переключения периода

- `MainViewModel` — `ActivePeriod`, `WinterResults`, `SummerResults`, `WinterChartModel`, `SummerChartModel`
- `DoRecalculate()` — считает оба периода, отображает активный
- `OnActivePeriodChanged()` — переключает отображаемые результаты и график
- `ModuleEditorControl.xaml` — OutdoorAirModule показывает зимние и летние параметры в двух панелях (синяя/жёлтая)
- `SeasonToIndexConverter` — конвертер для ComboBox выбора сезона в HeatingModule
- HeatingModule DataTemplate — ComboBox "Сезон" (Весь год / Только зимой / Только летом)

### T11: Шаблон чистого помещения ISO 7

- `TemplateService` — новый шаблон "Чистое помещение (ISO 7)"
- Схема: G4 → F7 → РКУ → Предподогрев(winter) → Охладитель/осушение → Догрев → Увлажнитель → H13 → Вентилятор → Шумоглушитель
- Предподогрев: `Season = Season.WinterOnly`, TargetTemperature = 5°C
- Охладитель: режим `ByDehumidification`, TargetTemperature = 14°C, TargetRH = 90%
- Увлажнитель: TargetHumidityRatio = 8.5 г/кг
- HEPA H13: PressureDrop = 250 Па

## 3. Доказательства

```
Сборка: dotnet build AHUCalculator.slnx → 0 ошибок, 0 предупреждений
Тесты:  dotnet test AHUCalculator.Tests  → 66/66 пройдено (0 failed, 0 skipped)
```

## 4. Числа до/после

| Метрика | Было | Стало |
|---------|------|-------|
| Наборов параметров наружного воздуха | 1 | 2 (зима + лето) |
| Периодов расчёта | 1 | 2 (одновременно) |
| Шаблонов ПВУ | 6 | 7 (+ чистое помещение) |
| Модулей с сезонностью | 0 | 10 (все) |
| Свойств OutdoorAirModule | 3 | 9 |

## 5. Изменённые файлы

```
M  Models/Enums.cs                      — Season, CalculationPeriod enum
M  Modules/IProcessModule.cs            — Season property
M  Modules/ProcessModuleBase.cs         — Season property + INPC
M  Modules/OutdoorAirModule.cs          — 6 winter/summer props + ApplyPeriod
M  Modules/HeatingModule.cs             — CloneModule copies Season
M  Modules/CoolingModule.cs             — CloneModule copies Season
M  Modules/MixingModule.cs              — CloneModule copies Season
M  Modules/RecuperatorModule.cs         — CloneModule copies Season
M  Modules/FilterModule.cs              — CloneModule copies Season
M  Modules/FanModule.cs                 — CloneModule copies Season
M  Modules/SilencerModule.cs            — CloneModule copies Season
M  Modules/AdiabaticCoolingModule.cs    — CloneModule copies Season
M  Modules/SteamHumidifierModule.cs     — CloneModule copies Season
M  Services/AHUChainService.cs          — Period property, season filtering
M  Services/AHUProject.cs               — Season + winter/summer serialization
M  Services/TemplateService.cs          — Cleanroom ISO 7 template
M  ViewModels/MainViewModel.cs          — dual-period calculation, ActivePeriod
M  Views/Controls/ModuleEditorControl.xaml — OutdoorAir dual-panel, Season ComboBox
A  Converters/BoolToEnumConverter.cs    — SeasonToIndexConverter
M  Tests/Integration/AHUChainServiceTests.cs — updated for new defaults
M  Tests/Integration/ExcelVerificationTests.cs — updated for new defaults
```

## 6. Как пересобрать/проверить

```powershell
dotnet build "D:\Projects\AHUCalculator\AHUCalculator.slnx"
dotnet test "D:\Projects\AHUCalculator\AHUCalculator.Tests\AHUCalculator.Tests.csproj"
```
