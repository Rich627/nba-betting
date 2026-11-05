#!/usr/bin/env python3
"""Settlement + model track-record.

2026-11-05 rewrite. The previous version graded every moneyline pick at a hardcoded
flat 1.91. Models overwhelmingly pick favourites priced 1.3-1.7, so that inflated every
wallet — the MLB sibling's "GLM +2821" was this artifact, and re-scored at real prices
all seven MLB models lost. Here every pick is graded at the price the picked side
actually carried: predictions.odds (median across books, captured when the prediction
was made), falling back to the current stored median if that is missing. A pick with
no price at all stays UNGRADED and never enters the wallet.

  settle(con)                          grade predictions -> predictions.result/pnl,
                                       rebuild model_record, snapshot model_wallet_history
  grade_recommendations_for_game(con)  grade engine.edge recs (moneyline/spread/total)
  weights_from_record(con)             consensus weights, z-gated so noise stays uniform
"""
import argparse
import datetime
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import connect  # noqa: E402
from data.fetch_odds import game_odds_dict  # noqa: E402

STAKE = 100.0


def _final_games(con):
    return {r["game_id"]: r for r in con.execute(
        "SELECT game_id, home_team, away_team, home_score, away_score, commence_time FROM games "
        "WHERE status='final' AND home_score IS NOT NULL AND away_score IS NOT NULL"
    )}


def settle(con, stake=STAKE):
    """Grade every ungraded moneyline prediction on a final game at its real price,
    then rebuild model_record from the predictions table. Returns
    {model: [bets, wins, pnl]} for every model with at least one graded pick."""
    finals = _final_games(con)
    rows = con.execute(
        "SELECT rowid, game_id, model, pick, odds FROM predictions "
        "WHERE market='moneyline' AND settled_at IS NULL"
    ).fetchall()
    odds_cache = {}
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for p in rows:
        g = finals.get(p["game_id"])
        if not g:
            continue
        hs, as_ = g["home_score"], g["away_score"]
        if hs == as_:
            result, pnl = "PUSH", 0.0
        else:
            winner = g["home_team"] if hs > as_ else g["away_team"]
            price = p["odds"]
            if not price or price <= 1:
                if p["game_id"] not in odds_cache:
                    odds_cache[p["game_id"]] = game_odds_dict(con, p["game_id"])
                o = odds_cache[p["game_id"]]
                # Fallback only to a PRE-GAME snapshot; an in-play price is not a bet we could have made.
                pre_game = bool(o) and (not g["commence_time"] or (o.get("fetched_at") or "") < g["commence_time"])
                price = (o.get("ml_home") if p["pick"] == g["home_team"] else o.get("ml_away")) if pre_game else None
            if not price or price <= 1:
                result, pnl = "UNGRADED", None
            elif p["pick"] == winner:
                result, pnl = "WON", round(stake * (price - 1), 2)
            else:
                result, pnl = "LOST", -stake
        con.execute(
            "UPDATE predictions SET result=?, pnl=?, odds=COALESCE(odds, ?), settled_at=? WHERE rowid=?",
            (result, pnl, price if result in ("WON", "LOST") else None, now, p["rowid"]),
        )
    # Rebuild the wallet from graded picks (idempotent; UNGRADED/PUSH excluded from bets).
    rec = {}
    for r in con.execute(
        "SELECT model, COUNT(*) AS bets, SUM(result='WON') AS wins, SUM(pnl) AS pnl "
        "FROM predictions WHERE market='moneyline' AND result IN ('WON','LOST') GROUP BY model"
    ):
        rec[r["model"]] = [r["bets"], r["wins"], round(r["pnl"] or 0.0, 1)]
        con.execute(
            """INSERT INTO model_record(model,bets,wins,pnl,updated_at) VALUES(?,?,?,?,datetime('now'))
               ON CONFLICT(model) DO UPDATE SET bets=excluded.bets,wins=excluded.wins,pnl=excluded.pnl,
               updated_at=datetime('now')""",
            (r["model"], r["bets"], r["wins"], round(r["pnl"] or 0.0, 1)),
        )
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    for m, (b, w, pnl) in rec.items():
        con.execute(
            "INSERT OR REPLACE INTO model_wallet_history(as_of_date,model,bets,wins,pnl) VALUES(?,?,?,?,?)",
            (today, m, b, w, pnl),
        )
    con.commit()
    return rec


