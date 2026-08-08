# -*- coding: utf-8 -*-
"""Qwen-мост для локального агента (deepseek free) + облачный Qwen.

Использует ГОТОВЫЙ конвейер LocalAssitent (сценарий 'text'): читает файл
(questions.txt) построчно, отправляет каждый вопрос в облачный ИИ, получает
ответ, сохраняет в answers.md. Это проверенный путь (тестировался на DeepSeek,
работает и на Qwen), в отличие от нестабильного send_to_cloud с копированием.

Локальный агент (opencode deepseek free) — «тонкий»: формулирует запрос, а
облачный Qwen пишет файлы (возвращает ```FILE: путь``` блоки). Мост:
  1. собирает контекст (задача + файлы) в questions.txt;
  2. запускает LocalAssitent pipeline (--scenario text --provider qwen);
  3. (--apply) применяет FILE:-блоки из ответа на диск.

Использование (из субагента):
  python -X utf8 "E:\ПлагиныРевит\dev-pipeline\agents\qwen_bridge.py" \
      --task <файл задачи> --context <файлы> --out Tasks/00_Референсы/Qwen_X.md
  python -X utf8 "E:\ПлагиныРевит\dev-pipeline\agents\qwen_bridge.py" \
      --task <файл> --out Tasks/00_Референсы/Qwen_X.md --apply --dir <корень>
  python -X utf8 "E:\ПлагиныРевит\dev-pipeline\agents\qwen_bridge.py" \
      --prompt "текст" --out ... --apply --dir ...
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

QWEN_DIR = r"E:\ПлагиныРевит\LocalAssitent"
QWEN_MODEL = "Qwen3.8-Max-Preview"


def _build_question(task_file: str | None, context_files: list[str], prompt: str) -> str:
    parts = []
    if task_file:
        p = Path(task_file)
        if p.exists():
            if p.name.endswith(".task.json"):
                parts.append(_task_json_human(p))
            else:
                parts.append("### ЗАДАЧА (выполни)\n" +
                             p.read_text(encoding="utf-8", errors="replace"))
    for cf in context_files:
        p = Path(cf)
        if p.exists():
            parts.append(f"### КОНТЕКСТ: {p.name}\n" +
                         p.read_text(encoding="utf-8", errors="replace")[:20000])
    if prompt:
        parts.append("### ИНСТРУКЦИЯ\n" + prompt)
    parts.append("""
### ТРЕБОВАНИЯ К ОТВЕТУ
1. Если нужно создать/изменить файлы — верни каждый файл блоком:
   ```FILE: <относительный путь>
   <содержимое файла>
   ```
   Кодировка UTF-8. Пути — относительно корня проекта.
