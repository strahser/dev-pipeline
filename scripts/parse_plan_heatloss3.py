# -*- coding: utf-8 -*-
"""Готовый скрипт для следующего агента: как парсить план heatloss3.

Запуск:
    python -X utf8 scripts/parse_plan_heatloss3.py
    python -X utf8 scripts/parse_plan_heatloss3.py --json
    python -X utf8 -m pipeline.cli status heatloss3

Что парсит:
    - EPIC-* → этап (summary, is_summary True или id startswith EPIC)
    - ARCH-* → листовые карточки (execution)
    - Статусы: done/open из "### Карточка ARCH-22 — ... ✅/⬜" + "- **Статус:** В работе/Выполнено"
    - Цель/Описание из "- **Цель:** ..." (поддерживает "**Цель:**" и "**Цель**:")
    - Длительность: runner_state.json updated → минуты (колонка ⏱ в /project/heatloss3/plan)

Git-правила (чтобы легко парсить):
    - id этапа EPIC-* → группа, ARCH-* → листы, все в _current/*.md
    - статус в заголовке карточки эмодзи + буллет Статус
    - зависимости в "- **Зависимости:** нет | ARCH-22, ARCH-23"
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config
from pipeline.plans import load

def main():
    import argparse, json
    ap = argparse.ArgumentParser(description="Парсинг плана heatloss3")
    ap.add_argument("--project", default="heatloss3")
    ap.add_argument("--json", action="store_true", help="вывод JSON")
    args = ap.parse_args()

    cfg = load_config(args.project)
    pf = cfg.find_plan_file()
    if not pf:
        print(f"план не найден: plan.repo {cfg.plan_repo} subdir {cfg.plan_subdir}")
        return 1
    plan = load(pf)
    rows = []
    for c in plan.cards:
        rows.append({
            "id": c.id,
            "title": c.title,
            "status": c.status,
            "is_stage": c.is_stage or c.id.startswith("EPIC"),
            "level": c.level,
            "deps": c.deps,
            "goal": (c.goal or "")[:200],
            "has_goal": bool(c.goal),
        })
    if args.json:
        print(json.dumps({"plan": str(pf), "progress": plan.progress(), "rows": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"план: {pf}")
        print(f"прогресс: {plan.progress()}")
        print(f"{'WBS':12} {'STATUS':8} {'KIND':10} {'TITLE'}")
        for r in rows:
            kind = "stage" if r["is_stage"] or r["id"].startswith("EPIC") else "leaf"
            print(f"{r['id']:12} {r['status']:8} {kind:10} {r['title'][:60]}")
        # длительность
        import datetime, json as _json
        sf = cfg.conveyor_dir() / "runner_state.json"
        if sf.exists():
            st = _json.loads(sf.read_text(encoding="utf-8"))
            if st.get("phase") == "executing":
                upd = st.get("updated","")
                try:
                    dt = datetime.datetime.fromisoformat(upd.replace("Z","+00:00"))
                    if dt.tzinfo: dt = dt.astimezone().replace(tzinfo=None)
                    mins = max(0, int((datetime.datetime.now() - dt).total_seconds()//60))
                    print(f"\nв работе: {st['card']} {mins} мин (попытка {st.get('attempt',1)}) — {' завис >30мин' if mins>30 else 'ok'}")
                except: pass
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
