# -*- coding: utf-8 -*-
"""РђРіРµРЅС‚-РјРµРЅРµРґР¶РµСЂ (РѕСЂРєРµСЃС‚СЂР°С‚РѕСЂ): РїСЂРёС‘Рј РјРёСЃСЃРёРё/РўР— -> РґРµРєРѕРјРїРѕР·РёС†РёСЏ РЅР° РїРѕРґР·Р°РґР°С‡Рё ->
Р·Р°РїСѓСЃРє СЃСѓР±Р°РіРµРЅС‚РѕРІ -> РјРѕРЅРёС‚РѕСЂРёРЅРі -> СЃРІРѕРґРєР°.

Р­С‚Рѕ В«С‚РѕР»РєР°СЋС‰РёР№В» СЃР»РѕР№ РїРѕРІРµСЂС… СЃРµСЂРІРµСЂР°/С„Р°Р№Р»РѕРІ: РјРµРЅРµРґР¶РµСЂ РќР• РёСЃРїРѕР»РЅСЏРµС‚ Р·Р°РґР°С‡Рё СЃР°Рј,
Р° РїРѕРґРЅРёРјР°РµС‚ РѕС‚РґРµР»СЊРЅС‹С… Р°РіРµРЅС‚РѕРІ-РёСЃРїРѕР»РЅРёС‚РµР»РµР№ РІ РѕС‚РґРµР»СЊРЅС‹С… РЇР’РќР«РҐ РЎР•РЎРЎРРЇРҐ РЅР° СЃРµСЂРІРµСЂРµ
(РєР°Р¶РґР°СЏ СЃРµСЃСЃРёСЏ = Р·Р°РїРёСЃСЊ /api/sessions + С‚РѕРЅРєРёР№ session_worker.py, РєРѕС‚РѕСЂС‹Р№ С‡РёС‚Р°РµС‚
РёРЅСЃС‚СЂСѓРєС†РёСЋ СЃ СЃРµСЂРІРµСЂР° Рё РѕС‚С‡РёС‚С‹РІР°РµС‚СЃСЏ С‡РµСЂРµР· СЃРµСЂРІРµСЂ; СЃРєРёР»Р»С‹ РїСЂРѕРµРєС‚Р° Рё РїСЂРѕС‚РѕРєРѕР»
РєРѕРЅРІРµР№РµСЂР° вЂ” РєР°Рє Рё СЂР°РЅСЊС€Рµ).

Р РµР¶РёРјС‹ Р·Р°РїСѓСЃРєР° СЃСѓР±Р°РіРµРЅС‚Р°:
  - СЏРІРЅР°СЏ СЃРµСЃСЃРёСЏ (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ): POST /api/sessions + session_worker.py
    (РёРЅСЃС‚СЂСѓРєС†РёСЏ/СЃС‚Р°С‚СѓСЃС‹/kill/abort вЂ” С‡РµСЂРµР· СЃРµСЂРІРµСЂ);
  - legacy: СЃРµСЂРІРµСЂ РЅРµРґРѕСЃС‚СѓРїРµРЅ (РёР»Рё --legacy) -> bash-`opencode run` РЅР°РїСЂСЏРјСѓСЋ;
  - parallel (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ): N СЃСѓР±Р°РіРµРЅС‚РѕРІ РѕРґРЅРѕРІСЂРµРјРµРЅРЅРѕ;
  - sequential: РїРѕ РѕРґРЅРѕРјСѓ (РїРѕР»РµР·РЅРѕ РїСЂРё РѕР±С‰РёС… С„Р°Р№Р»Р°С…/СЃР±РѕСЂРєРµ);
  - demo: Р±РµР· СЂРµР°Р»СЊРЅРѕРіРѕ opencode (РіРµРЅРµСЂРёСЂСѓРµС‚ Р·Р°РіР»СѓС€РµС‡РЅС‹Р№ РѕС‚С‡С‘С‚ РґР»СЏ РїСЂРѕРІРµСЂРєРё С†РёРєР»Р°).

Р—Р°РїСѓСЃРє:
  python -m agents.agent_manager --project meptaggingsolution --mission <РўР—.md> [--split 3]
  python -m agents.agent_manager --project meptaggingsolution --task A-01 --subagent
  python -m agents.agent_manager --project meptaggingsolution --mission <РўР—.md> --demo
"""
from __future__ import annotations

import argparse
import datetime
import glob
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config         # noqa: E402
from pipeline.templates import now               # noqa: E402
from pipeline.cli import cmd_dispatch            # noqa: E402
from pipeline.proc import no_window_flags        # noqa: E402
import argparse as _ap                           # noqa: E402


def _opencode_cmd() -> str:
    """РџСѓС‚СЊ Рє opencode: env OPENCODE_CMD, Р·Р°С‚РµРј РїСЂСЏРјРѕР№ exe РёР· npm global
    (Р·Р°РїСѓСЃРє С‡РµСЂРµР· opencode.cmd СѓРїРёСЂР°РµС‚СЃСЏ РІ Р»РёРјРёС‚ cmd 8191 СЃРёРјРІРѕР»РѕРІ вЂ”
    В«РЎР»РёС€РєРѕРј РґР»РёРЅРЅР°СЏ РєРѕРјР°РЅРґРЅР°СЏ СЃС‚СЂРѕРєР°В» РЅР° РґР»РёРЅРЅС‹С… РїСЂРѕРјРїС‚Р°С…; РёРЅС†РёРґРµРЅС‚ 2.1
    2026-08-24), Р·Р°С‚РµРј PATH."""
    env = os.environ.get("OPENCODE_CMD")
    if env and os.path.exists(env):
        return env
    npm_root = Path(os.environ.get("APPDATA", "")) / "npm"
    exe = npm_root / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    if exe.exists():
        return str(exe)
    found = shutil.which("opencode")
    if found and found.lower().endswith(".exe"):
        return found
    npm_cmd = npm_root / "opencode.cmd"
    if npm_cmd.exists():
        return str(npm_cmd)
    return "opencode"


OPENCODE = _opencode_cmd()
DEFAULT_MODEL = "opencode/big-pickle"  # СЃС‚Р°Р±РёР»СЊРЅР°СЏ РјРѕРґРµР»СЊ (2x usage); flash-free РіР»СЋС‡РёС‚ РЅР° РґР»РёРЅРЅС‹С… РїСЂРѕРјРїС‚Р°С…
# РђРЅС‚Рё-Р·Р°РІРёСЃР°РЅРёРµ: СЃСѓР±Р°РіРµРЅС‚ Р±РµР· СЂРµР·СѓР»СЊС‚Р°С‚Р° > N СЃ СѓР±РёРІР°РµС‚СЃСЏ. РќР°СЃС‚СЂР°РёРІР°РµС‚СЃСЏ env
# SUBAGENT_TIMEOUT_SEC (СЂР°РЅРЅРµСЂ С‚СЏР¶С‘Р»С‹С… РєР°СЂС‚РѕС‡РµРє РїРѕРґРЅРёРјР°РµС‚ РґРѕ 3600 вЂ” U2.1 2026-08-23).
SUBAGENT_TIMEOUT = int(os.environ.get("SUBAGENT_TIMEOUT_SEC", "1800"))
SERVER_URL = "http://127.0.0.1:8787"
DEV_PIPELINE_DIR = Path(__file__).resolve().parent.parent  # РєРѕСЂРµРЅСЊ dev-pipeline (РЅРµ С…Р°СЂРґРєРѕРґ E:\)