2. Если это анализ — структурируй секциями (A/B/C) и в конце ### SUMMARY.
3. Не выдумывай результаты сборки/тестов — только факты из контекста.
4. В конце — маркер: END OF RESPONSE.
""")
    return "\n\n".join(parts)


def _task_json_human(p: Path) -> str:
    """Превратить TDL .task.json в человекочитаемую постановку для Qwen."""
    import json
    try:
        t = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return (f"### ЗАДАЧА {p.stem} (JSON)\n"
                + p.read_text(encoding="utf-8", errors="replace"))
    lines = [f"### ЗАДАЧА {t.get('task_id', p.stem)} (TDL)", ""]
    for label, key in [("Наименование", "name"), ("Цель", "goal"),
                       ("Описание", "description"), ("Статус", "status"),
                       ("Workflow", "workflow_state"), ("Модуль", "module"),
                       ("Класс", "class_name"), ("Слой", "layer")]:
        v = t.get(key)
        if v:
            lines.append(f"- {label}: {v}")
    crit = t.get("acceptance_criteria")
    if crit:
        lines += ["", "## Критерии приёмки"]
        for c in crit:
            lines.append(f"- {c}")
    ver = t.get("verification") or {}
    cmds = ver.get("commands")
    if cmds:
        lines += ["", "## Команды проверки"]
        for c in cmds:
            lines.append(f"- {c}")
    return "\n".join(lines)


def _run_localassitent(question: str, out: str, input_file: Path, answers_file: Path) -> tuple[str, str]:
    """Запустить готовый сценарий 'text' LocalAssitent. Возвращает (stdout, ответ).

    ВАЖНО: TextScenario читает input_file ПОСТРОЧНО и каждую строку отправляет
    отдельным вопросом. Поэтому весь промпт пишем в ОДНУ строку (переносы -> пробелы).
    """
    one_line = " ".join(question.splitlines())
    input_file.write_text(one_line, encoding="utf-8")
    cmd = ["python", "-X", "utf8", "main.py", "--scenario", "text",
           "--provider", "qwen", "--model", QWEN_MODEL,
           "--input", str(input_file), "--output", str(answers_file)]
    r = subprocess.run(cmd, cwd=QWEN_DIR, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1200,
                       creationflags=0 if os.name != "nt" else 0x08000000)
    response = ""
    if answers_file.exists():
        response = answers_file.read_text(encoding="utf-8", errors="replace")
    # сохранить полный ответ моста (с заголовком)
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(response, encoding="utf-8")
    return (r.stdout or "") + (r.stderr or ""), response


def _strip_question_prefix(response: str) -> str:
    """TextScenario возвращает '**Вопрос:** ... **Ответ:** <текст>'. Убрать вопрос."""
    m = re.search(r"\*\*Ответ:\*\*\s*([\s\S]*)", response)
    return m.group(1).strip() if m else response


def _apply_files(response_text: str, root: Path, force: bool = True) -> list[dict]:
    """Применить файлы из ответа: блоки ```FILE: путь ... ``` -> на диск.
    Возвращает [{rel, action}] где action in {written, skipped_exists, skipped_empty}."""
    body = _strip_question_prefix(response_text)
    applied = []
    pattern = re.compile(r"```FILE:\s*([^\s`]+)\s*\n(.*?)```", re.S)
    for m in pattern.finditer(body):
        rel = m.group(1).strip().strip('"').strip("'")
        content = m.group(2).strip("\n")
        if not content:
            applied.append({"rel": rel, "action": "skipped_empty"})
            continue
        target = root / rel
        if target.exists() and not force:
            applied.append({"rel": rel, "action": "skipped_exists"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        applied.append({"rel": rel, "action": "written"})
    return applied


def _is_complete(response: str) -> bool:
    return "END OF RESPONSE" in response or "```FILE:" in response


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", help="путь к файлу задачи (TDL .task.json или legacy)")
    ap.add_argument("--context", action="append", default=[], help="файлы контекста")
    ap.add_argument("--prompt", default="", help="прямая инструкция")
    ap.add_argument("--out", required=True, help="куда сохранить ответ Qwen")
    ap.add_argument("--apply", action="store_true", help="применить FILE:-блоки на диск")
    ap.add_argument("--dir", default=".", help="корень проекта для --apply")
    ap.add_argument("--force", action="store_true", help="перезаписывать существующие файлы")
    ap.add_argument("--retries", type=int, default=3, help="повторных попыток, если ответ без FILE/неполный")
    args = ap.parse_args()

    question = _build_question(args.task, args.context, args.prompt)
    work = Path(args.out).parent
    work.mkdir(parents=True, exist_ok=True)
    input_file = work / "qwen_questions.txt"
    answers_file = work / "qwen_answers.md"

    response = ""
    log = ""
    for attempt in range(1, max(1, args.retries + 1)):
        if attempt > 1:
            print(f"[qwen-bridge] повторная попытка {attempt}/{args.retries}: уточняю запрос...", flush=True)
            # уточнение: требуем вернуть файлы блоками
            question = ("Ответь ТОЛЬКО блоками ```FILE: <путь>\\n<содержимое>\\n```. "
                        "Ничего лишнего. Это повторный запрос после неполного ответа.\n\n" + question)
        print(f"[qwen-bridge] отправка в Qwen через LocalAssitent (сценарий text, попытка {attempt}) ...", flush=True)
        log, response = _run_localassitent(question, args.out, input_file, answers_file)
        print(log[-400:], flush=True)
        print(f"[qwen-bridge] ответ: {len(response)} символов")
        if args.apply and _apply_files(response, Path(args.dir), force=args.force):
            break
        if _is_complete(response):
            break
        # нет FILE-блоков и неполный — повторим
        if attempt >= args.retries:
            break

    print(f"[qwen-bridge] итог сохранён: {args.out}")

    if args.apply:
        root = Path(args.dir)
        applied = _apply_files(response, root, force=args.force)
        written = [a for a in applied if a["action"] == "written"]
        skipped = [a for a in applied if a["action"] != "written"]
        print(f"[qwen-bridge] применено файлов: {len(written)}"
              + (f" (+{len(skipped)} пропущено)" if skipped else ""))
        for a in written:
            print(f"   + {a['rel']}")
        for a in skipped:
            print(f"   ~ {a['rel']} ({a['action']})")
        if not applied:
            print("[qwen-bridge] FILE:-блоков нет. Проверь ответ, возможно нужна повторная отправка.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