def grade_recommendations_for_game(con, game_id, stake=STAKE):
    """Grade every stored recommendation for one FINAL game and persist result/pnl.
    Returns [{market, pick, odds, result, pnl}]."""
    g = con.execute(
        "SELECT status, home_team, away_team, home_score, away_score FROM games WHERE game_id=?", (game_id,)
    ).fetchone()
    if not g or g["status"] != "final" or g["home_score"] is None or g["away_score"] is None:
        return []
    hs, as_ = g["home_score"], g["away_score"]
    margin, total = hs - as_, hs + as_
    actual_winner = "home" if hs > as_ else "away"
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    recs = con.execute(
        "SELECT rowid, market, pick, odds, line FROM recommendations WHERE game_id=?", (game_id,)
    ).fetchall()
    for r in recs:
        market, pick, odds, line = r["market"], r["pick"], r["odds"], r["line"]
        result = "UNGRADED"
        if market == "moneyline":
            result = "PUSH" if hs == as_ else ("WON" if pick == actual_winner else "LOST")
        elif market == "spread" and line is not None:
            side = pick.split(" ")[0]
            side_margin = margin if side == "home" else -margin
            v = side_margin + line
            result = "PUSH" if v == 0 else ("WON" if v > 0 else "LOST")
        elif market == "total" and line is not None:
            if total == line:
                result = "PUSH"
            else:
                result = "WON" if ((pick == "over") == (total > line)) else "LOST"
        if result == "WON" and odds and odds > 1:
            pnl = round(stake * (odds - 1), 2)
        elif result == "LOST":
            pnl = -stake
        elif result == "PUSH":
            pnl = 0.0
        else:
            pnl = None
        con.execute(
            "UPDATE recommendations SET result=?, pnl=?, settled_at=? WHERE rowid=?",
            (result, pnl, now, r["rowid"]),
        )
        out.append({"market": market, "pick": pick, "odds": odds, "line": line, "result": result, "pnl": pnl})
    con.commit()
    return out


def weights_from_record(con, z_gate=2.0):
    """Consensus weights from the track record, gated on statistical significance.
    Unless some model's hit rate deviates from 50% by >= z_gate standard errors with
    >= 30 graded bets, return {} (uniform) — otherwise noise would decide who gets
    heard (the MLB sibling once gave its worst model 0.004x the best's weight on a
    spread that 211 coin flips produce by chance)."""
    rows = con.execute("SELECT model,bets,wins,pnl FROM model_record").fetchall()
    if not rows:
        return {}

    def z(r):
        b = r["bets"] or 0
        if b < 30:
            return 0.0
        return abs(r["wins"] / b - 0.5) / math.sqrt(0.25 / b)

    if max(z(r) for r in rows) < z_gate:
        return {}
    mn = min(r["pnl"] for r in rows)
    shifted = {r["model"]: (r["pnl"] - mn + 1) for r in rows}
    tot = sum(shifted.values()) or 1
    return {m: round(v / tot * len(rows), 3) for m, v in shifted.items()}


def leaderboard(con):
    """[(model, bets, wins, pnl)] sorted by pnl desc."""
    return [tuple(r) for r in con.execute(
        "SELECT model,bets,wins,pnl FROM model_record WHERE bets>0 ORDER BY pnl DESC"
    )]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stake", type=float, default=STAKE)
    a = ap.parse_args()
    con = connect()
    rec = settle(con, stake=a.stake)
    if rec:
        print("model wallet (graded at real median prices):")
        for m, (b, w, pnl) in sorted(rec.items(), key=lambda x: -x[1][2]):
            print(f"  {m:10} {w}/{b} wins  pnl {pnl:+.0f}")
        print("weights:", weights_from_record(con) or "uniform (no significant record yet)")
    else:
        print("nothing graded yet — need final games with stored moneyline predictions + prices")
