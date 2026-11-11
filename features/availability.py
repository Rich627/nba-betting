#!/usr/bin/env python3
"""Availability features — who is actually expected to play tonight.

The rolling form + same-position z-scores (features/rolling.py, features/zscore.py) say
how good each player has been. This says how much of him the team gets tonight: each
player's recent minutes are scaled by P(plays) from the injury view the models see
(data/injuries.snapshot: official NBA report first, ESPN fallback). Out -> 0,
Doubtful -> 0.25, Questionable -> 0.6, not listed -> 1. Players are matched by
normalized name, so ESPN athlete ids never need to line up with the report.

Without a snapshot (backtests with before_date) every player counts fully — no
injury news from after the cutoff leaks in.

  python3 features/availability.py <game_id>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.injuries import norm_name, weight  # noqa: E402


def play_prob(snap, team):
    """{normalized name: P(plays)} for the players a team lists; unlisted players are 1.0."""
    if not snap:
        return {}
    return {norm_name(e["player"]): round(1.0 - weight(e["status"]), 3)
            for e in (snap.get("teams") or {}).get(team, [])}


def available_minutes(form, team, snap=None):
    """Rows of `form` (rolling_form output) for `team`, with columns:
    p_play (0..1) and exp_min = r_min * p_play (expected minutes tonight)."""
    rows = form[form["team"] == team].copy()
    if rows.empty:
        return rows
    probs = play_prob(snap, team)
    rows["p_play"] = [probs.get(norm_name(n), 1.0) for n in rows["name"]]
    rows["exp_min"] = rows["r_min"].astype(float).fillna(0.0) * rows["p_play"]
    return rows


def team_strength(zdf, strength, team, snap=None):
    """Availability-weighted roster strength: sum(exp_min * strength) / sum(exp_min) over the
    players expected to play. Returns (strength or None, rows with p_play/exp_min/strength)."""
    rows = available_minutes(zdf, team, snap)
    if rows.empty:
        return None, rows
    rows["strength"] = [strength.get(int(p), 0.0) for p in rows["player_id"]]
    den = float(rows["exp_min"].sum())
    if den <= 0:
        return None, rows
    return float((rows["exp_min"] * rows["strength"]).sum() / den), rows


if __name__ == "__main__":
    from data import injuries as INJ
    from data.db import connect
    from features.rolling import load_player_games, rolling_form
    from features.zscore import player_strength, position_zscores

    con = connect()
    gid = sys.argv[1]
    g = con.execute("SELECT * FROM games WHERE game_id=?", (gid,)).fetchone()
    snap = INJ.snapshot(con, gid)
    zdf = position_zscores(rolling_form(load_player_games(con, before_date=g["game_date"])))
    st = player_strength(zdf)
    for team in (g["home_team"], g["away_team"]):
        s, rows = team_strength(zdf, st, team, snap)
        print(f"{team}: strength {s}")
        if not rows.empty:
            print(rows.sort_values("exp_min", ascending=False)[
                ["name", "position", "games_used", "r_min", "p_play", "exp_min", "strength"]].head(10).to_string(index=False))
