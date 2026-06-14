#!/usr/bin/env python3
"""ADDITIVE schema migration runner for nba-betting.

Applies db/schema_v2.sql (new tables) AND adds rest_days / is_b2b columns to the
existing P0 tables WITHOUT dropping anything. Safe to run repeatedly (idempotent).

  python3 db/migrate.py
"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "nba.db")
SCHEMA_V1 = os.path.join(ROOT, "schema.sql")
SCHEMA_V2 = os.path.join(ROOT, "db", "schema_v2.sql")

# (table, column, type) — added only if the column is missing. NEVER drops.
ADD_COLUMNS = [
    ("games", "rest_days_home", "INTEGER"),
    ("games", "rest_days_away", "INTEGER"),
    ("games", "is_b2b_home", "INTEGER"),
    ("games", "is_b2b_away", "INTEGER"),
    ("team_game_stats", "rest_days", "INTEGER"),
    ("team_game_stats", "is_b2b", "INTEGER"),
]


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def _columns(con, table):
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def _table_exists(con, table):
    r = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return r is not None


def migrate(con):
    # 1. ensure v1 base tables exist (no-op if present)
    if os.path.exists(SCHEMA_V1):
        with open(SCHEMA_V1) as f:
            con.executescript(f.read())
    # 2. apply v2 additive tables
    with open(SCHEMA_V2) as f:
        con.executescript(f.read())
    # 3. additive ALTERs for rest / b2b
    added = []
    for table, col, typ in ADD_COLUMNS:
        if not _table_exists(con, table):
            continue
        if col not in _columns(con, table):
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            added.append(f"{table}.{col}")
    con.commit()
    return added


if __name__ == "__main__":
    con = connect()
    added = migrate(con)
    print(f"migration applied -> {DB}")
    if added:
        print("  added columns:", ", ".join(added))
    else:
        print("  (no new columns; already migrated)")
    print("  tables now:")
    for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        print("   -", r["name"])
    con.close()
