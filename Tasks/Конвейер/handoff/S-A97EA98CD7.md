# HANDOFF S-A97EA98CD7 — U3.1

## Репозитории и коммиты
- см. git log проекта (коммиты этой порции)

## Контекст
- сессия S-A97EA98CD7; задача/карточка: U3.1

## ГОТОВО
(см. отчёт)

## ЗАДАЧА (продолжение)
- доведи задачу до критериев приёмки;
- отчёт: D:\Projects\HVACLoadTerminals\Tasks\Отчёты\U3.1_Отчёт_2026-08-23_200133.md

## Цикл работы
- правки -> сборка -> тесты -> коммит -> push -> отметка в плане

## Грабли
ML), LiveRecalc debounce 300 мс (коалесинг правок), валидация числовых полей с сообщением; тесты +10 (108/108), отчёт, скриншоты и лог таймингов
e167969 plan/U3.1: паритет хостов App<->ревит-стенд — координаты в мм, «№. Имя»+уровень в таблицах, k_ef цветом по порогам, тулбар стенда (назначение/v решётки/проект/HTML), LiveRecalc debounce 300 мс, валидация числовых полей; тесты +10 (108/108), отчёт и скриншоты
1073e75 plan/U2.2: финальный отчёт по шаблону — верификация критериев приёмки офлайн-каталога (round-trip, внешний каталог в Calculate, 108/108 тестов, скриншот редактора)
=== 
U3.1_���䠪��
U3.1_�����_2026-08-23_194031.md
U3.1_�����_2026-08-23_200112.md
U3.1_�����_2026-08-23_200133.md
[0m
[0m✱ [0mGrep "Header="RoomId"|Header="X"|Header="Y""[90m in D:/Projects/HVACLoadTerminals/src · 0 matches[0m
[0m
[0m$ [0mdotnet build src\Core.Tests\HVACLoadTerminals.Core.Tests.csproj --nologo -v q -c Debug 2>&1 & dotnet test src\Core.Tests\HVACLoadTerminals.Core.Tests.csproj --nologo -v q -c Debug --no-build 2>&1
Сборка успешно завершена.
    Предупреждений: 0
    Ошибок: 0

Прошло времени 00:00:01.62
Тестовый запуск для D:\Projects\HVACLoadTerminals\src\Core.Tests\bin\Debug\net48\HVACLoadTerminals.Core.Tests.dll (.NETFramework,Version=v4.8)
Общее количество тестовых файлов (1), соответствующих указанному шаблону.

Пройден!   : не пройдено     0, пройдено   108, пропущено     0, всего   108, длительность 804 ms. - HVACLoadTerminals.Core.Tests.dll (net48)
[0m

