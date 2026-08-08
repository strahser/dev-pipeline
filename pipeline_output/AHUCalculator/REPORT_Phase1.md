# ОТЧЁТ: AHUCalculator Phase 1 — MVVM Refactoring

**Дата:** 2026-08-08
**Статус:** done (partial — T6 остался)

---

## 1. Что было не так

| Проблема | Файл | Критичность |
|----------|------|-------------|
| ModuleEditorControl — ноль привязок в XAML, всё через code-behind | `Views/Controls/ModuleEditorControl.xaml.cs` | CRITICAL |
| Модули не реализуют INotifyPropertyChanged | Все `Modules/*.cs` | CRITICAL |
| MassFlow захардкожен на 1.2 вместо расчётной плотности | `Models/AirState.cs:35` | HIGH |
| Пустой catch блок проглатывает все ошибки | `ModuleEditorControl.xaml.cs:236-238` | HIGH |
| Dead code: 5 неиспользуемых стилей в Styles.xaml | `Resources/Styles.xaml` | MEDIUM |
| Dead code: FlowArrowConverter всегда возвращает Visible | `Converters/ValueConverters.cs:69-82` | MEDIUM |
| Dead code: ModuleTypeToIconConverter объявлен но не используется | `MainWindow.xaml:15` | MEDIUM |
| Конвертер цветов создаёт новый SolidColorBrush на каждый вызов | `Converters/ValueConverters.cs:15-28` | LOW |

## 2. Что сделано

### T1: INotifyPropertyChanged для модулей (10 файлов)

**ProcessModuleBase.cs** — переписан:
- Наследуется от `ObservableObject` (CommunityToolkit.Mvvm)
- Свойства `Name`, `Id`, `InputState`, `Pressure` используют `SetProperty()`
- Реализует `IProcessModule` (добавлен `INotifyPropertyChanged` в интерфейс)

**Все 10 модулей** обновлены:
- Классы стали `partial`
- Auto-properties заменены на `[ObservableProperty]` поля:
  - `OutdoorAirModule`: `_temperature`, `_relativeHumidity`, `_volumeFlow`
  - `HeatingModule`: `_inputMode`, `_targetTemperature`, `_targetPower`
  - `CoolingModule`: `_inputMode`, `_targetTemperature`, `_targetPower`, `_targetRelativeHumidity`, `_coilSurfaceTemperature`
  - `MixingModule`: `_secondaryTemperature`, `_secondaryRelativeHumidity`, `_secondaryVolumeFlow`
  - `RecuperatorModule`: `_efficiency`, `_exhaustTemperature`, `_exhaustRelativeHumidity`, `_exhaustVolumeFlow`
  - `FilterModule`: `_filterClass`, `_pressureDrop`
  - `FanModule`: `_pressureRise`, `_efficiency`
  - `SilencerModule`: `_insertionLoss`
  - `AdiabaticCoolingModule`: `_targetRelativeHumidity`
  - `SteamHumidifierModule`: `_targetHumidityRatio`

### T2: Dead code удалён

- `Styles.xaml` — очищен (5 неиспользуемых стилей удалены)
- `FlowArrowConverter` — удалён из `ValueConverters.cs`
- `ModuleTypeToIconConverter` — удалён из `ValueConverters.cs`
- `ModuleIconConverter` reference — удалён из `MainWindow.xaml`

### T3: MassFlow исправлен

**AirState.cs:35** — `MassFlow = VolumeFlow / 3600.0 * 1.2` → `MassFlow = VolumeFlow / 3600.0 * Density`

### T4: ModuleEditorControl переписан

**ModuleEditorControl.xaml** — полностью переписан:
- 10 неявных `DataTemplate` (по одному на каждый тип модуля)
- `ContentControl` с автовыбором шаблона по типу DataContext
- Двухсторонние привязки `UpdateSourceTrigger=PropertyChanged`
- RadioButtons с `BoolToEnumConverter` для выбора режима нагрева/охлаждения
- `WrapPanel` для компактного размещения полей в Mixing и Recuperator

