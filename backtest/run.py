#!/usr/bin/env python3
"""Backtest harness — replay finished games, STRICT pre-game cutoff (no look-ahead).

For each final game, every analysis module is recomputed using ONLY data from games
strictly before that game's date (before_date=game_date). Each module's signed score is
read as a moneyline pick (score>=0 => home, else away) and graded vs the actual result.
Also grades the consensus (modules fused -> home prob) on hit-rate, ROI and Brier.

Mirrors football-betting backtest/run.py. Pure logic + free data; no keys needed.

  python3 backtest/run.py                 # all final games
  python3 backtest/run.py --min-prior 1   # require N prior games to count
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.registry import MODULES  # noqa: E402
from data.db import connect  # noqa: E402
from engine.consensus import modules_to_home_prob  # noqa: E402


def actual_home_win(g):
    if g["home_score"] is None or g["away_score"] is None:
        return None
    return 1 if g["home_score"] > g["away_score"] else 0


def brier(prob_home, outcome_home):
    return (prob_home - outcome_home) ** 2


def backtest(con, flat_odds=1.91, stake=100, min_prior=0):
    games = con.execute(
        "SELECT * FROM games WHERE status='final' AND home_score IS NOT NULL "
        "ORDER BY game_date"
    ).fetchall()

    # per-module tallies: name -> [picks, hits, pnl]
    mod_tally = {name: [0, 0, 0.0] for name in MODULES}
    cons_tally = [0, 0, 0.0]
    cons_brier = []
    rows_out = []

    for g in games:
        gid = g["game_id"]
        outcome = actual_home_win(g)
        if outcome is None:
            continue
        # count prior final games (look-ahead guard / warmup)
        n_prior = con.execute(
            "SELECT COUNT(*) FROM games WHERE status='final' AND game_date < ?", (g["game_date"],)
        ).fetchone()[0]
        if n_prior < min_prior:
            continue

        # recompute every module with strict cutoff
        scores = {}
        for name, fn in MODULES.items():
            try:
                if name == "odds_value":
                    res = fn(gid, con, module_scores={k: {"score": v} for k, v in scores.items()},
                             before_date=g["game_date"])
                else:
                    res = fn(gid, con, before_date=g["game_date"])
            except Exception:
                res = {"score": 0.0, "confidence": 0.0}
            scores[name] = res["score"]
            # grade module as a moneyline pick
            pick_home = res["score"] >= 0
            # skip neutral (score==0 & no confidence) so we don't reward coin-flips
            if res["score"] == 0 and res.get("confidence", 0) < 0.15:
                continue
            won = (pick_home == bool(outcome))
            t = mod_tally[name]
            t[0] += 1
            t[1] += 1 if won else 0
            t[2] += stake * (flat_odds - 1) if won else -stake

        # consensus
        mrows = [{"module": k, "score": v, "confidence": 0.5} for k, v in scores.items()]
        home_prob, _ = modules_to_home_prob(mrows)
        cons_brier.append(brier(home_prob, outcome))
        cpick_home = home_prob >= 0.5
        cwon = (cpick_home == bool(outcome))
        cons_tally[0] += 1
        cons_tally[1] += 1 if cwon else 0
        cons_tally[2] += stake * (flat_odds - 1) if cwon else -stake
        rows_out.append((g["game_date"], g["away_team"], g["home_team"],
                         "HOME" if outcome else "AWAY", round(home_prob, 2),
                         "HOME" if cpick_home else "AWAY", "WIN" if cwon else "loss"))

    return mod_tally, cons_tally, cons_brier, rows_out


def _line(name, t, brier_val=None):
    picks, hits, pnl = t
    if picks == 0:
        return f"  {name:22} no graded picks"
    hr = hits / picks
    roi = pnl / (picks * 100)
    extra = f"  Brier {brier_val:.3f}" if brier_val is not None else ""
    return f"  {name:22} {hits}/{picks} ({hr:.0%})  ROI {roi:+.1%}  pnl {pnl:+.0f}{extra}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-prior", type=int, default=0)
    a = ap.parse_args()
    con = connect()
    mod_tally, cons_tally, cons_brier, rows = backtest(con, min_prior=a.min_prior)

    print("=== per-game consensus replay (strict pre-game cutoff) ===")
    for r in rows:
        print(f"  {r[0]}  {r[1]} @ {r[2]}  actual {r[3]}  | model p(home)={r[4]} pick {r[5]} -> {r[6]}")

    print("\n=== per-module hit-rate / ROI (flat 1.91, $100) ===")
    for name, t in mod_tally.items():
        print(_line(name, t))

    bsum = sum(cons_brier) / len(cons_brier) if cons_brier else None
    print("\n=== consensus ===")
    print(_line("consensus", cons_tally, bsum))
    print("\nNOTE: tiny sample (Finals only) — directional sanity check, not significance. "
          "Real edge validation needs a full season (Oct 2026+).")
