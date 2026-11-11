#!/usr/bin/env python3
"""Module: player_ability — net same-position talent/form edge between the two teams,
counting only the players expected to play.

Feature chain (same as ~/football-betting): data/fetch_players.py (ESPN box per game)
-> features/rolling (last-10 form per player) -> features/zscore (each player z-scored
against his own position group, G / F / C, then a composite strength) ->
features/availability (recent minutes x P(plays) from the injury snapshot). Home
availability-weighted roster strength minus away -> signed score (+ favors home).

  python3 analysis/player_ability.py <game_id>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis._common import clamp, empty, game_row, get_con  # noqa: E402
from features.availability import team_strength  # noqa: E402
from features.rolling import load_player_games, rolling_form  # noqa: E402
from features.zscore import player_strength, position_zscores  # noqa: E402

MIN_GAMES = 3   # a roster read off fewer games per player is mostly noise


def _missing(rows, n=2):
    """Biggest expected-minute losses (p_play < 1), strongest first."""
    lost = rows[rows["p_play"] < 1.0].copy()
    if lost.empty:
        return []
    lost["lost_min"] = lost["r_min"].astype(float).fillna(0.0) * (1.0 - lost["p_play"])
    lost = lost[lost["lost_min"] >= 8].sort_values("lost_min", ascending=False).head(n)
    return [f"{r['name']} ({r['position'] or '?'}, z {r['strength']:+.2f}, -{r['lost_min']:.0f} min)"
            for _, r in lost.iterrows()]


def _leaders(rows, n=2):
    top = rows[rows["exp_min"] >= 15].sort_values("strength", ascending=False).head(n)
    return [f"{r['name']} {r['position'] or '?'} z {r['strength']:+.2f}" for _, r in top.iterrows()]


def analyze(game_id, con=None, before_date=None, window=10, injury_snapshot=None):
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
    # live runs see who is expected to play; backtests (before_date) count everyone
    snap = injury_snapshot
    if snap is None and before_date is None:
        try:
            from data import injuries as INJ
            snap = INJ.snapshot(con, game_id)
        except Exception:  # noqa: BLE001
            snap = None

    form = rolling_form(df, window=window)
    zdf = position_zscores(form)
    strength = player_strength(zdf)  # player_id -> composite same-position z strength

    sh, rh = team_strength(zdf, strength, home, snap)
    sa, ra = team_strength(zdf, strength, away, snap)
    if sh is None or sa is None:
        return empty(f"insufficient roster history for {home} or {away}")
    depth = min(float(rh["games_used"].median()), float(ra["games_used"].median()))
    if depth < MIN_GAMES:
        return empty(f"only {depth:.0f} prior games per player; too few for a roster read")

    diff = sh - sa
    score = clamp(diff / 1.5)  # ~1.5 z-units of roster gap ≈ saturate
    confidence = clamp(0.4 + 0.3 * min(abs(diff), 1.5) / 1.5, 0, 0.9) * min(1.0, depth / window)
    parts = []
    for team, s, rows in ((home, sh, rh), (away, sa, ra)):
        miss = _missing(rows)
        parts.append(f"{team} {s:+.2f} (top: {', '.join(_leaders(rows)) or 'n/a'}"
                     + (f"; missing: {', '.join(miss)}" if miss else "") + ")")
    md = (
        f"**Player ability (same-position z, last-{window}, availability-weighted)** — "
        f"{parts[0]} vs {parts[1]}; diff {diff:+.2f}. Score favors **{home if diff >= 0 else away}**."
    )
    return {"score": round(score, 3), "confidence": round(confidence, 3), "reasoning_md": md}


if __name__ == "__main__":
    con = get_con(None)
    gid = sys.argv[1] if len(sys.argv) > 1 else (
        con.execute("SELECT game_id FROM games WHERE status='final' ORDER BY game_date DESC LIMIT 1").fetchone() or [None]
    )[0]
    print(f"game {gid}:")
    print(analyze(gid, con))
