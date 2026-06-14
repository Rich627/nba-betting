#!/usr/bin/env python3
"""Module: player_ability — net same-position talent/form edge between the two teams.

Uses features/rolling (last-10 form) + features/zscore (same-position composite strength),
weighted by each player's recent minutes. Home roster strength minus away roster strength
-> signed score (+ favors home).

  python3 analysis/player_ability.py <game_id>
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis._common import clamp, empty, game_row, get_con  # noqa: E402
from features.rolling import load_player_games, rolling_form  # noqa: E402
from features.zscore import player_strength, position_zscores  # noqa: E402


def analyze(game_id, con=None, before_date=None, window=10):
    con = get_con(con)
    g = game_row(con, game_id)
    if not g:
        return empty(f"game {game_id} not found")
    home, away = g["home_team"], g["away_team"]

    # look-ahead-safe: only games before this game's date for backtests
    cutoff = before_date or g["game_date"]
    df = load_player_games(con, before_date=cutoff)
    if df.empty:
        return empty("no prior player-game data to assess ability")

    form = rolling_form(df, window=window)
    zdf = position_zscores(form)
    strength = player_strength(zdf)  # player_id -> composite z strength

    # minute-weight each team's roster strength using rolling minutes (recent role)
    def _m(v):
        try:
            return 0.0 if v is None or math.isnan(float(v)) else float(v)
        except (TypeError, ValueError):
            return 0.0

    min_by = {int(r["player_id"]): _m(r.get("r_min")) for _, r in form.iterrows()}
    team_by = {int(r["player_id"]): r["team"] for _, r in form.iterrows()}

    def team_strength(team):
        num, den = 0.0, 0.0
        for pid, s in strength.items():
            if team_by.get(pid) == team:
                w = float(min_by.get(pid, 0.0))
                num += w * s
                den += w
        return (num / den) if den else None

    sh, sa = team_strength(home), team_strength(away)
    if sh is None or sa is None:
        return empty(f"insufficient roster history for {home} or {away}")

    diff = sh - sa
    score = clamp(diff / 1.5)  # ~1.5 z-units of roster gap ≈ saturate
    confidence = clamp(0.4 + 0.3 * min(abs(diff), 1.5) / 1.5, 0, 0.9)
    md = (
        f"**Player ability (same-position, last-{window})** — "
        f"{home} minute-weighted roster strength {sh:+.2f} vs {away} {sa:+.2f} "
        f"(diff {diff:+.2f}). Score favors **{home if diff>=0 else away}**."
    )
    return {"score": round(score, 3), "confidence": round(confidence, 3), "reasoning_md": md}


if __name__ == "__main__":
    con = get_con(None)
    gid = sys.argv[1] if len(sys.argv) > 1 else (
        con.execute("SELECT game_id FROM games WHERE status='final' ORDER BY game_date DESC LIMIT 1").fetchone() or [None]
    )[0]
    print(f"game {gid}:")
    print(analyze(gid, con))