def _pid_alive(pid: int) -> bool:
    """Р–РёРІ Р»Рё РїСЂРѕС†РµСЃСЃ (Windows: tasklist; РёРЅР°С‡Рµ os.kill(pid, 0))."""
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, errors="replace",
                                 timeout=10, creationflags=no_window_flags()).stdout or ""
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_tree(pid: int) -> None:
    """РЈР±РёС‚СЊ РїСЂРѕС†РµСЃСЃ Рё РІСЃС‘ РµРіРѕ РґРµСЂРµРІРѕ (Windows: taskkill /F /T; РёРЅР°С‡Рµ SIGKILL).

    opencode.cmd РїРѕСЂРѕР¶РґР°РµС‚ node.exe вЂ” Р±РµР· /T СѓРјРёСЂР°РµС‚ С‚РѕР»СЊРєРѕ РѕР±С‘СЂС‚РєР° cmd,
    Р° node-РїСЂРѕС†РµСЃСЃ РѕСЃС‚Р°С‘С‚СЃСЏ СЃРёСЂРѕС‚РѕР№ Рё РІРёСЃРёС‚ (СЃР»СѓС‡Р°Р№ A-12, ~1 Р“Р‘ РїР°РјСЏС‚Рё)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, text=True, errors="replace",
                           timeout=10, creationflags=no_window_flags())
            return
        except Exception:
            pass
    try:
        os.kill(pid, 9)
    except OSError:
        pass

def _publish(cfg, client, type_: str, task_id: str, payload: dict | None = None):
    """РћРїСѓР±Р»РёРєРѕРІР°С‚СЊ СЃРѕР±С‹С‚РёРµ РІ СЃРµСЂРІРµСЂ РєРѕРѕСЂРґРёРЅР°С†РёРё (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ; РјРѕР»С‡Р° РїСЂРё РЅРµРґРѕСЃС‚СѓРїРЅРѕСЃС‚Рё)."""
    if client is None:
        return
    try:
        client.notify(type_, to="feed", task=task_id, payload=payload or {})
    except Exception:
        pass


def _hb(client, name: str):
    """РћС‚РјРµС‚РёС‚СЊ Р°РіРµРЅС‚Р° online РЅР° СЃРµСЂРІРµСЂРµ (heartbeat) вЂ” С‡С‚РѕР±С‹ РїР°РЅРµР»СЊ РїРѕРєР°Р·С‹РІР°Р»Р° В«СЂР°Р±РѕС‚Р°РµС‚В»."""
    if client is None:
        return
    try:
        client._request("POST", "/heartbeat", body={"agent": name}, timeout=3.0)
    except Exception:
        pass
SUBPROMPT = """РўР•Р‘Р• Р’Р«Р”РђРќРђ РљРћРќРљР Р•РўРќРђРЇ Р—РђР”РђР§Рђ: {task_file}

РќР• Р·Р°РґР°РІР°Р№ РІРѕРїСЂРѕСЃРѕРІ, РќР• СЃРїСЂР°С€РёРІР°Р№ В«РєР°РєСѓСЋ Р·Р°РґР°С‡Сѓ РІС‹РїРѕР»РЅСЏС‚СЊВ», РќР• РёС‰Рё Р·Р°РґР°С‡Рё РІ Tasks\\РђРєС‚РёРІРЅС‹Рµ вЂ”
РЅР°С‡РёРЅР°Р№ СЂР°Р±РѕС‚Сѓ РЅРµРјРµРґР»РµРЅРЅРѕ СЃ С€Р°РіР° 0.

РџРћР РЇР”РћРљ Р РђР‘РћРўР« (СЃС‚СЂРѕРіРѕ):
1. РџСЂРѕС‡РёС‚Р°Р№ {task_file} (РєРѕРЅС‚РµРєСЃС‚, С‚СЂРµР±РѕРІР°РЅРёСЏ, РіСЂР°РЅРёС†С‹).
2. РЎР РђР—РЈ РїСЂРёРјРµРЅСЏР№ РёР·РјРµРЅРµРЅРёСЏ РІ РїСЂРѕРµРєС‚Рµ: edit/write С„Р°Р№Р»РѕРІ. РќРµ РїРёС€Рё РїР»Р°РЅ, РЅРµ РѕРїРёСЃС‹РІР°Р№ РЅР°РјРµСЂРµРЅРёСЏ вЂ” СЂРµРґР°РєС‚РёСЂСѓР№.
3. РџРѕСЃР»Рµ РїСЂР°РІРѕРє Р·Р°РїСѓСЃС‚Рё СЃР±РѕСЂРєСѓ: dotnet build Core.Tests/Core.Tests.csproj --nologo -v q  (cwd = РєРѕСЂРµРЅСЊ РїСЂРѕРµРєС‚Р°). РЈР±РµРґРёСЃСЊ EXIT 0.
4. Р—Р°РїСѓСЃС‚Рё С‚РµСЃС‚С‹: dotnet test Core.Tests/Core.Tests.csproj --nologo -v q. РЈР±РµРґРёСЃСЊ, С‡С‚Рѕ РЅРµ С…СѓР¶Рµ Р±Р°Р·РѕРІРѕРіРѕ СЃРѕСЃС‚РѕСЏРЅРёСЏ
   (baseline РІ pipeline.yaml РїСЂРѕРµРєС‚Р°; РґРѕ РїСЂР°РІРѕРє РѕР±С‹С‡РЅРѕ 8/15 вЂ” СѓРєР°Р¶Рё С„Р°РєС‚РёС‡РµСЃРєРѕРµ РІ РѕС‚С‡С‘С‚Рµ).
5. РЎРѕР·РґР°Р№ РѕС‚С‡С‘С‚ РџРћ-Р РЈРЎРЎРљР РІ {report}: СЃРµРєС†РёРё В«Р§С‚Рѕ Р±С‹Р»Рѕ РЅРµ С‚Р°РєВ», В«Р§С‚Рѕ СЃРґРµР»Р°РЅРѕВ» (РїСѓС‚Рё С„Р°Р№Р»РѕРІ),
   В«Р”РѕРєР°Р·Р°С‚РµР»СЊСЃС‚РІР°В» (РІС‹РІРѕРґС‹ СЃР±РѕСЂРєРё/С‚РµСЃС‚РѕРІ), В«Р§РёСЃР»Р° РґРѕ/РїРѕСЃР»РµВ», В«РћС‚РєСЂС‹С‚С‹Рµ РІРѕРїСЂРѕСЃС‹В», В«РљР°Рє РїРµСЂРµСЃРѕР±СЂР°С‚СЊ/РїСЂРѕРІРµСЂРёС‚СЊВ».
6. Р’ С€Р°РїРєРµ Р·Р°РґР°С‡Рё {task_file} Р·Р°РјРµРЅРё 'СЃС‚Р°С‚СѓСЃ: in_progress' РЅР° 'СЃС‚Р°С‚СѓСЃ: done_report'.
7. РљРѕРјРјРёС‚: git add -A; git commit -m "agent/{task_id}: РѕС‚С‡С‘С‚ РёСЃРїРѕР»РЅРёС‚РµР»СЏ".

РџСЂР°РІРёР»Р°:
- Р’СЂРµРјРµРЅРЅС‹Рµ С„Р°Р№Р»С‹ (Р»РѕРіРё С‚РµСЃС‚РѕРІ Рё С‚.Рї.) РїРёС€Рё Р’ РџР РћР•РљРў (РїР°РїРєР° Tasks\\РљРѕРЅРІРµР№РµСЂ\\logs\\), РќР• РІ %TEMP% вЂ”
  РґРѕСЃС‚СѓРї Рє Temp РјРѕР¶РµС‚ Р±С‹С‚СЊ РѕРіСЂР°РЅРёС‡РµРЅ. Р•СЃР»Рё Р·Р°РїСѓСЃРєР°РµС€СЊ РєРѕРјР°РЅРґСѓ СЃ СЂРµРґРёСЂРµРєС‚РѕРј РІ С„Р°Р№Р» вЂ” РёСЃРїРѕР»СЊР·СѓР№
  РїСѓС‚СЊ РІРЅСѓС‚СЂРё РїСЂРѕРµРєС‚Р°.
- РќРµ РІС‹РґСѓРјС‹РІР°Р№ РІС‹РІРѕРґС‹ (СЃР±РѕСЂРєР°/С‚РµСЃС‚С‹ вЂ” СЂРµР°Р»СЊРЅС‹Рµ); РЅРµ С‚СЂРѕРіР°Р№ С„Р°Р№Р»С‹ РІРЅРµ Р·Р°РґР°С‡Рё.
- РќРµ СЃРѕР·РґР°РІР°Р№ СЃСѓР±Р°РіРµРЅС‚РѕРІ; РЅРµ Р·Р°РєСЂС‹РІР°Р№ Р·Р°РґР°С‡Сѓ.
- РћРўР§РЃРўРќР«Р™ Р¤РђР™Р› {report} вЂ” РџРћРЎР›Р•Р”РќРР™ РЁРђР“ Р РћР‘РЇР—РђРўР•Р›Р•Рќ. РќРµ Р·Р°РІРµСЂС€Р°Р№ СЃРµСЃСЃРёСЋ, РїРѕРєР° С„Р°Р№Р» {report}
  РЅРµ СЃРѕР·РґР°РЅ Рё РЅРµ СЃРѕРґРµСЂР¶РёС‚ РІСЃРµ СЃРµРєС†РёРё. РџСЂРѕРІРµСЂСЊ РІ РєРѕРЅС†Рµ, С‡С‚Рѕ С„Р°Р№Р» СЃСѓС‰РµСЃС‚РІСѓРµС‚ (Test-Path).
