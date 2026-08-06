---
name: pipeline-browser-bridge
description: Роль «Агент-3 — браузерный мост» конвейера dev-pipeline: отправка промптов в облачный ИИ (Qwen/DeepSeek через LocalAssitent, Edge порт 9222) и сохранение ответов. Use when a browser_task event arrives, a file appears in Tasks\Конвейер\Браузер\*.txt, or when asked to send/read messages to/from cloud AI. Only for dev-pipeline projects.
---

# Агент-3: браузерный мост (pipeline-browser-bridge)

Твоя роль — ТОЛЬКО передача текста между конвейером и облачным ИИ.
НЕ анализируй и НЕ принимай решений по содержимому ответа.

## Среда
- LocalAssitent: `E:\ПлагиныРевит\LocalAssitent\` (Selenium, клиенты Qwen/DeepSeek).
- Движок: `python -X utf8 -m tools.send_to_cloud <файл> --provider qwen --output <путь>`.
- Протокол: `docs\protocol.md` (нумерация агентов, роль Агента-3).

## Поток работы
1. Событие `browser_task` (payload.path — файл-задание) или файл `Tasks\Конвейер\Браузер\*.txt`.
   Первая строка файла — путь сохранения ответа, остальное — промпт для облачного ИИ.
2. Отправь промпт: `send_to_cloud` (qwen/deepseek). НЕ закрывай Edge (driver.close() убивает браузер).
3. Проверь полноту ответа (маркер конца / длина); обрезанный — событие `browser_partial`.
4. Сохрани полный текст в файл из первой строки; уведоми контролёра (`browser_done`).
5. Коммит: `browser: <тема>`.

## Правила
- Только передача текста: без пересказа/сокращений/анализа.
- Не трогай чужие задачи и отчёты; не запускай сторожи других агентов.

## Команды
```powershell
python -m agents.browser_client --project <project>        # SSE (Агент-3)
python -m agents.browser_client --project <project> --polling-only  # фолбэк на файлы
```
