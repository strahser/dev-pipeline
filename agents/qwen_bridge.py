# -*- coding: utf-8 -*-
"""Qwen-мост для локального агента (deepseek free) + облачный Qwen.

Локальный агент (opencode deepseek free) — «тонкий»: он формулирует запрос, а
тяжёлую генерацию (код/файлы/анализ) делает облачный Qwen. Этот мост:
  1. собирает контекст (задача + запрошенные файлы);
  2. отправляет в Qwen через LocalAssitent (send_to_cloud);
  3. сохраняет полный ответ;
  4. (опция --apply) применяет файлы из ответа: блоки ```FILE: path``` пишутся на диск.

Использование (из субагента):
  python -X utf8 agents/qwen_bridge.py --task A-01 --context <файл> --out Tasks/00_Референсы/Qwen_A-01.md
  python -X utf8 agents/qwen_bridge.py --task A-01 --context <файл> --out ... --apply --dir <корень проекта>
  python -X utf8 agents/qwen_bridge.py --prompt "текст" --out ... --apply
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

QWEN_DIR = r"E:\ПлагиныРевит\LocalAssitent"
QWEN_MODEL = "Qwen3.8-Max-Preview"


def _build_prompt(task_file: str | None, context_files: list[str], prompt: str) -> str:
    parts = []
    if task_file:
        parts.append("### ЗАДАЧА (прочитай и выполни)\n" +
                     Path(task_file).read_text(encoding="utf-8", errors="replace"))
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
   Кодировка UTF-8. Пути — относительные от корня проекта.
2. Если это анализ — структурируй секциями (A/B/C) и в конце ### SUMMARY.
3. Не выдумывай результаты сборки/тестов — только факты из контекста.
4. В конце — маркер: END OF RESPONSE.
""")
    return "\n\n".join(parts)


def _send(prompt: str, out: str) -> str:
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp_prompt.md")
    tmp.write_text(prompt, encoding="utf-8")
    cmd = ["python", "-X", "utf8", "-m", "tools.send_to_cloud", str(tmp),
           "--provider", "qwen", "--model", QWEN_MODEL, "--output", str(out_path)]
    r = subprocess.run(cmd, cwd=QWEN_DIR, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    return (r.stdout or "") + (r.stderr or "")


def _apply_files(response_text: str, root: Path) -> list[str]:
    """Применить файлы из ответа: блоки ```FILE: path ... ``` -> на диск."""
    applied = []
    pattern = re.compile(r"```FILE:\s*([^\n]+)\n(.*?)```", re.S)
    for m in pattern.finditer(response_text):
        rel = m.group(1).strip().strip('"').strip("'")
        content = m.group(2).strip("\n")
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        applied.append(rel)
    return applied


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", help="путь к файлу задачи (TDL/legacy)")
    ap.add_argument("--context", action="append", default=[], help="файлы контекста")
    ap.add_argument("--prompt", default="", help="прямая инструкция")
    ap.add_argument("--out", required=True, help="куда сохранить ответ Qwen")
    ap.add_argument("--apply", action="store_true", help="применить FILE:-блоки на диск")
    ap.add_argument("--dir", default=".", help="корень проекта для --apply")
    args = ap.parse_args()

    prompt = _build_prompt(args.task, args.context, args.prompt)
    print(f"[qwen-bridge] отправка в Qwen ({QWEN_MODEL}) ...", flush=True)
    log = _send(prompt, args.out)
    print(log[-400:], flush=True)

    out_path = Path(args.out)
    if not out_path.exists():
        print("ОТВЕТ QWEN НЕ ПОЛУЧЕН")
        return 1
    text = out_path.read_text(encoding="utf-8", errors="replace")
    print(f"[qwen-bridge] ответ: {len(text)} символов -> {out_path}")

    if args.apply:
        root = Path(args.dir)
        applied = _apply_files(text, root)
        if applied:
            print(f"[qwen-bridge] применено файлов: {len(applied)}")
            for a in applied:
                print(f"   + {a}")
        else:
            print("[qwen-bridge] FILE:-блоков в ответе нет (только сохранён текст)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
