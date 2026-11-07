#!/usr/bin/env python3
"""Replay the edge engine over the stored model predictions, read-only.

For every game with predictions and odds, prints the recommendations the CURRENT engine
rules would make next to the recommendations actually stored (made by the engine as it
was at prediction time), and grades both against the final score when there is one.
Nothing is written. Usage: python3 scripts/replay_edge.py [--db nba.db] [--since YYYY-MM-DD]
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from data.fetch_odds import game_odds_dict  # noqa: E402
from engine import edge  # noqa: E402
from engine.settle import STAKE  # noqa: E402


def grade(rec, g):
    if g["status"] != "final" or g["home_score"] is None:
        return None, None
    hs, as_ = g["home_score"], g["away_score"]
    margin, total = hs - as_, hs + as_
    m, pick, line = rec["market"], rec["pick"], rec.get("line")
    if m == "moneyline":
        v = margin if pick == "home" else -margin
    elif m == "spread" and line is not None:
        v = (margin if pick.split(" ")[0] == "home" else -margin) + line
    elif m == "total" and line is not None:
        v = (total - line) if pick == "over" else (line - total)
    else:
        return "UNGRADED", None
    if v == 0:
        return "PUSH", 0.0
    if v > 0:
        return "WON", round(STAKE * (rec["odds"] - 1), 1) if rec.get("odds") else None
    return "LOST", -STAKE


def fmt(rec, result, pnl):
    s = f"{rec['market']} {rec['pick']}"
    if rec.get("odds"):
        s += f" @{rec['odds']:.2f}"
    if rec.get("edge") is not None:
        s += f" EV{rec['edge']:+.0%}"
    if result:
        s += f" -> {result}" + (f" {pnl:+.0f}" if pnl is not None else "")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "nba.db"))
    ap.add_argument("--since", default=None)
    a = ap.parse_args()
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    games = con.execute(
        "SELECT * FROM games WHERE game_id IN (SELECT DISTINCT game_id FROM predictions) "
        "AND (? IS NULL OR game_date >= ?) ORDER BY game_id", (a.since, a.since)).fetchall()
    tot = {"old": [0, 0.0], "new": [0, 0.0]}
    for g in games:
        gid = g["game_id"]
        preds = {}
        for p in con.execute("SELECT model, win_prob, pred_margin, pred_total FROM predictions "
                             "WHERE game_id=? AND market='moneyline'", (gid,)):
            if p["win_prob"] is None:
                continue
            t = p["pred_total"] or 0.0
            m = p["pred_margin"] or 0.0
            preds[p["model"]] = {"home_win_prob": p["win_prob"], "pred_home_score": (t + m) / 2,
                                 "pred_away_score": (t - m) / 2, "pred_total": t}
        odds = game_odds_dict(con, gid)
        c = edge.weighted_consensus(preds)
        score = f"{g['away_score']}-{g['home_score']}" if g["home_score"] is not None else g["status"]
        if not c:
            print(f"\n{gid}  {score}  no usable predictions")
            continue
        mp = edge.market_home_prob(odds)
        market = f"market home {mp:.0%}" if mp is not None else "(no two-sided moneyline)"
        print(f"\n{gid}  {score}  models home {c['home_win_prob']:.0%} agree {c['agreement']:.0%}  {market}")
        old = [dict(r) for r in con.execute(
            "SELECT market,pick,odds,line,edge,result,pnl FROM recommendations WHERE game_id=?", (gid,))]
        new = edge.recommend(c, odds) if c else []
        for label, recs in (("old", old), ("new", new)):
            if not recs:
                print(f"  {label}: no bet")
            for r in recs:
                result, pnl = grade(r, g)
                if result in ("WON", "LOST", "PUSH"):
                    tot[label][0] += 1
                    tot[label][1] += pnl or 0.0
                print(f"  {label}: {fmt(r, result, pnl)}")
    print(f"\ngraded bets: old {tot['old'][0]} ({tot['old'][1]:+.0f})  new {tot['new'][0]} ({tot['new'][1]:+.0f})"
          f"  stake {STAKE}/bet")


if __name__ == "__main__":
    main()