**ModuleEditorControl.xaml.cs** — упрощён с 259 до 32 строк:
- Удалён весь ручной код заполнения/чтения TextBox
- Удалён пустой catch блок
- Остался только `OnApplyClick` и `FindMainViewModel`

### T5: Валидация ввода

**BoolToEnumConverter.cs** — новый файл:
- `BoolToEnumConverter` для RadioButtons (привязка enum к IsChecked)
- `NumericValidationRule` — проверка: число, диапазон [Min..Max]

Все TextBox в DataTemplate'ах используют:
- `ValidatesOnExceptions=True` — ловит FormatException от невалидного ввода
- `NotifyOnValidationError=True` — показывает красную рамку

### Дополнительно: Converter optimization

`ModuleTypeToColorConverter` — SolidColorBrush создаются один раз через `MakeBrush()` + `Freeze()`, а не на каждый вызов.

## 3. Доказательства

```
Сборка: dotnet build AHUCalculator.slnx → 0 ошибок, 0 предупреждений
Тесты:  dotnet test AHUCalculator.Tests  → 66/66 пройдено (0 failed, 0 skipped)
```

## 4. Числа до/после

| Метрика | Было | Стало |
|---------|------|-------|
| ModuleEditorControl.xaml.cs строк | 259 | 32 |
| Привязок в ModuleEditorControl.xaml | 0 | 40+ |
| Модулей с INotifyPropertyChanged | 0 | 10 |
| SolidColorBrush создаваемых на вызов | 10 (все) | 0 (все frozen) |
| Dead code файлов | 3 | 0 |
| Падающих тестов | 0 | 0 |
| MassFlow формула | `V/3600*1.2` | `V/3600*Density` |

## 5. Открытые вопросы

- **T6 (HasUnsavedChanges + CanExecute)** — не реализован в этом цикле. Требует:
  - `Closing` handler в MainWindow для проверки несохранённых изменений
  - `CanExecute` predicates для `RemoveSelectedModuleCommand`, `MoveModuleUpCommand`, `MoveModuleDownCommand`
  - Визуальный индикатор несохранённых изменений (звёздочка в заголовке)

- **ProcessPointIndexConverter** — по-прежнему использует `App.Current.MainWindow.DataContext` (service locator). Требует MultiBinding или AlternationIndex паттерна.

- **VisualTree walk** в `FindMainViewModel()` — всё ещё связывает control с конкретным типом окна.

## 6. Изменённые файлы

```
M  Modules/IProcessModule.cs           — INotifyPropertyChanged в интерфейсе
M  Modules/ProcessModuleBase.cs        — ObservableObject, SetProperty
M  Modules/OutdoorAirModule.cs         — partial, [ObservableProperty]
M  Modules/HeatingModule.cs            — partial, [ObservableProperty]
M  Modules/CoolingModule.cs            — partial, [ObservableProperty]
M  Modules/MixingModule.cs             — partial, [ObservableProperty]
M  Modules/RecuperatorModule.cs        — partial, [ObservableProperty]
M  Modules/FilterModule.cs             — partial, [ObservableProperty]
M  Modules/FanModule.cs                — partial, [ObservableProperty]
M  Modules/SilencerModule.cs           — partial, [ObservableProperty]
M  Modules/AdiabaticCoolingModule.cs   — partial, [ObservableProperty]
M  Modules/SteamHumidifierModule.cs    — partial, [ObservableProperty]
M  Models/AirState.cs                  — MassFlow fix
M  Converters/ValueConverters.cs       — frozen brushes, dead code removed
M  Views/MainWindow.xaml               — removed ModuleIconConverter ref
M  Views/Controls/ModuleEditorControl.xaml  — full rewrite (DataTemplates)
M  Views/Controls/ModuleEditorControl.xaml.cs — simplified (32 lines)
M  Resources/Styles.xaml               — cleared dead styles
A  Converters/BoolToEnumConverter.cs   — new (BoolToEnum + NumericValidation)
M  Tests/Unit/AirStateTests.cs         — updated MassFlow expectation
M  Tests/Unit/ModuleTests.cs           — updated power expectations
M  Tests/Integration/ExcelVerificationTests.cs — updated power expectations
```
