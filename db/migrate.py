#!/usr/bin/env python3
"""ADDITIVE schema migration runner for nba-betting.

Applies schema.sql (v1), db/schema_v2.sql (player/analysis layers) and
db/schema_v3.sql (live-season scheduler), then ADDs any missing columns listed in
ADD_COLUMNS. Never drops anything. Safe to run repeatedly (idempotent).

  python3 db/migrate.py
"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("NBA_DB", os.path.join(ROOT, "nba.db"))
SCHEMA_V1 = os.path.join(ROOT, "schema.sql")
SCHEMA_V2 = os.path.join(ROOT, "db", "schema_v2.sql")
SCHEMA_V3 = os.path.join(ROOT, "db", "schema_v3.sql")

# (table, column, type) — added only if the column is missing. NEVER drops.
ADD_COLUMNS = [
    # v2: rest / back-to-back
    ("games", "rest_days_home", "INTEGER"),
    ("games", "rest_days_away", "INTEGER"),
    ("games", "is_b2b_home", "INTEGER"),
    ("games", "is_b2b_away", "INTEGER"),
    ("team_game_stats", "rest_days", "INTEGER"),
    ("team_game_stats", "is_b2b", "INTEGER"),
    # v3: live-season scheduler (scripts/run_scheduler.py)
    ("games", "commence_time", "TEXT"),        # ISO8601 UTC tip-off
    ("games", "espn_event_id", "TEXT"),
    ("games", "odds_event_id", "TEXT"),
    ("games", "nba_game_id", "TEXT"),          # stats.nba.com id when nba_api is reachable
    ("games", "notified_at", "TEXT"),          # pre-game alert queued (gates repeats)
    ("games", "settled_notified_at", "TEXT"),  # settlement summary queued (gates repeats)
    ("games", "digest_at", "TEXT"),            # listed in a next-day schedule digest
    ("predictions", "odds", "REAL"),           # median price of the picked side AT prediction time
    ("predictions", "result", "TEXT"),         # WON / LOST / PUSH / UNGRADED
    ("predictions", "pnl", "REAL"),
    ("predictions", "settled_at", "TEXT"),
    ("recommendations", "odds", "REAL"),
    ("recommendations", "line", "REAL"),       # picked side's line (spread/total); NULL for moneyline
    ("recommendations", "result", "TEXT"),
    ("recommendations", "pnl", "REAL"),
    ("recommendations", "settled_at", "TEXT"),
    ("recommendations", "close_odds", "REAL"), # closing price of the picked side (CLV)
    # v3.1: ESPN news feed (data/fetch_news.py)
    ("prematch_news", "headline", "TEXT"),
    ("prematch_news", "published", "TEXT"),
    ("prematch_news", "url", "TEXT"),
    # 2026-11-06: bilingual site — the same reasoning in Traditional Chinese
    ("predictions", "reasoning_zh", "TEXT"),
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
    for path in (SCHEMA_V1, SCHEMA_V2, SCHEMA_V3):
        if os.path.exists(path):
            with open(path) as f:
                con.executescript(f.read())
    added = []
    for table, col, typ in ADD_COLUMNS:
        if not _table_exists(con, table):
            continue
        if col not in _columns(con, table):
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            added.append(f"{table}.{col}")
    # Indexes on columns that only exist after the ALTERs above.
    if _table_exists(con, "prematch_news"):
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_news_team_headline ON prematch_news(team, headline)")
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