- Р•СЃР»Рё С‡С‚Рѕ-С‚Рѕ РЅРµ РїРѕР»СѓС‡Р°РµС‚СЃСЏ вЂ” РїРёС€Рё С‡РµСЃС‚РЅРѕ blocked/NEED_DATA РІ РѕС‚С‡С‘С‚Рµ, РЅРѕ СЃРЅР°С‡Р°Р»Р° СЃРґРµР»Р°Р№ РјР°РєСЃРёРјСѓРј РёР·РјРµРЅРµРЅРёР№.
"""


def slug(title: str) -> str:
    s = re.sub(r"[^\wР°-СЏРђ-РЇС‘РЃ\- ]", "", title).strip().replace(" ", "_")
    return s[:60] or "Р·Р°РґР°С‡Р°"


def next_task_id(cfg) -> str:
    ids = []
    for d in (cfg.abs_tasks_dir("active"), cfg.abs_tasks_dir("archive"),
              cfg.abs_tasks_dir("reports")):
        if d.is_dir():
            ids += re.findall(r"A-(\d+)", " ".join(os.listdir(d)))
    return "A-" + str((max([int(x) for x in ids] + [0]) + 1)).zfill(2)


def split_mission(text: str, n: int) -> list[str]:
    """Р Р°Р·Р±РёС‚СЊ С‚РµРєСЃС‚ РўР— РЅР° n РїРѕРґР·Р°РґР°С‡ РїРѕ Р·Р°РіРѕР»РѕРІРєР°Рј '## ' (РёР»Рё РїРѕ Р°Р±Р·Р°С†Р°Рј)."""
    parts = re.split(r"(?m)^(?=##\s)", text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < n:
        # РјР°Р»Рѕ СЃРµРєС†РёР№ вЂ” СЂРµР¶РµРј РїРѕ Р°Р±Р·Р°С†Р°Рј
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        parts = paras
    if len(parts) <= n:
        return parts
    # СЂР°РІРЅРѕРјРµСЂРЅРѕ СЃР»РёРІР°РµРј РІ n РєСѓСЃРєРѕРІ
    base = len(parts) // n
    rem = len(parts) % n
    chunks = []
    i = 0
    for k in range(n):
        size = base + (1 if k < rem else 0)
        chunks.append("\n\n".join(parts[i:i + size]))
        i += size
    return chunks


def dispatch_chunk(cfg, chunk: str, idx: int, total: int, title: str) -> str:
    """РЎРѕР·РґР°С‚СЊ Р·Р°РґР°С‡Сѓ A-NN РІ РђРєС‚РёРІРЅС‹Рµ РёР· РєСѓСЃРєР° РјРёСЃСЃРёРё. Р’РѕР·РІСЂР°С‰Р°РµС‚ id."""
    tid = next_task_id(cfg)
    task_file = f"{tid}_{slug(title)}.md"
    dst = cfg.abs_tasks_dir("active") / task_file
    body = chunk.strip()[:4000]
    content = f"""---
id: {tid}
РїСЂРёРѕСЂРёС‚РµС‚: РІС‹СЃРѕРєРёР№
СЃС‚Р°С‚СѓСЃ: open
РїРѕСЃС‚Р°РЅРѕРІС‰РёРє: Р°РіРµРЅС‚-РјРµРЅРµРґР¶РµСЂ
РёСЃРїРѕР»РЅРёС‚РµР»СЊ: subagent
РґР°С‚Р°: {now()}
РёСЃС‚РѕС‡РЅРёРє_Р·Р°РїСЂРѕСЃР°: РјРёСЃСЃРёСЏ (С‡Р°СЃС‚СЊ {idx}/{total})
Р·Р°РјРµС‡Р°РЅРёРµ: РјРёСЃСЃРёСЏ {title}
---

# Р—РђР”РђР§Рђ: {title} (С‡Р°СЃС‚СЊ {idx} РёР· {total})

## РљРѕРЅС‚РµРєСЃС‚ (Р·Р°С‡РµРј, С‡С‚Рѕ СѓР¶Рµ РёР·РІРµСЃС‚РЅРѕ)
{body}

## РўСЂРµР±РѕРІР°РЅРёСЏ (РєСЂРёС‚РµСЂРёРё РїСЂРёС‘РјРєРё)
Р’С‹РїРѕР»РЅРёС‚СЊ С‡Р°СЃС‚СЊ РјРёСЃСЃРёРё РёР· РєРѕРЅС‚РµРєСЃС‚Р°. РљР°Р¶РґРѕРµ В«СЃРґРµР»Р°РЅРѕВ» вЂ” СЃ РґРѕРєР°Р·Р°С‚РµР»СЊСЃС‚РІРѕРј
(Р»РѕРі СЃР±РѕСЂРєРё/С‚РµСЃС‚РѕРІ/grep, РїСѓС‚Рё С„Р°Р№Р»РѕРІ, РєРѕРјРјРёС‚С‹ agent/{tid}).

## Р“СЂР°РЅРёС†С‹ (С‡С‚Рѕ РќР• РґРµР»Р°С‚СЊ)
- РќРµ РјРµРЅСЏС‚СЊ Р°СЂС…РёС‚РµРєС‚СѓСЂСѓ СЃРІРµСЂС… Р·Р°РґР°С‡Рё; РЅРµ С‚СЂРѕРіР°С‚СЊ С„Р°Р№Р»С‹ РІРЅРµ СЃРІРѕРµР№ С‡Р°СЃС‚Рё.
- РќРµ РєРѕРјРјРёС‚РёС‚СЊ: .idea\\, .opencode\\, bin\\obj, TestResults\\.
- РќРµ СЃРѕР·РґР°РІР°С‚СЊ СЃСѓР±Р°РіРµРЅС‚РѕРІ; Р·Р°РґР°С‡Сѓ СЃР°РјРѕРјСѓ РЅРµ Р·Р°РєСЂС‹РІР°С‚СЊ (РђСЂС…РёРІ вЂ” С‚РѕР»СЊРєРѕ РєРѕРЅС‚СЂРѕР»С‘СЂ).

## Р РµР·СѓР»СЊС‚Р°С‚ (РєСѓРґР° РїРѕР»РѕР¶РёС‚СЊ Р°СЂС‚РµС„Р°РєС‚С‹)
РћС‚С‡С‘С‚ вЂ” Tasks\\РћС‚С‡С‘С‚С‹\\{tid}_РћС‚С‡С‘С‚_<РґР°С‚Р°>.md РїРѕ С€Р°Р±Р»РѕРЅСѓ РїСЂРѕС‚РѕРєРѕР»Р°;
РєРѕРјРјРёС‚ agent/{tid}.

