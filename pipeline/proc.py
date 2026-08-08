# -*- coding: utf-8 -*-
"""Утилиты запуска процессов: без всплывающих окон консоли (Windows)."""
from __future__ import annotations

import os
import subprocess


def no_window_flags() -> int:
    """CREATE_NO_WINDOW на Windows (дочерние процессы не открывают консоль),
    иначе 0. Флаг нужен ВСЕМ Popen/subprocess.run, иначе при каждом запуске
    субагента/сборки мелькает окно терминала."""
    if os.name == "nt":
        try:
            return subprocess.CREATE_NO_WINDOW  # 0x08000000
        except AttributeError:
            return 0
    return 0


def popen(cmd, **kwargs) -> subprocess.Popen:
    """subprocess.Popen с подавлением консольного окна (Windows)."""
    kwargs.setdefault("creationflags", no_window_flags())
    return subprocess.Popen(cmd, **kwargs)


def run(cmd, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run с подавлением консольного окна (Windows)."""
    kwargs.setdefault("creationflags", no_window_flags())
    return subprocess.run(cmd, **kwargs)
