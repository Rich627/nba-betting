"""Shared helpers for analysis modules."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import connect  # noqa: E402


def get_con(con):
    return con if con is not None else connect()


def game_row(con, game_id):
    return con.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def empty(reason):
    return {"score": 0.0, "confidence": 0.0, "reasoning_md": reason}