## РҐРѕРґ СЂР°Р±РѕС‚С‹ (Р·Р°РїРѕР»РЅСЏРµС‚ РёСЃРїРѕР»РЅРёС‚РµР»СЊ)
- (Р·Р°РґР°С‡Р° РІС‹РґР°РЅР° {now()})
"""
    dst.write_text(content, encoding="utf-8")
    print(f"  [manager] Р·Р°РґР°С‡Р° {tid}: {task_file}")
    return tid


def subagent_env(cfg):
    """РЎС‚СЂРѕРєРё РѕРєСЂСѓР¶РµРЅРёСЏ, РєРѕС‚РѕСЂС‹Рµ СЃСѓР±Р°РіРµРЅС‚ РѕР±СЏР·Р°РЅ РїСЂРѕС‡РёС‚Р°С‚СЊ."""
    return (
        f"task_file={cfg.abs_tasks_dir('active')}",
        f"protocol={cfg.resolve(cfg.protocol)}",
        f"controller_prompt={cfg.root / 'Tasks' / '00_РљРѕРЅС‚СЂРѕР»С‘СЂ_РїСЂРѕРјРїС‚' / 'ControlerPromptv1.txt'}",
        f"executor_instr={cfg.root / 'Tasks' / 'РљРѕРЅРІРµР№РµСЂ' / 'РРќРЎРўР РЈРљР¦РРЇ_РёСЃРїРѕР»РЅРёС‚РµР»СЋ.md'}",
    )


def _build_subprompt(cfg, task_id: str, task_file: Path, report_path: Path,
                     skill: str = "", worker: str = "",
                     prompt_override: str = "") -> str:
    """РЎРѕР±СЂР°С‚СЊ РїСЂРѕРјРїС‚ СЃСѓР±Р°РіРµРЅС‚Р° (РѕР±С‰РёР№ РґР»СЏ legacy Рё СЃРµСЃСЃРёРѕРЅРЅРѕРіРѕ СЂРµР¶РёРјР°).

    prompt_override вЂ” РїРѕР»РЅР°СЏ Р·Р°РјРµРЅР° Р±Р°Р·РѕРІРѕРіРѕ SUBPROMPT (РёСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ РїР»Р°РЅ-СЂР°РЅРЅРµСЂРѕРј)."""
    skill_line = (f"Р—Р°РіСЂСѓР·Рё СЃРєРёР»Р» '{skill}' ({DEV_PIPELINE_DIR / 'skills' / skill / 'SKILL.md'}) "
                  f"РґР»СЏ СЂРѕР»РµРІС‹С… РїСЂР°РІРёР».\n"
                  f"Р’РђР–РќРћ: С‚РІРѕСЏ Р·Р°РґР°С‡Р° РЈР–Р• Р’Р«Р”РђРќРђ Рё РїСЂРёРєСЂРµРїР»РµРЅР° РІР»РѕР¶РµРЅРёРµРј: {task_file}. "
                  f"РќР• Р¶РґРё СѓРєР°Р·Р°РЅРёР№, РќР• СЃРїСЂР°С€РёРІР°Р№ 'РєР°РєСѓСЋ Р·Р°РґР°С‡Сѓ РІС‹РїРѕР»РЅСЏС‚СЊ' Рё РќР• РѕС‚РєСЂС‹РІР°Р№ "
                  f"Tasks\\РђРєС‚РёРІРЅС‹Рµ РІ РїРѕРёСЃРєР°С… РґСЂСѓРіРёС… Р·Р°РґР°С‡ вЂ” СЃСЂР°Р·Сѓ РїСЂРёСЃС‚СѓРїР°Р№ Рє С€Р°РіР°Рј РёР· РїСЂРѕРјРїС‚Р°.\n"
                  if skill else "") + ""

    if prompt_override:
        # Р‘РµР·РѕРїР°СЃРЅРѕРµ С„РѕСЂРјР°С‚РёСЂРѕРІР°РЅРёРµ: РЅРµРёР·РІРµСЃС‚РЅС‹Рµ/Р»РёС€РЅРёРµ {placeholder} РІ С‚РµРєСЃС‚Рµ
        # РєР°СЂС‚РѕС‡РєРё (РЅР°РїСЂРёРјРµСЂ, /buildings/{id}) РЅРµ РґРѕР»Р¶РЅС‹ СЂРѕРЅСЏС‚СЊ СЂР°РЅРЅРµСЂ KeyError'РѕРј.
        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"

        try:
            prompt = prompt_override.format_map(
                _SafeDict(task_file=task_file,
                          report=report_path,
                          task_id=task_id,
                          project=cfg.name)
            )
        except Exception:
            # РҐРІРѕСЃС‚С‹ РѕС€РёР±РѕРє СЃСѓР±Р°РіРµРЅС‚Р° СЃРѕРґРµСЂР¶Р°С‚ РїСЂРѕРёР·РІРѕР»СЊРЅС‹Рµ '{ ... }'
            # (PowerShell-РѕРґРЅРѕСЃС‚СЂРѕС‡РЅРёРєРё Рё С‚.Рї.) вЂ” Р°С‚СЂРёР±СѓС‚РЅС‹Р№ РґРѕСЃС‚СѓРї РІРёРґР°
            # {$_ .Path} СЂРѕРЅСЏРµС‚ format_map AttributeError'РѕРј Рё СѓР±РёРІР°РµС‚
            # СЂР°РЅРЅРµСЂ РјРµР¶РґСѓ РїРѕРїС‹С‚РєР°РјРё (РёРЅС†РёРґРµРЅС‚ U1.3 2026-08-23). Р’СЃРµ
            # СЂР°Р±РѕС‡РёРµ РїР»РµР№СЃС…РѕР»РґРµСЂС‹ Рє СЌС‚РѕРјСѓ РјРѕРјРµРЅС‚Сѓ СѓР¶Рµ РїРѕРґСЃС‚Р°РІР»РµРЅС‹
            # РїР»Р°РЅ-СЂР°РЅРЅРµСЂРѕРј С‡РµСЂРµР· str.replace вЂ” РёСЃРїРѕР»СЊР·СѓРµРј С‚РµРєСЃС‚ РєР°Рє РµСЃС‚СЊ.
            prompt = prompt_override
    else:
        prompt = SUBPROMPT.format(
            task_file=task_file,
            protocol=cfg.resolve(cfg.protocol),
            controller_prompt=str(cfg.root / "Tasks" / "00_РљРѕРЅС‚СЂРѕР»С‘СЂ_РїСЂРѕРјРїС‚" / "ControlerPromptv1.txt"),
            executor_instr=str(cfg.root / "Tasks" / "РљРѕРЅРІРµР№РµСЂ" / "РРќРЎРўР РЈРљР¦РРЇ_РёСЃРїРѕР»РЅРёС‚РµР»СЋ.md"),
            report=report_path,
            task_id=task_id,
            project=cfg.name,
        )
    if worker == "qwen":
        qwen_skill = "pipeline-qwen-worker"
        qwen_bridge = DEV_PIPELINE_DIR / "agents" / "qwen_bridge.py"
        qwen_block = (
            "Р—РђРџР Р•Рў: РќР• Р·Р°РіСЂСѓР¶Р°Р№ Рё РќР• РёСЃРїРѕР»СЊР·СѓР№ СЃРєРёР»Р»С‹ cloud-ai-bridge, revit-api, revit-3d-export, "
            "threejs-viewer Рё Р»СЋР±С‹Рµ Р”Р РЈР“РР• СЃРєРёР»Р»С‹, РєСЂРѕРјРµ pipeline-qwen-worker. Р Р°Р±РѕС‚Р°Р№ СЃС‚СЂРѕРіРѕ РїРѕ С€Р°РіР°Рј РЅРёР¶Рµ.\n"
            "Р”РћРџРћР›РќРРўР•Р›Р¬РќРћ (СЂРµР¶РёРј Р±РµСЃРїР»Р°С‚РЅРѕРіРѕ СЂР°Р±РѕС‡РµРіРѕ): С‚СЏР¶С‘Р»СѓСЋ РіРµРЅРµСЂР°С†РёСЋ С„Р°Р№Р»РѕРІ РґРµР»Р°РµС‚ "
            "РѕР±Р»Р°С‡РЅС‹Р№ Qwen С‡РµСЂРµР· РјРѕСЃС‚. РўР’РћРЇ Р—РђР”РђР§Рђ РЈР–Р• Р’Р«Р”РђРќРђ вЂ” С„Р°Р№Р»:\n"
            f"  {task_file}\n"
            "РќР• РёС‰Рё Р·Р°РґР°С‡Рё СЃРѕ СЃС‚Р°С‚СѓСЃРѕРј open РІ Tasks\\РђРєС‚РёРІРЅС‹Рµ, РќР• РѕС‚РєСЂС‹РІР°Р№ РѕР±С‰СѓСЋ Р±РµСЃРµРґСѓ. "
            "Р Р°Р±РѕС‚Р°Р№ РўРћР›Р¬РљРћ СЃ СЌС‚РёРј С„Р°Р№Р»РѕРј Р·Р°РґР°С‡Рё. РџРѕСЂСЏРґРѕРє (СЃС‚СЂРѕРіРѕ, РєР°Р¶РґС‹Р№ С€Р°Рі СЂРµР°Р»СЊРЅРѕР№ РєРѕРјР°РЅРґРѕР№):\n"
            f"  РЁРђР“ 1. РџСЂРѕС‡РёС‚Р°Р№ С„Р°Р№Р» Р·Р°РґР°С‡Рё {task_file} вЂ” СЌС‚Рѕ РїРѕСЃС‚Р°РЅРѕРІРєР°.\n"
            "  РЁРђР“ 2. РЎРѕР±РµСЂРё РєРѕРЅС‚РµРєСЃС‚: РїСЂРѕС‡РёС‚Р°Р№ РЅСѓР¶РЅС‹Рµ С„Р°Р№Р»С‹ РїСЂРѕРµРєС‚Р° (read/grep/glob), "
            "РѕРїСЂРµРґРµР»Рё С„Р°Р№Р»С‹, РєРѕС‚РѕСЂС‹Рµ РЅР°РґРѕ РёСЃРїСЂР°РІРёС‚СЊ.\n"
            f"  РЁРђР“ 3. Р’С‹Р·РѕРІРё РјРѕСЃС‚ (РѕРґРёРЅ РІС‹Р·РѕРІ, question РІ РѕРґРЅСѓ СЃС‚СЂРѕРєСѓ):\n"
            f"    python -X utf8 \"{qwen_bridge}\" --task \"{task_file}\" "
            "--context <РїСѓС‚Рё С‡РµСЂРµР· Р·Р°РїСЏС‚СѓСЋ> --out Tasks\\00_Р РµС„РµСЂРµРЅСЃС‹\\Qwen_<С‚РµРјР°>.md\n"
            f"  РЁРђР“ 4. РџСЂРёРјРµРЅРё С„Р°Р№Р»С‹, РєРѕС‚РѕСЂС‹Рµ Qwen В«РЅР°РїРёСЃР°Р»В»:\n"
            f"    python -X utf8 \"{qwen_bridge}\" --task \"{task_file}\" "
            "--out Tasks\\00_Р РµС„РµСЂРµРЅСЃС‹\\Qwen_<С‚РµРјР°>.md --apply --dir \"<РєРѕСЂРµРЅСЊ РїСЂРѕРµРєС‚Р°>\"\n"
            "  РЁРђР“ 5. РџСЂРѕРІРµСЂСЊ СЃР±РѕСЂРєСѓ Рё С‚РµСЃС‚С‹ (СЃРј. РєРѕРјР°РЅРґС‹ РІ Р·Р°РґР°С‡Рµ/РєРѕРЅС„РёРіРµ). РџСЂРё РѕС€РёР±РєР°С… вЂ” "
            "РѕС‚РїСЂР°РІСЊ Р»РѕРі Qwen РЅР° РёСЃРїСЂР°РІР»РµРЅРёРµ (РїРѕРІС‚РѕСЂРЅС‹Р№ РІС‹Р·РѕРІ РјРѕСЃС‚Р° СЃ Р»РѕРіРѕРј РІ --context).\n"
            "  РЁРђР“ 6. РЎРѕР·РґР°Р№ РѕС‚С‡С‘С‚ (СЃРј. SUBPROMPT РЅРёР¶Рµ).\n"
            "РџСЂРѕС‡РёС‚Р°Р№ СЃРєРёР»Р» pipeline-qwen-worker (D:\\Projects\\revit-skills\\.opencode\\skills\\pipeline-qwen-worker\\SKILL.md) вЂ” "
            "С‚Р°Рј СЃС…РµРјР° СЂР°Р±РѕС‚С‹ Рё РєРѕРјР°РЅРґС‹ РјРѕСЃС‚Р°.\n"
        )
        prompt = qwen_block + prompt
        if not skill:
            skill = qwen_skill
    if skill_line:
        prompt = skill_line + prompt
    return prompt


def _find_task_file(cfg, task_id: str) -> Path | None:
    """MD-С„Р°Р№Р» Р·Р°РґР°С‡Рё РІ РђРєС‚РёРІРЅС‹Рµ."""
    for f in glob.glob(str(cfg.abs_tasks_dir("active") / (task_id + "_*.md"))):
        return Path(f)
    return None


def run_subagent_legacy(cfg, task_id: str, report_path: Path, log_path: Path,
                        model: str = "", agent: str = "", skill: str = "", client=None,
                        worker: str = "", prompt_override: str = "") -> int:
    """Legacy-СЂРµР¶РёРј: opencode run РЅР°РїСЂСЏРјСѓСЋ РёР· bash-РїСЂРѕС†РµСЃСЃР° (С„РѕР»Р±СЌРє Р±РµР· СЃРµСЂРІРµСЂР°)."""
    task_file = _find_task_file(cfg, task_id)
    if not task_file:
        print(f"  [manager] Р·Р°РґР°С‡Р° {task_id} РЅРµ РЅР°Р№РґРµРЅР° РІ РђРєС‚РёРІРЅС‹Рµ (JSON Р·Р°РґР°С‡Рё)")
        return 2

    # open -> in_progress
    from pipeline.models import Task
    t = Task.from_file(task_file)
    if t.status == "open":
        t.set_status("in_progress")
        _publish(cfg, client, "task_started", task_id, {"file": task_file.name})
    _hb(client, f"subagent-{task_id}")

    prompt = _build_subprompt(cfg, task_id, task_file, report_path, worker=worker,
                              prompt_override=prompt_override)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [manager] СЃСѓР±Р°РіРµРЅС‚ {task_id}: opencode run (legacy)"
          + (f" (model={model})" if model else "")
          + (f" (agent={agent})" if agent else "")
          + (f" (skill={skill})" if skill else "")
          + (f" (worker={worker})" if worker else ""))
    cmd = [OPENCODE, "run", prompt]
    if model:
        cmd += ["-m", model]
    if agent:
        cmd += ["--agent", agent]
    # РџСЂРёРєСЂРµРїРёС‚СЊ С„Р°Р№Р» Р·Р°РґР°С‡Рё РєР°Рє РІР»РѕР¶РµРЅРёРµ: СЃСѓР±Р°РіРµРЅС‚ РіР°СЂР°РЅС‚РёСЂРѕРІР°РЅРЅРѕ РІРёРґРёС‚ РїРѕСЃС‚Р°РЅРѕРІРєСѓ
    # (РёРЅР°С‡Рµ РїСЂРё Р·Р°РіСЂСѓР·РєРµ СЃРєРёР»Р»Р° РїСѓС‚Р°РµС‚СЃСЏ В«РєР°РєРѕР№ С„Р°Р№Р»?В»).
    if task_file and task_file.exists():
        cmd += ["-f", str(task_file)]
    # --auto: Р°РІС‚Рѕ-РїРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ СЂР°Р·СЂРµС€РµРЅРёР№ (РёРЅР°С‡Рµ РЅРµРёРЅС‚РµСЂР°РєС‚РёРІРЅС‹Р№ СЃСѓР±Р°РіРµРЅС‚
    # РѕСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ РЅР° Р·Р°РїСЂРѕСЃРµ Р·Р°РїРёСЃРё С„Р°Р№Р»Р° Рё РЅРµ Р·Р°РІРµСЂС€Р°РµС‚ Р·Р°РґР°С‡Сѓ)
    cmd += ["--auto"]
    # PID-С„Р°Р№Р» СЃСѓР±Р°РіРµРЅС‚Р°: СЃС‚РѕСЂРѕР¶ РјРѕР¶РµС‚ РѕР±РЅР°СЂСѓР¶РёС‚СЊ Рё СѓР±РёС‚СЊ Р·Р°РІРёСЃС€РёР№ РїСЂРѕС†РµСЃСЃ
    pid_file = log_path.parent / f"{task_id}.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(cmd, cwd=str(cfg.root),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=no_window_flags())
        # PID-С„Р°Р№Р»: СЃС‚СЂРѕРєР° 1 вЂ” PID, СЃС‚СЂРѕРєР° 2 вЂ” РІСЂРµРјСЏ СЃС‚Р°СЂС‚Р° (unix).
        # РЎС‚РѕСЂРѕР¶ (agent_watch) РїРѕ РЅРµРјСѓ РЅР°С…РѕРґРёС‚ СЃРёСЂРѕС‚: РјРµРЅРµРґР¶РµСЂ СѓР±РёС‚/Р·Р°РІРёСЃ,
        # Р° СЃСѓР±Р°РіРµРЅС‚ РїСЂРѕРґРѕР»Р¶Р°РµС‚ РІРёСЃРµС‚СЊ.
        pid_file.write_text(f"{proc.pid}\n{int(time.time())}", encoding="utf-8")
        try:
            out_raw, err_raw = proc.communicate(timeout=SUBAGENT_TIMEOUT)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            # Р·Р°РІРёСЃС€РёР№ СЃСѓР±Р°РіРµРЅС‚: СѓР±РёС‚СЊ РІСЃС‘ РґРµСЂРµРІРѕ (Р±РµР· /T РѕСЃС‚Р°С‘С‚СЃСЏ node-СЃРёСЂРѕС‚Р°)
            _kill_tree(proc.pid)
            try:
                out_raw, err_raw = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                out_raw, err_raw = b"", b""
            rc = 124
        from pipeline.proc import smart_decode
        out, err = smart_decode(out_raw or b""), smart_decode(err_raw or b"")
        log_path.write_text((out or "") + (err or ""), encoding="utf-8")
        return rc
    except Exception as e:
        log_path.write_text(f"РћРЁРР‘РљРђ Р—РђРџРЈРЎРљРђ: {e}", encoding="utf-8")
        return 3
    finally:
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass


def run_subagent_session(cfg, task_id: str, report_path: Path, log_path: Path,
                         model: str = "", agent: str = "", skill: str = "", client=None,
                         worker: str = "", poll_sec: int = 20, prompt_override: str = "") -> int:
    """РЇРІРЅР°СЏ СЃРµСЃСЃРёСЏ: РёРЅСЃС‚СЂСѓРєС†РёСЏ/СЃС‚Р°С‚СѓСЃ вЂ” С‡РµСЂРµР· СЃРµСЂРІРµСЂ (РѕР±С‰РµРЅРёРµ, РЅРµ bash).

    РЎРѕР·РґР°С‘С‚ СЃРµСЃСЃРёСЋ РЅР° СЃРµСЂРІРµСЂРµ (POST /api/sessions) СЃ РїРѕР»РЅРѕР№ РёРЅСЃС‚СЂСѓРєС†РёРµР№,
    Р·Р°РїСѓСЃРєР°РµС‚ С‚РѕРЅРєРѕРіРѕ session_worker.py (РѕРЅ С‡РёС‚Р°РµС‚ РёРЅСЃС‚СЂСѓРєС†РёСЋ СЃ СЃРµСЂРІРµСЂР° Рё
    РѕС‚С‡РёС‚С‹РІР°РµС‚СЃСЏ С‡РµСЂРµР· СЃРµСЂРІРµСЂ), РјРѕРЅРёС‚РѕСЂРёС‚ СЃС‚Р°С‚СѓСЃ СЃРµСЃСЃРёРё РїРѕ API. Р’РѕР·РІСЂР°С‰Р°РµС‚ rc:
    0 вЂ” done+РѕС‚С‡С‘С‚; 1 вЂ” failed; 124 вЂ” killed/stalled/С‚Р°Р№РјР°СѓС‚; 2 вЂ” РЅРµС‚ Р·Р°РґР°С‡Рё."""
    task_file = _find_task_file(cfg, task_id)
    if not task_file:
        print(f"  [manager] Р·Р°РґР°С‡Р° {task_id} РЅРµ РЅР°Р№РґРµРЅР° РІ РђРєС‚РёРІРЅС‹Рµ (JSON Р·Р°РґР°С‡Рё)")
        return 2

    from pipeline.models import Task
    t = Task.from_file(task_file)
    if t.status == "open":
        t.set_status("in_progress")
        _publish(cfg, client, "task_started", task_id, {"file": task_file.name})

    prompt = _build_subprompt(cfg, task_id, task_file, report_path, worker=worker,
                              prompt_override=prompt_override)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    session = client.create_session(
        project=cfg.name, task=task_id, agent=f"session-{task_id}",
        role="qwen" if worker == "qwen" else "worker",
        model=model or DEFAULT_MODEL, skill=skill or worker,
        instruction={
            "task_file": str(task_file), "report": str(report_path),
            "log": str(log_path), "prompt": prompt, "model": model or DEFAULT_MODEL,
            "skill": skill or worker, "agent": agent or "", "worker": worker,
            "task_id": task_id,
        })
    if not session:
        print(f"  [manager] СЃРµСЃСЃРёСЏ {task_id} РќР• СЃРѕР·РґР°РЅР° (СЃРµСЂРІРµСЂ РЅРµРґРѕСЃС‚СѓРїРµРЅ?) вЂ” legacy")
        return run_subagent_legacy(cfg, task_id, report_path, log_path,
                                   model=model, agent=agent, skill=skill,
                                   client=client, worker=worker,
                                   prompt_override=prompt_override)
    sid = session["id"]
    print(f"  [manager] СЃСѓР±Р°РіРµРЅС‚ {task_id}: СЃРµСЃСЃРёСЏ {sid}"
          + (f" (model={session.get('model')})" if session.get("model") else "")
          + (f" (skill={skill})" if skill else "")
          + (f" (worker={worker})" if worker else ""))

    worker_script = DEV_PIPELINE_DIR / "agents" / "session_worker.py"
    cmd = [sys.executable, "-X", "utf8", str(worker_script),
           "--session", sid, "--url", SERVER_URL,
           "--project", cfg.name, "--cwd", str(cfg.root)]
    pid_file = log_path.parent / f"{task_id}.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    proc = None
    try:
        proc = subprocess.Popen(cmd, cwd=str(cfg.root),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=no_window_flags())
        pid_file.write_text(f"{proc.pid}\n{int(time.time())}", encoding="utf-8")
        # РјРѕРЅРёС‚РѕСЂРёРЅРі С‡РµСЂРµР· СЃРµСЂРІРµСЂ (РЅРµ С‡РµСЂРµР· stdout РїСЂРѕС†РµСЃСЃР°)
        deadline = time.time() + SUBAGENT_TIMEOUT
        terminal = ("done", "failed", "killed", "stalled")
        status = "created"
        while time.time() < deadline:
            cur = client.get_session(sid)
            if not cur:
                print(f"  [manager] {task_id}: СЃРµСЃСЃРёСЏ {sid} РёСЃС‡РµР·Р»Р° СЃ СЃРµСЂРІРµСЂР°")
                break
            status = cur.get("status", "created")
            if status in terminal:
                break
            time.sleep(poll_sec)
        else:
            print(f"  [manager] {task_id}: С‚Р°Р№РјР°СѓС‚ {SUBAGENT_TIMEOUT} СЃ вЂ” СѓР±РёРІР°СЋ СЃРµСЃСЃРёСЋ {sid}")
            client.session_kill(sid)
            _kill_tree(proc.pid)
            status = "killed"
        cur = client.get_session(sid) or {}
        final = cur.get("status", status)
        note = (cur.get("note") or "")[:300]
        if note:
            print(f"  [manager] {task_id}: {note}")
        if final == "done":
            return 0
        if final in ("failed", "killed", "stalled"):
            err = (cur.get("error") or "")[:400]
            print(f"  [manager] {task_id}: СЃРµСЃСЃРёСЏ {final}" + (f" ({err})" if err else ""))
            return 124 if final in ("killed", "stalled") else 1
        print(f"  [manager] {task_id}: СЃРµСЃСЃРёСЏ Р·Р°РІРµСЂС€РёР»Р°СЃСЊ СЃРѕ СЃС‚Р°С‚СѓСЃРѕРј {final}")
        return 124
    finally:
        if proc is not None:
            try:
                proc.wait(timeout=10)
            except Exception:
                _kill_tree(proc.pid)
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass


def run_subagent(cfg, task_id: str, report_path: Path, log_path: Path,
                 model: str = "", agent: str = "", skill: str = "", client=None,
                 worker: str = "", prompt_override: str = "") -> int:
    """Р—Р°РїСѓСЃС‚РёС‚СЊ СЃСѓР±Р°РіРµРЅС‚Р°. Р•СЃР»Рё СЃРµСЂРІРµСЂ РґРѕСЃС‚СѓРїРµРЅ вЂ” С‡РµСЂРµР· РЇР’РќРЈР® РЎР•РЎРЎРР®
    (РёРЅСЃС‚СЂСѓРєС†РёСЏ Рё СЃС‚Р°С‚СѓСЃС‹ С‡РµСЂРµР· СЃРµСЂРІРµСЂ), РёРЅР°С‡Рµ legacy opencode run РЅР°РїСЂСЏРјСѓСЋ."""
    if client is not None and client.server_alive(timeout=3.0):
        return run_subagent_session(cfg, task_id, report_path, log_path,
                                    model=model, agent=agent, skill=skill,
                                    client=client, worker=worker,
                                    prompt_override=prompt_override)
    print(f"  [manager] СЃРµСЂРІРµСЂ РЅРµРґРѕСЃС‚СѓРїРµРЅ вЂ” legacy opencode run ({task_id})")
    return run_subagent_legacy(cfg, task_id, report_path, log_path,
                               model=model, agent=agent, skill=skill,
                               client=client, worker=worker,
                               prompt_override=prompt_override)


def run_subagent_demo(cfg, task_id: str, report_path: Path):
    """Р—Р°РіР»СѓС€РєР° РґР»СЏ РїСЂРѕРІРµСЂРєРё С†РёРєР»Р° Р±РµР· СЂРµР°Р»СЊРЅРѕРіРѕ opencode."""
    from pipeline.models import Task
    task_file = None
    for f in glob.glob(str(cfg.abs_tasks_dir("active") / (task_id + "_*.md"))):
        task_file = Path(f)
        break
    if not task_file:
        return 2
    t = Task.from_file(task_file)
    if t.status == "open":
        t.set_status("in_progress")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        f"# РћРўР§РЃРў: {task_id} вЂ” РґРµРјРѕ-СЃСѓР±Р°РіРµРЅС‚ (РїСЂРѕРІРµСЂРєР° С†РёРєР»Р°)\n\n"
        f"**Р”Р°С‚Р°:** {now()} | **РЎС‚Р°С‚СѓСЃ:** done (РґРµРјРѕ)\n\n"
        "## Р§С‚Рѕ Р±С‹Р»Рѕ РЅРµ С‚Р°Рє\nР”РµРјРѕ-СЂРµР¶РёРј: СЂРµР°Р»СЊРЅРѕРµ РІС‹РїРѕР»РЅРµРЅРёРµ РЅРµ Р·Р°РїСѓСЃРєР°Р»РѕСЃСЊ.\n\n"
        "## Р§С‚Рѕ СЃРґРµР»Р°РЅРѕ\nРџСЂРѕРІРµСЂРµРЅ С†РёРєР» РјРµРЅРµРґР¶РµСЂР° (Р·Р°РґР°С‡Р° в†’ СЃСѓР±Р°РіРµРЅС‚ в†’ РѕС‚С‡С‘С‚).\n\n"
        "## Р”РѕРєР°Р·Р°С‚РµР»СЊСЃС‚РІР°\nР”РµРјРѕ: С„Р°Р№Р» РѕС‚С‡С‘С‚Р° СЃРѕР·РґР°РЅ; РєРѕРјР°РЅРґС‹ СЃР±РѕСЂРєРё РЅРµ Р·Р°РїСѓСЃРєР°Р»РёСЃСЊ.\n\n"
        "## РћС‚РєСЂС‹С‚С‹Рµ РІРѕРїСЂРѕСЃС‹\nР РµР°Р»СЊРЅРѕРµ РІС‹РїРѕР»РЅРµРЅРёРµ вЂ” С‡РµСЂРµР· СЂРµР°Р»СЊРЅРѕРіРѕ СЃСѓР±Р°РіРµРЅС‚Р°.\n\n"
        "## РљР°Рє РїРµСЂРµСЃРѕР±СЂР°С‚СЊ/РїСЂРѕРІРµСЂРёС‚СЊ\npython -m agents.agent_manager --project ... --mission ...",
        encoding="utf-8")
    t.set_status("done_report")
    print(f"  [manager] РґРµРјРѕ-РѕС‚С‡С‘С‚ {task_id}: {report_path}")
    return 0


def cmd_mission(args):
    cfg = load_config(args.project)
    mission = Path(args.mission)
    if not mission.exists():
        print("РњРРЎРЎРРЇ РќР• РќРђР™Р”Р•РќРђ:", mission)
        return 1
    for d in ("active", "reports"):
        cfg.abs_tasks_dir(d).mkdir(parents=True, exist_ok=True)
    title = args.title or mission.stem

    text = mission.read_text(encoding="utf-8")
    chunks = split_mission(text, args.split)
    total = len(chunks)
    print(f"[manager] РјРёСЃСЃРёСЏ '{title}': {total} РїРѕРґР·Р°РґР°С‡")

    ids = []
    for i, chunk in enumerate(chunks, start=1):
        tid = dispatch_chunk(cfg, chunk, i, total, title)
        ids.append(tid)

    _run_batch(cfg, ids, args)
    return 0


def cmd_task(args):
    cfg = load_config(args.project)
    for d in ("active", "reports"):
        cfg.abs_tasks_dir(d).mkdir(parents=True, exist_ok=True)
    _run_batch(cfg, [args.task], args)
    return 0


def _run_batch(cfg, ids, args):
    """Р—Р°РїСѓСЃС‚РёС‚СЊ СЃСѓР±Р°РіРµРЅС‚РѕРІ РїРѕ Р·Р°РґР°С‡Р°Рј (parallel/sequential/demo).

    РџРѕ СѓРјРѕР»С‡Р°РЅРёСЋ вЂ” РЇР’РќР«Р• РЎР•РЎРЎРР С‡РµСЂРµР· СЃРµСЂРІРµСЂ (РёРЅСЃС‚СЂСѓРєС†РёСЏ Рё СЃС‚Р°С‚СѓСЃС‹ С‡РµСЂРµР·
    API; session_worker.py вЂ” С‚РѕРЅРєРёР№ РєР»РёРµРЅС‚). --legacy РёР»Рё РЅРµРґРѕСЃС‚СѓРїРЅС‹Р№ СЃРµСЂРІРµСЂ вЂ”
    С„РѕР»Р±СЌРє РЅР° bash opencode run РЅР°РїСЂСЏРјСѓСЋ."""
    reports_dir = cfg.abs_tasks_dir("reports")
    logs_dir = cfg.root / "Tasks" / "РљРѕРЅРІРµР№РµСЂ" / "logs"
    results = {}
    model = getattr(args, "model", "") or DEFAULT_MODEL
    agent = getattr(args, "agent", "")
    skill = getattr(args, "skill", "")
    worker = getattr(args, "worker", "")
    legacy = bool(getattr(args, "legacy", False))
    # РџСѓР±Р»РёРєР°С†РёСЏ СЃРѕР±С‹С‚РёР№ РІ СЃРµСЂРІРµСЂ (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ): РїРѕР·РІРѕР»СЏРµС‚ РїР°РЅРµР»Рё РїРѕРєР°Р·С‹РІР°С‚СЊ С…РѕРґ Р·Р°РґР°С‡.
    client = None
    try:
        from pipeline.client import Client
        client = Client("agent-manager", project=cfg.name, base_url=SERVER_URL,
                        notif_dir=str(cfg.resolve(cfg.notif)))
    except Exception:
        client = None

    if args.demo:
        for tid in ids:
            report = reports_dir / f"{tid}_РћС‚С‡С‘С‚_{now()}.md"
            rc = run_subagent_demo(cfg, tid, report)
            results[tid] = rc
        _print_summary(cfg, ids, results)
        return

    def _one_subagent(tid):
        report = reports_dir / f"{tid}_РћС‚С‡С‘С‚_{now()}.md"
        log = logs_dir / f"{tid}_run.log"
        if legacy:
            rc = run_subagent_legacy(cfg, tid, report, log, model=model, agent=agent,
                                     skill=skill, client=client, worker=worker)
        else:
            rc = run_subagent(cfg, tid, report, log, model=model, agent=agent, skill=skill,
                              client=client, worker=worker)
        ok = _ensure_report(cfg, tid, rc)
        _publish(cfg, client, "subagent_finished", tid, {"rc": rc, "report": ok})
        return tid, rc, ok

    if args.sequential:
        for tid, rc, ok in (_one_subagent(tid) for tid in ids):
            results[tid] = rc
            print(f"  [manager] {tid}: rc={rc}, РѕС‚С‡С‘С‚={'РµСЃС‚СЊ' if ok else 'РќР•Рў'}")
    else:
        # parallel: Р·Р°РїСѓСЃРєР°РµРј РІСЃРµ СЂР°Р·РѕРј, Р¶РґС‘Рј РїРѕ РѕС‡РµСЂРµРґРё
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
            for tid, rc, ok in ex.map(_one_subagent, ids):
                results[tid] = rc
                print(f"  [manager] {tid}: rc={rc}, РѕС‚С‡С‘С‚={'РµСЃС‚СЊ' if ok else 'РќР•Рў'}")

    _print_summary(cfg, ids, results)


def _print_summary(cfg, ids, results):
    print("\n=== РЎР’РћР”РљРђ РњР•РќР•Р”Р–Р•Р Рђ ===")
    reports_dir = cfg.abs_tasks_dir("reports")
    for tid in ids:
        rc = results.get(tid)
        report = sorted(glob.glob(str(reports_dir / (tid + "_РћС‚С‡С‘С‚_*"))))
        status = "РћРљ" if (rc == 0 and report) else (f"rc={rc}" if rc else "РЅРµС‚ РѕС‚С‡С‘С‚Р°")
        print(f"  {tid}: {status}" + (f" -> {os.path.basename(report[-1])}" if report else ""))
    print("Р”Р°Р»СЊС€Рµ: РєРѕРЅС‚СЂРѕР»С‘СЂ Р·Р°РїСѓСЃРєР°РµС‚ verify РїРѕ РєР°Р¶РґРѕР№ Р·Р°РґР°С‡Рµ.")


def _ensure_report(cfg, tid: str, rc: int) -> bool:
    """РџСЂРѕРІРµСЂСЏРµС‚ РЅР°Р»РёС‡РёРµ РѕС‚С‡С‘С‚Р° РёСЃРїРѕР»РЅРёС‚РµР»СЏ. Р¤РµР№РєРѕРІС‹Р№ РѕС‚С‡С‘С‚ РќР• СЃРѕР·РґР°С‘С‚ вЂ”
    РѕРЅ РјР°СЃРєРёСЂСѓРµС‚ РѕР±СЂС‹РІС‹/Р·Р°РІРёСЃР°РЅРёСЏ СЃСѓР±Р°РіРµРЅС‚Р°. РџСЂРё rc != 0 РёР»Рё РѕС‚СЃСѓС‚СЃС‚РІРёРё
    РѕС‚С‡С‘С‚Р° вЂ” РїРѕРјРµС‡Р°РµС‚ Р·Р°РґР°С‡Сѓ stalled РІ history Рё РІРѕР·РІСЂР°С‰Р°РµС‚ False."""
    reports_dir = cfg.abs_tasks_dir("reports")
    if glob.glob(str(reports_dir / (tid + "_РћС‚С‡С‘С‚_*"))):
        return True
    _mark_stalled(cfg, tid,
                  f"СЃСѓР±Р°РіРµРЅС‚ rc={rc} Р±РµР· РѕС‚С‡С‘С‚Р° вЂ” РѕР±СЂС‹РІ/Р·Р°РІРёСЃР°РЅРёРµ СЃРµСЃСЃРёРё, РЅСѓР¶РµРЅ СЂРµРґРёСЃРїР°С‚С‡ "
                  f"(Р»РѕРі: Tasks\\РљРѕРЅРІРµР№РµСЂ\\logs\\{tid}_run.log)")
    return False


def _mark_stalled(cfg, tid: str, reason: str):
    """РџРѕРјРµС‚РєР° Р·Р°РІРёСЃР°РЅРёСЏ: С„Р°Р№Р»-РјР°СЂРєРµСЂ Tasks\\РљРѕРЅРІРµР№РµСЂ\\stalled\\<tid>.txt (С„Р°Р№Р»С‹ = РёСЃС‚РѕС‡РЅРёРє РїСЂР°РІРґС‹)."""
    try:
        d = cfg.root / "Tasks" / "РљРѕРЅРІРµР№РµСЂ" / "stalled"
        d.mkdir(parents=True, exist_ok=True)
        marker = d / f"{tid}.txt"
        if not marker.exists():
            marker.write_text(f"{now()}\n{reason}\n", encoding="utf-8")
        print(f"  [manager] {tid}: РїРѕРјРµС‚РєР° task_stalled вЂ” {reason}")
    except Exception as e:
        print(f"  [manager] stalled-РїРѕРјРµС‚РєР° {tid} РЅРµ СЃРѕС…СЂР°РЅРµРЅР°: {e}")


def cmd_report(args):
    """РћС‚С‡С‘С‚ РјРµРЅРµРґР¶РµСЂР°: СЃРІРѕРґРєР° РїРѕ С†РµР»СЏРј РїСЂРѕРµРєС‚Р° (Р·Р°РґР°С‡Рё РїРѕ СЃС‚Р°С‚СѓСЃР°Рј, РІРµСЂРґРёРєС‚С‹,
    СЂРµРєРѕРјРµРЅРґР°С†РёРё). РСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ РґР»СЏ РєРѕРЅС‚СЂРѕР»СЏ С†РµР»РµР№ РїСЂРѕРµРєС‚Р° РїРѕ РѕС‚С‡С‘С‚Сѓ РјРµРЅРµРґР¶РµСЂР°."""
    from pipeline.models import Task
    cfg = load_config(args.project)
    lines = [f"# РћРўР§РЃРў РњР•РќР•Р”Р–Р•Р Рђ: {cfg.name} вЂ” {now()}", ""]
    for key, label in [("inbox", "Р’С…РѕРґСЏС‰РёРµ"), ("active", "РђРєС‚РёРІРЅС‹Рµ"),
                       ("archive", "РђСЂС…РёРІ"), ("reports", "РћС‚С‡С‘С‚С‹")]:
        d = cfg.abs_tasks_dir(key)
        files = sorted(os.listdir(d)) if d.is_dir() else []
        a = [f for f in files if f.startswith("A-")]
        lines.append(f"## {label}: {len(a)}")
        for f in a:
            if key in ("inbox", "reports"):
                lines.append(f"- {f}")
                continue
            try:
                meta = Task.parse_frontmatter((d / f).read_text(encoding="utf-8"))
                lines.append(f"- {f} [СЃС‚Р°С‚СѓСЃ: {meta.get('СЃС‚Р°С‚СѓСЃ', '?')}]")
            except Exception:
                lines.append(f"- {f}")
        lines.append("")
    # Р’РµСЂРґРёРєС‚С‹ Рё СЃС‚Р°С‚СѓСЃС‹
    reports_dir = cfg.abs_tasks_dir("reports")
    verdicts = sorted(glob.glob(str(reports_dir / "*_Р’РµСЂРґРёРєС‚_*")), reverse=True)
    lines.append(f"## Р’РµСЂРґРёРєС‚С‹: {len(verdicts)}")
    for f in verdicts[:10]:
        lines.append(f"- {os.path.basename(f)}")
    lines.append("")
    # Р РµРєРѕРјРµРЅРґР°С†РёРё
    active = cfg.abs_tasks_dir("active")
    open_tasks = [f for f in os.listdir(active) if f.startswith("A-")] if active.is_dir() else []
    lines.append("## Р РµРєРѕРјРµРЅРґР°С†РёРё")
    if not open_tasks:
        lines.append("- РќРµС‚ Р°РєС‚РёРІРЅС‹С… Р·Р°РґР°С‡. РњРѕР¶РЅРѕ Р·Р°РїСѓСЃС‚РёС‚СЊ РЅРѕРІСѓСЋ РјРёСЃСЃРёСЋ.")
    else:
        lines.append(f"- РђРєС‚РёРІРЅС‹С… Р·Р°РґР°С‡: {len(open_tasks)}. Р—Р°РїСѓСЃС‚Рё СЃСѓР±Р°РіРµРЅС‚РѕРІ: "
                     f"python -m agents.agent_manager task --project {cfg.name} --task A-XX --sequential")
    text = "\n".join(lines)
    out = cfg.resolve(cfg.status).with_name("РћС‚С‡С‘С‚_РјРµРЅРµРґР¶РµСЂР°.md") \
        if Path(cfg.status).name == "РЎС‚Р°С‚СѓСЃ_РєРѕРЅРІРµР№РµСЂР°.md" else cfg.resolve(cfg.status)
    Path(out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agents.agent_manager")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("mission")
    p.add_argument("--project", default="meptaggingsolution")
    p.add_argument("--mission", required=True)
    p.add_argument("--split", type=int, default=3)
    p.add_argument("--title")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--sequential", action="store_true")
    p.add_argument("--parallel", type=int, default=2)
    p.add_argument("--model", default="", help="opencode-РјРѕРґРµР»СЊ (РЅР°РїСЂ. opencode/big-pickle-free)")
    p.add_argument("--agent", default="", help="СЂРѕР»СЊ opencode (--agent)")
    p.add_argument("--skill", default="", help="СЃРєРёР»Р», РєРѕС‚РѕСЂС‹Р№ СЃСѓР±Р°РіРµРЅС‚ РѕР±СЏР·Р°РЅ Р·Р°РіСЂСѓР·РёС‚СЊ")
    p.add_argument("--worker", default="", choices=["", "qwen"],
                   help="qwen вЂ” Р±РµСЃРїР»Р°С‚РЅС‹Р№ СЂР°Р±РѕС‡РёР№: РіРµРЅРµСЂР°С†РёСЋ С„Р°Р№Р»РѕРІ РґРµР»Р°РµС‚ РѕР±Р»Р°С‡РЅС‹Р№ Qwen")
    p.add_argument("--legacy", action="store_true",
                   help="Р±РµР· СЏРІРЅРѕР№ СЃРµСЃСЃРёРё: opencode run РЅР°РїСЂСЏРјСѓСЋ РёР· bash (С„РѕР»Р±СЌРє)")
    p.set_defaults(handler=cmd_mission)

    p = sub.add_parser("task")
    p.add_argument("--project", default="meptaggingsolution")
    p.add_argument("--task", required=True)
    p.add_argument("--demo", action="store_true")
    p.add_argument("--sequential", action="store_true")
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--model", default="")
    p.add_argument("--agent", default="")
    p.add_argument("--skill", default="")
    p.add_argument("--worker", default="", choices=["", "qwen"],
                   help="qwen вЂ” Р±РµСЃРїР»Р°С‚РЅС‹Р№ СЂР°Р±РѕС‡РёР№: РіРµРЅРµСЂР°С†РёСЋ С„Р°Р№Р»РѕРІ РґРµР»Р°РµС‚ РѕР±Р»Р°С‡РЅС‹Р№ Qwen")
    p.add_argument("--legacy", action="store_true",
                   help="Р±РµР· СЏРІРЅРѕР№ СЃРµСЃСЃРёРё: opencode run РЅР°РїСЂСЏРјСѓСЋ РёР· bash (С„РѕР»Р±СЌРє)")
    p.set_defaults(handler=cmd_task)

    p = sub.add_parser("report")
    p.add_argument("--project", default="meptaggingsolution")
    p.set_defaults(handler=cmd_report)

    args = ap.parse_args(argv)
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 0
    try:
        return args.handler(args)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"РћРЁРР‘РљРђ: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

