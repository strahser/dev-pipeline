# -*- coding: utf-8 -*-
"""Smoke-тест CLI: dispatch -> задача -> status (на временном проекте, без коммитов)."""
from __future__ import annotations

import argparse
import glob
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import cli          # noqa: E402
from pipeline.config import load_config  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pipeline_cli_"))
    for sub in ["Входящие", "Активные", "Отчёты", "Архив", "Конвейер", "Конвейер/Уведомления"]:
        (tmp / "Tasks" / sub).mkdir(parents=True)
    (tmp / "Входящий.txt").write_text("Тестовая задача для dispatch", encoding="utf-8")

    cfg_dir = Path(__file__).resolve().parent.parent / "examples" / "_cli"
    cfg_dir.mkdir(exist_ok=True)
    root = tmp.as_posix().replace("\\", "/")
    cfg_dir.joinpath("pipeline.yaml").write_text(
        "project:\n  name: _cli\n  root: " + root + "\n"
        "build:\n  msbuild: \"\"\n  sln: \"\"\n"
        "tests:\n  runner: vstest\n",
        encoding="utf-8")

    cfg = load_config("_cli")
    ns = argparse.Namespace(file=str(tmp / "Входящий.txt"), title="Тест",
                            priority="высокий", requirements=None, result=None,
                            remark=None, id=None)
    rc = cli.cmd_dispatch(cfg, ns)
    tasks = glob.glob(str(tmp / "Tasks" / "Активные" / "A-*.md"))
    assert rc == 0 and len(tasks) == 1, f"dispatch failed rc={rc} tasks={tasks}"
    print("задача создана:", tasks[0])

    rc2 = cli.cmd_status(cfg, argparse.Namespace())
    assert rc2 == 0, f"status failed rc={rc2}"
    assert (tmp / "Tasks" / "Статус_конвейера.md").exists()
    print("status ок")

    # Карточка 4.2: CLI perms write -> файл прав перезаписан на write
    rc3 = cli.cmd_perms(cfg, argparse.Namespace(mode="write"))
    assert rc3 == 0, f"perms write failed rc={rc3}"
    perm = tmp / ".opencode" / "permissions.json"
    assert perm.exists(), "permissions.json не создан"
    assert '"allow"' in perm.read_text(encoding="utf-8"), "режим write не применён"
    # невалидный mode -> rc != 0
    rc4 = cli.cmd_perms(cfg, argparse.Namespace(mode="admin"))
    assert rc4 != 0, f"невалидный mode должен завершиться ошибкой, rc={rc4}"
    print("perms ок")

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(cfg_dir, ignore_errors=True)
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
