# -*- coding: utf-8 -*-
"""Запуск сервера координации: python -m server [--host 127.0.0.1] [--port 8787]."""
from __future__ import annotations

import argparse

import uvicorn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    uvicorn.run("server.app:app", host=a.host, port=a.port, reload=a.reload)


if __name__ == "__main__":
    main()
