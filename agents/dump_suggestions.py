# -*- coding: utf-8 -*-
"""Дамп предложений алгоритма MepTaggingSolution + сводка по комнатам + проверка коллизий.

Использование:
  python -X utf8 agents/dump_suggestions.py --project meptaggingsolution --out <path.json>
  python -X utf8 agents/dump_suggestions.py --project meptaggingsolution --verify
  python -X utf8 agents/dump_suggestions.py --project meptaggingsolution --summary
  python -X utf8 agents/dump_suggestions.py --project meptaggingsolution --dxf --out Tasks\Эксперт\View1.dxf

Что делает:
  1. Запускает CoreConsoleRunner --dump <fixtureDir> <out.json> (реальный прогон Core),
     либо --dxf для DXF-визуализации (комнаты, элементы, марки, сдвиги — оценка прогресса).
  2. --summary: по комнатам считает элементы внутри (по locationCurvePoints/центру BBox)
     и марки, которые в них попали.
  3. --verify: порт CollisionVerifier (марка-марка, лидер-лидер, лидер-марка) на Python.

Зависимость: собранный CoreConsoleRunner.exe (dotnet build CoreConsoleRunner/CoreConsoleRunner.csproj).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from pipeline.proc import no_window_flags
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config   # noqa: E402

EXE = "CoreConsoleRunner/bin/Debug/net48/CoreConsoleRunner.exe"
FIXTURE = "TestRevitData/CoreFixtures/View1"
DEFAULT_OUT = "Tasks/Эксперт/suggestions.json"


# ---------------------------------------------------------------------------
# Проверка коллизий (порт CollisionVerifier на Python)
# ---------------------------------------------------------------------------

_EPS = 1e-6


def _overlaps(a, b):
    return (a[0] + _EPS < b[2] and b[0] + _EPS < a[2] and
            a[1] + _EPS < b[3] and b[1] + _EPS < a[3])


def _bbox(s):
    half_w = (s.get("profile_width", 0) or 0) / 2.0
    half_h = (s.get("profile_height", 0) or 0) / 2.0
    x, y = s["placementPoint"]["x"], s["placementPoint"]["y"]
    return (x - half_w, y - half_h, x + half_w, y + half_h)


def _cross(o, a, b):
    return (a["x"] - o["x"]) * (b["y"] - o["y"]) - (a["y"] - o["y"]) * (b["x"] - o["x"])


def _on_seg(p, q, r):
    return (q["x"] <= max(p["x"], r["x"]) + _EPS and q["x"] >= min(p["x"], r["x"]) - _EPS and
            q["y"] <= max(p["y"], r["y"]) + _EPS and q["y"] >= min(p["y"], r["y"]) - _EPS)


def _seg_intersect(p1, p2, q1, q2):
    if (p1 == q1 or p1 == q2 or p2 == q1 or p2 == q2):
        return False
    o1, o2 = _cross(p1, p2, q1), _cross(p1, p2, q2)
    o3, o4 = _cross(q1, q2, p1), _cross(q1, q2, p2)
    if ((o1 > _EPS and o2 < -_EPS) or (o1 < -_EPS and o2 > _EPS)) and \
       ((o3 > _EPS and o4 < -_EPS) or (o3 < -_EPS and o4 > _EPS)):
        return True
    if abs(o1) <= _EPS and _on_seg(p1, q1, p2) and not (q1 == p1 or q1 == p2): return True
    if abs(o2) <= _EPS and _on_seg(p1, q2, p2) and not (q2 == p1 or q2 == p2): return True
    if abs(o3) <= _EPS and _on_seg(q1, p1, q2) and not (p1 == q1 or p1 == q2): return True
    if abs(o4) <= _EPS and _on_seg(q1, p2, q2) and not (p2 == q1 or p2 == q2): return True
    return False


def _seg_intersects_box(p1, p2, box):
    if (box[0] + _EPS < p1["x"] < box[2] - _EPS and box[1] + _EPS < p1["y"] < box[3] - _EPS):
        return True
    corners = [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]
    for i in range(4):
        c1 = {"x": corners[i][0], "y": corners[i][1]}
        c2 = {"x": corners[(i + 1) % 4][0], "y": corners[(i + 1) % 4][1]}
        if _seg_intersect(p1, p2, c1, c2):
            return True
    return False


def verify(suggestions: list[dict]) -> list[str]:
    """Проверка коллизий. Возвращает список строк-нарушений."""
    # проставить ширину/высоту марки из профиля (один rule на категорию)
    by_cat = {}
    for s in suggestions:
        cid = s.get("categoryId")
        if cid not in by_cat:
            w = s.get("tagWidthFeet", 0) or s.get("cachedWidthFeet", 0)
            h = s.get("tagHeightFeet", 0) or s.get("cachedHeightFeet", 0)
            by_cat[cid] = (w, h)
        s["profile_width"] = by_cat.get(cid, (0, 0))[0]
        s["profile_height"] = by_cat.get(cid, (0, 0))[1]

    issues = []
    n = len(suggestions)
    # марка-марка
    for i in range(n):
        for j in range(i + 1, n):
            if _overlaps(_bbox(suggestions[i]), _bbox(suggestions[j])):
                issues.append(f"Tag-Tag: {suggestions[i]['elementId']} ↔ {suggestions[j]['elementId']}")
    # лидер-лидер
    for i in range(n):
        for j in range(i + 1, n):
            a, b = suggestions[i], suggestions[j]
            if a["basePoint"] == b["basePoint"]:
                continue
            if _seg_intersect({"x": a["basePoint"]["x"], "y": a["basePoint"]["y"]},
                              {"x": a["placementPoint"]["x"], "y": a["placementPoint"]["y"]},
                              {"x": b["basePoint"]["x"], "y": b["basePoint"]["y"]},
                              {"x": b["placementPoint"]["x"], "y": b["placementPoint"]["y"]}):
                issues.append(f"Leader-Leader: {a['elementId']} ↔ {b['elementId']}")
    # лидер-марка
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            lead, tag = suggestions[i], suggestions[j]
            if lead["elementId"] == tag["elementId"]:
                continue
            if tag.get("elementId") in lead.get("referencedElementIds", []):
                continue
            if _seg_intersects_box({"x": lead["basePoint"]["x"], "y": lead["basePoint"]["y"]},
                                   {"x": lead["placementPoint"]["x"], "y": lead["placementPoint"]["y"]},
                                   _bbox(tag)):
                issues.append(f"Leader-Tag: лидер {lead['elementId']} через марку {tag['elementId']}")
    return issues


# ---------------------------------------------------------------------------
# Сводка по комнатам
# ---------------------------------------------------------------------------

def point_in_polygon(pt, poly):
    x, y = pt["x"], pt["y"]
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]["x"], poly[i]["y"]
        xj, yj = poly[j]["x"], poly[j]["y"]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def build_summary(cfg, dump: dict, snapshot: dict, rooms: list[dict]) -> dict:
    """Для каждой комнаты: элементы внутри + марки (suggestions), чей placement/base внутри."""
    out = []
    for rm in rooms:
        rid = rm.get("id")
        name = rm.get("name", "?")
        poly = rm.get("boundary", [])
        elems_inside = 0
        for e in snapshot:
            pts = e.get("locationCurvePoints") or []
            inside = False
            for pt in pts:
                if point_in_polygon(pt, poly):
                    inside = True
                    break
            if inside:
                elems_inside += 1
        tags_inside = 0
        tag_ids = []
        for s in dump["suggestions"]:
            pp = s["placementPoint"]
            if point_in_polygon(pp, poly):
                tags_inside += 1
                tag_ids.append(s["elementId"])
        out.append({"roomId": rid, "name": name, "elementsInside": elems_inside,
                    "tagsInside": tags_inside, "tagIds": tag_ids[:20]})
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_dump(path) -> dict:
    return json.load(open(path, encoding="utf-8"))


def cmd_dump(args) -> int:
    cfg = load_config(args.project)
    out = cfg.root / (args.out or DEFAULT_OUT)

    if args.no_run:
        if not out.exists():
            print(f"Дамп не найден: {out}. Сначала запусти без --no-run.")
            return 1
    else:
        exe = cfg.root / EXE
        if not exe.exists():
            print(f"CoreConsoleRunner не собран: {exe}. Выполни: dotnet build CoreConsoleRunner/CoreConsoleRunner.csproj")
            return 1
        fixture = cfg.root / args.fixture
        mode = "--dxf" if args.dxf else "--dump"
        cmd = [str(exe), mode, str(fixture), str(out)]
        print("RUN:", " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120,
                           creationflags=no_window_flags())
        print((r.stdout or "").strip()[-500:])
        print((r.stderr or "").strip()[-500:])
        if r.returncode != 0 or not out.exists():
            print(f"{mode} НЕ УДАЛСЯ")
            return 1
    if args.dxf:
        print(f"\nDXF: {out}")
        return 0
    dump = _load_dump(out)
    if args.summary:
        snapshot = _load_snapshot(cfg)
        rooms = _load_rooms(cfg)
        summ = build_summary(cfg, dump, snapshot, rooms)
        print("\n=== СВОДКА ПО КОМНАТАМ ===")
        for row in summ:
            print(f"  [{row['roomId']}] {row['name'][:40]:40} элементов={row['elementsInside']:4} марок={row['tagsInside']}")
    if args.verify:
        issues = verify(dump["suggestions"])
        print(f"\n=== КОЛЛИЗИИ: {len(issues)} ===")
        for i in issues[:30]:
            print("  ", i)
    print(f"\nДУМП: {out}")
    return 0


def _load_snapshot(cfg) -> list:
    p = cfg.root / "TestRevitData/CoreFixtures/View1/viewSnapshot.json"
    return json.load(open(p, encoding="utf-8"))["elements"]


def _load_rooms(cfg) -> list:
    p = cfg.root / "TestRevitData/CoreFixtures/View1/rooms.json"
    return json.load(open(p, encoding="utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agents.dump_suggestions")
    ap.add_argument("--project", default="meptaggingsolution")
    ap.add_argument("--fixture", default=FIXTURE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--dxf", action="store_true",
                    help="сгенерировать DXF-визуализацию (комнаты/элементы/марки/сдвиги) вместо JSON-дампа")
    ap.add_argument("--no-run", action="store_true",
                    help="не пересобирать дамп, а проверить уже сохранённый файл")
    args = ap.parse_args(argv)
    return cmd_dump(args)


if __name__ == "__main__":
    sys.exit(main())
