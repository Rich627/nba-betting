#!/usr/bin/env python3
"""Module: lineup_pace_matchup — style/pace matchup from team-level rolling ratings.

Builds each team's recent offensive rating, defensive rating and pace (minute-weighted
from player_game_stats) and scores the matchup: a team whose offense outperforms the
opponent's defense, adjusted for pace, gets the edge.

  python3 analysis/lineup_pace_matchup.py <game_id>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis._common import clamp, empty, game_row, get_con  # noqa: E402
from features.rolling import load_player_games, rolling_form  # noqa: E402


def _team_profile(form, team):
    """Minute-weighted ORTG/DRTG/pace for a team from rolling player form."""
    rows = form[form["team"] == team]
    if rows.empty:
        return None
    w = rows["r_min"].astype(float).fillna(0.0)
    if w.sum() == 0:
        return None

    def wm(col):
        if col not in rows.columns or rows[col].notna().sum() == 0:
            return None
        vals = rows[col].astype(float)
        mask = vals.notna() & (w > 0)
        if mask.sum() == 0:
            return None
        return float((vals[mask] * w[mask]).sum() / w[mask].sum())

    return {"ortg": wm("r_off_rating"), "drtg": wm("r_def_rating"), "pace": wm("r_pace")}


def analyze(game_id, con=None, before_date=None, window=10):
    con = get_con(con)
    g = game_row(con, game_id)
    if not g:
        return empty(f"game {game_id} not found")
    home, away = g["home_team"], g["away_team"]
    cutoff = before_date or g["game_date"]
    form = rolling_form(load_player_games(con, before_date=cutoff), window=window)
    if form.empty:
        return empty("no prior data for pace/matchup")

    ph, pa = _team_profile(form, home), _team_profile(form, away)
    if not ph or not pa or None in (ph["ortg"], pa["ortg"], ph["drtg"], pa["drtg"]):
        return empty("insufficient rating data for matchup")

    # net efficiency edge: (home offense vs away defense) - (away offense vs home defense)
    home_attack = ph["ortg"] - pa["drtg"]
    away_attack = pa["ortg"] - ph["drtg"]
    net = home_attack - away_attack  # >0 favors home
    score = clamp(net / 12.0)  # ~12 pts/100 poss net edge saturates

    pace_note = ""
    if ph["pace"] and pa["pace"]:
        gap = abs(ph["pace"] - pa["pace"])
        faster = home if ph["pace"] > pa["pace"] else away
        pace_note = f" Pace {ph['pace']:.0f} vs {pa['pace']:.0f} ({faster} pushes tempo, gap {gap:.0f})."

    confidence = clamp(0.35 + 0.4 * min(abs(net), 12) / 12, 0, 0.85)
    md = (
        f"**Lineup/pace matchup** — {home} eff edge {home_attack:+.1f} vs {away} {away_attack:+.1f} "
        f"(net {net:+.1f} per 100).{pace_note} Favors **{home if net>=0 else away}**."
    )
    return {"score": round(score, 3), "confidence": round(confidence, 3), "reasoning_md": md}


if __name__ == "__main__":
    con = get_con(None)
    gid = sys.argv[1] if len(sys.argv) > 1 else (
        con.execute("SELECT game_id FROM games WHERE status='final' ORDER BY game_date DESC LIMIT 1").fetchone() or [None]
    )[0]
    print(f"game {gid}:")
    print(analyze(gid, con))
