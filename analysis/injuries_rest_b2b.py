#!/usr/bin/env python3
"""Module: injuries_rest_b2b — FIRST-CLASS NBA edge (biggest line mover).

Combines two signals available pre-game:
  1. REST / BACK-TO-BACK: from games.rest_days_* / is_b2b_* (computed by data layer).
     A team on a b2b (or markedly less rest) is at a measurable disadvantage.
  2. INJURIES, weighted by role so losing a star hurts more.
     Live (no before_date): data.injuries — official NBA report first, ESPN fallback,
     ESPN season minutes per game. Needs no nba_api data (2026-11-06).
     Backtest (before_date): prematch_news.injuries_json + nba_api rolling minutes,
     so no injury news from after the cutoff leaks in.

  python3 analysis/injuries_rest_b2b.py <game_id>
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis._common import clamp, empty, game_row, get_con  # noqa: E402

# injury status -> probability the player effectively misses impact
_STATUS_W = {"out": 1.0, "doubtful": 0.75, "questionable": 0.35, "probable": 0.1, "gtd": 0.4}


def _rest_signal(g):
    """+ favors home: home better-rested / away on b2b => positive."""
    rh, ra = g["rest_days_home"], g["rest_days_away"]
    bh, ba = g["is_b2b_home"], g["is_b2b_away"]
    if rh is None and ra is None and bh is None and ba is None:
        return 0.0, "no rest data"
    s = 0.0
    notes = []
    if bh is not None and ba is not None and bh != ba:
        if ba == 1:
            s += 0.5
            notes.append(f"{g['away_team']} on a back-to-back")
        if bh == 1:
            s -= 0.5
            notes.append(f"{g['home_team']} on a back-to-back")
    if rh is not None and ra is not None:
        diff = rh - ra  # home extra rest days
        s += clamp(diff / 4.0, -0.5, 0.5)
        if diff != 0:
            notes.append(f"rest {rh}d vs {ra}d ({'home' if diff>0 else 'away'} fresher)")
    return clamp(s), "; ".join(notes) or "rest even"


def _live_injury_signal(con, g, snap=None):
    """+ favors home: away missing more expected rotation minutes => positive."""
    from data import injuries as INJ
    snap = snap or INJ.snapshot(con, g["game_id"])
    if not snap or not any(snap["teams"].values()):
        return 0.0, None
    away_hit = INJ.impact(snap["teams"].get(g["away_team"], []))
    home_hit = INJ.impact(snap["teams"].get(g["home_team"], []))
    net = (away_hit - home_hit) / 36.0
    note = f"injury impact: {g['away_team']} -{away_hit:.0f} min vs {g['home_team']} -{home_hit:.0f} min"
    return clamp(net), note


def _injury_signal(con, g, cutoff, window):
    """Backtest path. + favors home: away missing more minute-weighted talent => positive."""
    from features.rolling import load_player_games, rolling_form
    news = con.execute(
        "SELECT team,injuries_json FROM prematch_news WHERE game_id=?", (g["game_id"],)
    ).fetchall()
    if not news:
        return 0.0, None
    form = rolling_form(load_player_games(con, before_date=cutoff), window=window)
    def _m(v):
        try:
            return 0.0 if v is None or math.isnan(float(v)) else float(v)
        except (TypeError, ValueError):
            return 0.0

    min_by_name = {str(r["name"]).lower(): _m(r.get("r_min")) for _, r in form.iterrows()}

    def team_hit(team):
        total = 0.0
        for row in news:
            if row["team"] != team or not row["injuries_json"]:
                continue
            try:
                items = json.loads(row["injuries_json"])
            except (ValueError, TypeError):
                continue
            for it in items:
                name = str(it.get("player", "")).lower()
                status = str(it.get("status", "")).lower()
                w = _STATUS_W.get(status, 0.3)
                mins = min_by_name.get(name, 12.0)  # default mid-role if unknown
                total += w * mins
        return total

    away_hit = team_hit(g["away_team"])
    home_hit = team_hit(g["home_team"])
    # normalize by ~36 (a star's minutes); positive if away loses more
    net = (away_hit - home_hit) / 36.0
    note = f"injury impact: {g['away_team']} -{away_hit:.0f} min vs {g['home_team']} -{home_hit:.0f} min"
    return clamp(net), note


def analyze(game_id, con=None, before_date=None, window=10, injury_snapshot=None):
    con = get_con(con)
    g = game_row(con, game_id)
    if not g:
        return empty(f"game {game_id} not found")
    cutoff = before_date or g["game_date"]

    rest_s, rest_note = _rest_signal(g)
    if before_date is None:
        inj_s, inj_note = _live_injury_signal(con, g, injury_snapshot)
    else:
        inj_s, inj_note = _injury_signal(con, g, cutoff, window)

    have_inj = inj_note is not None
    # rest is the reliable always-present signal; injuries dominate when present
    if have_inj:
        score = clamp(0.55 * inj_s + 0.45 * rest_s)
        confidence = clamp(0.5 + 0.35 * min(abs(inj_s) + abs(rest_s), 1.0))
    else:
        score = clamp(rest_s)
        confidence = clamp(0.3 + 0.3 * abs(rest_s)) if rest_note != "no rest data" else 0.1

    parts = [f"rest: {rest_note}"]
    if have_inj:
        parts.append(inj_note)
    fav = g["home_team"] if score >= 0 else g["away_team"]
    md = "**Injuries / rest / b2b (key NBA edge)** — " + "; ".join(parts) + f". Favors **{fav}**."
    return {"score": round(score, 3), "confidence": round(confidence, 3), "reasoning_md": md}


if __name__ == "__main__":
    con = get_con(None)
    gid = sys.argv[1] if len(sys.argv) > 1 else (
        con.execute("SELECT game_id FROM games ORDER BY game_date DESC LIMIT 1").fetchone() or [None]
    )[0]
    print(f"game {gid}:")
    print(analyze(gid, con))
