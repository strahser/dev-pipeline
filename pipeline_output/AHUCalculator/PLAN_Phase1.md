# ПЛАН: AHUCalculator — Phase 1 (MVVM Refactoring)

**Дата:** 2026-08-08
**Статус:** in_progress
**Постановщик:** пользователь
**Исполнитель:** MiMoCode Agent

---

## Контекст

Проект AHUCalculator — WPF-приложение (.NET 8.0) для расчёта приточно-вытяжных установок (ПВУ/AHU) с визуализацией на i-d диаграмме Молье. Текущее состояние — рабочий прототип с грубыми нарушениями MVVM, мёртвым кодом и отсутствием уведомлений об изменениях свойств модулей.

## Цель

Исправить архитектурные проблемы Phase 1 перед добавлением функционала для проектирования ПВУ чистых помещений.

## Задачи

| ID | Задача | Статус |
|----|--------|--------|
| T1 | Добавить INotifyPropertyChanged модулям (через ObservableObject + [ObservableProperty]) | done |
| T2 | Удалить мёртвый код (Styles.xaml, FlowArrowConverter, ModuleTypeToIconConverter) | done |
| T3 | Исправить MassFlow — использовать расчётную плотность вместо захардкоженной 1.2 | done |
| T4 | Переписать ModuleEditorControl на DataTemplate + двухсторонние привязки | done |
| T5 | Добавить валидацию ввода (NumericValidationRule, ValidatesOnExceptions) | done |
| T6 | HasUnsavedChanges guard + CanExecute команд | open |

## Границы (что НЕ делать)

- Не менять логику расчётов психрометрии
- Не добавлять новые модули (Phase 2)
- Не менять шаблоны TemplateService
- Не трогать тесты, если они проходят

## Результат

- Все файлы модулей обновлены: `partial class`, наследование от `ObservableObject`, `[ObservableProperty]` на полях
- ModuleEditorControl полностью переписан: 10 DataTemplate'ов с привязками + BoolToEnumConverter + NumericValidationRule
- MassFlow исправлен: `V/3600 * Density` вместо `V/3600 * 1.2`
- Dead code удалён: Styles.xaml очищен, FlowArrowConverter и ModuleTypeToIconConverter удалены
- ProcessModuleBase наследуется от ObservableObject (CommunityToolkit.Mvvm)
- Все 66 тестов проходят

## Как пересобрать/проверить

```powershell
dotnet build "D:\Projects\AHUCalculator\AHUCalculator.slnx"
dotnet test "D:\Projects\AHUCalculator\AHUCalculator.Tests\AHUCalculator.Tests.csproj"
```
