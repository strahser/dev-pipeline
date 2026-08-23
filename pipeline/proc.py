# -*- coding: utf-8 -*-
"""Утилиты запуска процессов: без всплывающих окон консоли (Windows),
умное декодирование вывода дочерних процессов."""
from __future__ import annotations

import locale
import os
import subprocess
import unicodedata


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


def _cyr_score(txt: str) -> int:
    """Чем правдоподобнее русский текст, тем выше: кириллица плюс, управляющие
    мусорные символы (псевдографика/нули) минус."""
    cyr = sum(1 for ch in txt if "\u0400" <= ch <= "\u04FF")
    bad = sum(1 for ch in txt
              if unicodedata.category(ch).startswith("C") and ch not in "\t\n\r")
    return cyr * 2 - bad * 10


def smart_decode(data) -> str:
    """Декодирование вывода дочерних процессов без кракозябр (карточка 3.2).

    Дети пишут в разных кодировках: opencode/python — utf-8, cmd-утилиты — OEM
    (cp866 на русской Windows), часть инструментов — ANSI (cp1251). Перебираем
    строгие декодирования и выбираем лучший скоринг (кириллические слова важнее
    «успешного», но бессмысленного декодирования); совсем мусор — utf-8 c replace."""
    if isinstance(data, str):
        return data
    if not data:
        return ""
    preferred = locale.getpreferredencoding(False) or ""
    best_txt, best_score = None, None
    seen = set()
    for enc in ("utf-8", preferred, "cp866", "cp1251"):
        enc = (enc or "").lower()
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            txt = data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
        score = _cyr_score(txt)
        if best_score is None or score > best_score:
            best_txt, best_score = txt, score
        if score >= 0 and enc == "utf-8":
            return txt          # валидный utf-8 без мусора — почти наверняка он
    if best_txt is not None:
        return best_txt
    return data.decode("utf-8", errors="replace")
