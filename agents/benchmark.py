# -*- coding: utf-8 -*-
"""Бенчмарк ядра MepTaggingSolution на фикстурах (тест-факт).

Прогоняет CoreConsoleRunner --dump по репрезентативным фикстурам,
считает метрики: марок по категориям, коллизии (Tag-Tag/Leader-Leader/Leader-Tag),
покрытие элементов, время. Пишет JSON-отчёт в Tasks\\Эксперт\\benchmark.json.

Использование:
  python -X utf8 agents/benchmark.py --project meptaggingsolution
  python -X utf8 agents/benchmark.py --project meptaggingsolution --fixture "1 этаж_Р"
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import load_config  # noqa: E402

EXE = "CoreConsoleRunner/bin/Debug/net48/CoreConsoleRunner.exe"
FIXTURES_ROOT = "TestRevitData/CoreFixtures"
OUT = "Tasks/Эксперт/benchmark.json"

# Репрезентативный набор: этажи, 3D, цоколь, узлы (фикстуры с элементами)
REPRESENTATIVE = [
    "1 этаж_Р", "2 этаж_Р", "3 этаж_Р", "4 этаж_Р", "5 этаж_Р",
    "6 этаж_Р", "7 этаж_Р", "8 этаж_Р",
    "Цоколь_Р_Отопление", "Цоколь_Р_Вентиляция",
    "3D ОВ", "3D Отопление", "3D Вентиляция",
    "Узел управления 1.1", "Обвязка радиаторов",
]


def run_dump(cfg, fixture, tmp_json):
    exe = cfg.root / EXE
    fixture_dir = cfg.root / FIXTURES_ROOT / fixture
    if not fixture_dir.exists():
        return None
    cmd = [str(exe), "--dump", str(fixture_dir), str(tmp_json)]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, cwd=str(cfg.root))
    elapsed = time.time() - t0
    if r.returncode != 0 or not Path(tmp_json).exists():
        return {"fixture": fixture, "error": r.stdout[-500:] + r.stderr[-500:], "elapsed": elapsed}
    return {"fixture": fixture, "elapsed": elapsed, "dump": json.load(open(tmp_json, encoding="utf-8"))}


def analyze(fixture, data):
    """Метрики по дампу."""
    dump = data.get("dump") or {}
    sug = dump.get("suggestions") or []
    errors = dump.get("errors") or []
    el = dump.get("elements") or 0

    # Коллизии (порт CollisionVerifier)
    from agents.dump_suggestions import verify
    issues = verify(sug)

    from collections import Counter
    by_cat = Counter(s.get("categoryId") for s in sug)

    return {
        "fixture": fixture,
        "elapsed_sec": round(data.get("elapsed", 0), 2),
        "elements": el,
        "suggestions": len(sug),
        "errors": errors,
        "collisions": len(issues),
        "collisions_by_type": {t: sum(1 for i in issues if i.startswith(t)) for t in
                               ["Tag-Tag", "Leader-Leader", "Leader-Tag"]},
        "suggestions_by_category": dict(by_cat),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agents.benchmark")
    ap.add_argument("--project", default="meptaggingsolution")
    ap.add_argument("--fixture", default="", help="одна фикстура; по умолчанию репрезентативный набор")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    cfg = load_config(args.project)
    tmp = cfg.root / "_bench_tmp.json"

    fixtures = [args.fixture] if args.fixture else REPRESENTATIVE
    results = []
    for f in fixtures:
        data = run_dump(cfg, f, tmp)
        if data is None:
            print(f"  [skip] {f}: фикстура не найдена")
            continue
        if "error" in data:
            print(f"  [ERR ] {f}: {data['error'][:100]}")
            results.append({"fixture": f, "error": data["error"]})
            continue
        row = analyze(f, data)
        results.append(row)
        print(f"  [{row['elapsed_sec']:5.2f}s] {f}: марок={row['suggestions']:4} "
              f"коллизий={row['collisions']:3} ({row['collisions_by_type']}) "
              f"ошибок={len(row['errors'])}")

    out_path = cfg.root / (args.out or OUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total_coll = sum(r.get("collisions", 0) for r in results if "collisions" in r)
    total_sug = sum(r.get("suggestions", 0) for r in results if "suggestions" in r)
    summary = {
        "fixtures": len(results),
        "total_suggestions": total_sug,
        "total_collisions": total_coll,
        "results": results,
    }
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nИТОГО: фикстур={len(results)}, марок={total_sug}, коллизий={total_coll}")
    print(f"Отчёт: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
