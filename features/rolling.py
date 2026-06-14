#!/usr/bin/env python3
"""Rolling form features — per-player last-N game averages (current state).

Mirrors football-betting features/rolling.py intent: a player's *current* form is
the last ~10 games, NOT the season average. Pure pandas over player_game_stats.

  python3 features/rolling.py            # smoke test on whatever's in nba.db
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import connect  # noqa: E402

# metrics we roll (box + advanced); only those present are used.
ROLL_METRICS = [
    "min", "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
    "plus_minus", "fg_pct", "fg3_pct", "ft_pct", "ts_pct", "efg_pct",
    "usg_pct", "off_rating", "def_rating", "net_rating", "pace", "pie",
]


def load_player_games(con, before_date=None):
    """Return player_game_stats as a DataFrame, optionally only games strictly
    BEFORE `before_date` (ISO YYYY-MM-DD) — for look-ahead-safe backtests."""
    sql = (
        "SELECT p.*, pl.name FROM player_game_stats p "
        "JOIN players pl ON pl.player_id=p.player_id"
    )
    args = ()
    if before_date:
        sql += " WHERE p.game_date < ?"
        args = (before_date,)
    df = pd.read_sql_query(sql, con, params=args)
    return df


def rolling_form(df, window=10):
    """Per-player rolling mean of the last `window` games (as of each player's
    most recent game in df). Returns one row per player_id with avg metrics +
    games_used. Sorted by game_date so the tail() is the most recent N."""
    if df.empty:
        return pd.DataFrame(columns=["player_id", "name", "position", "team", "games_used"])
    df = df.sort_values(["player_id", "game_date"])
    present = [m for m in ROLL_METRICS if m in df.columns]
    out = []
    for pid, grp in df.groupby("player_id"):
        recent = grp.tail(window)
        row = {
            "player_id": pid,
            "name": recent["name"].iloc[-1],
            "team": recent["team"].iloc[-1],
            "position": recent["position"].dropna().iloc[-1] if recent["position"].notna().any() else None,
            "games_used": len(recent),
        }
        for m in present:
            row[f"r_{m}"] = round(recent[m].astype(float).mean(skipna=True), 3) if recent[m].notna().any() else None
        out.append(row)
    return pd.DataFrame(out)


if __name__ == "__main__":
    con = connect()
    df = load_player_games(con)
    print(f"loaded {len(df)} player-game rows")
    form = rolling_form(df, window=10)
    print(f"rolling form for {len(form)} players (last-10):")
    cols = [c for c in ["name", "team", "position", "games_used", "r_pts", "r_reb", "r_ast", "r_ts_pct", "r_usg_pct"] if c in form.columns]
    if not form.empty:
        print(form.sort_values("r_pts", ascending=False, na_position="last")[cols].head(8).to_string(index=False))
    else:
        print("  (no player data — run data/fetch_players.py)")
