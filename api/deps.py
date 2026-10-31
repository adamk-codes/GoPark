"""Shared FastAPI dependencies."""
from __future__ import annotations

from collections.abc import Iterator

from api import db


def get_conn() -> Iterator:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()
