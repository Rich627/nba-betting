"""Shared DB helpers for nba-betting (single source of truth for the SQLite path).

Every layer (data / features / analysis / backtest / engine) imports `connect()`
from here so they all hit the same nba.db and the same schema.
"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("NBA_DB", os.path.join(ROOT, "nba.db"))


def connect():
    """Connection with Row factory; ensures schema (v1 + v2) is present."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    ensure_schema(con)
    return con


def ensure_schema(con):
    """Idempotently apply the additive migration (v1 base + v2 layers + ALTERs)."""
    # local import to avoid a hard dependency cycle at import time
    import importlib.util

    mig_path = os.path.join(ROOT, "db", "migrate.py")
    spec = importlib.util.spec_from_file_location("nba_migrate", mig_path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    mig.migrate(con)


if __name__ == "__main__":
    con = connect()
    print(f"DB ready at {DB_PATH}")
    con.close()
