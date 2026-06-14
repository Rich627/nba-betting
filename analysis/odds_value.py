#!/usr/bin/env python3
"""Module: odds_value — model-implied vs market-implied probability (the edge layer).

Reads stored moneyline odds for the game, de-vigs them to a market probability, and
compares to a model home-win probability derived from the OTHER analysis modules'
combined signal. score = how much value is on the home side (+) or away side (-).

If no odds are stored yet (the-odds-api key not set), returns neutral with a note.

  python3 analysis/odds_value.py <game_id>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis._common import clamp, empty, game_row, get_con  # noqa: E402


def _devig_two_way(p_home_raw, p_away_raw):
    tot = p_home_raw + p_away_raw
    return (p_home_raw / tot, p_away_raw / tot) if tot > 0 else (0.5, 0.5)


def _market_home_prob(con, game_id):
    """From odds table moneyline rows -> de-vigged home win prob, or None."""
    rows = con.execute(
        "SELECT selection,price FROM odds WHERE game_id=? AND market IN ('h2h','moneyline','ml')",
        (game_id,),
    ).fetchall()
    if not rows:
        return None
    g = game_row(con, game_id)
    home, away = g["home_team"], g["away_team"]
    ph = pa = None
    for r in rows:
        sel = str(r["selection"]).upper()
        price = r["price"]
        if price in (None, 0):
            continue
        imp = 1.0 / float(price)  # decimal odds assumed
        if sel in (home.upper(), "HOME"):
            ph = imp
        elif sel in (away.upper(), "AWAY"):
            pa = imp
    if ph is None or pa is None:
        return None
    return _devig_two_way(ph, pa)[0]


def model_home_prob_from_modules(module_scores):
    """Map combined module score (sum of signed scores) -> home win prob via logistic."""
    import math

    s = sum(v.get("score", 0.0) for v in module_scores.values() if isinstance(v, dict))
    return 1.0 / (1.0 + math.exp(-1.6 * s))  # gentle slope


def analyze(game_id, con=None, module_scores=None, **kw):
    con = get_con(con)
    g = game_row(con, game_id)
    if not g:
        return empty(f"game {game_id} not found")

    mkt = _market_home_prob(con, game_id)
    if mkt is None:
        return {
            "score": 0.0, "confidence": 0.1,
            "reasoning_md": "**Odds value** — no stored moneyline odds (set ODDS_API_KEY + run odds fetch). Neutral.",
        }

    # model prob: prefer modules passed in; else fall back to stored module_outputs
    if module_scores is None:
        rows = con.execute(
            "SELECT module,score,confidence FROM module_outputs WHERE game_id=? AND module!='odds_value'",
            (game_id,),
        ).fetchall()
        module_scores = {r["module"]: {"score": r["score"]} for r in rows}
    model_p = model_home_prob_from_modules(module_scores) if module_scores else 0.5

    edge = model_p - mkt  # + => value on home
    score = clamp(edge / 0.15)  # 15% prob edge saturates
    confidence = clamp(0.3 + 2.0 * min(abs(edge), 0.15))
    side = g["home_team"] if edge >= 0 else g["away_team"]
    md = (
        f"**Odds value** — model home-win {model_p:.0%} vs market {mkt:.0%} "
        f"(edge {edge:+.0%}). Value on **{side}**."
    )
    return {"score": round(score, 3), "confidence": round(confidence, 3), "reasoning_md": md}


if __name__ == "__main__":
    con = get_con(None)
    gid = sys.argv[1] if len(sys.argv) > 1 else (
        con.execute("SELECT game_id FROM games ORDER BY game_date DESC LIMIT 1").fetchone() or [None]
    )[0]
    print(f"game {gid}:")
    print(analyze(gid, con))
