#!/usr/bin/env python3
"""Orchestrator — one run: fetch data → odds → models → edge → store recs → settle.
Flow:
  1.  data/fetch_results.py   → completed games (free, no key)
  1b. data/fetch_players.py   → player-level data + rest/b2b (free, no key)
  1c. analysis/registry.py    → 4 analysis modules -> module_outputs (free, no key)
  1d. engine/consensus.py     → module-only consensus, always available (free, no key)
  2.  data/fetch_odds.py      → upcoming NBA odds (needs ODDS_API_KEY; empty result in
                                 off-season is NOT an error, just nothing to fetch)
  3.  models/predict.py       → 6-model LLM predictions for scheduled games w/o odds-gated
                                 markets skipped gracefully (needs OPENROUTER_API_KEY)
  4.  engine/edge.py          → weighted_consensus (weights from engine.settle) + recommend
  5.  store predictions + recs → nba.db (dashboard reads it)
  6.  engine/settle.py        → after games go final, grade + update model_record
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # optional; falls back to os.environ

ROOT = os.path.join(os.path.dirname(__file__), "..")
try:
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass


def main():
    ok = os.environ.get("OPENROUTER_API_KEY")
    odk = os.environ.get("ODDS_API_KEY")
    print("NBA predictor — daily run")
    print(" OPENROUTER_API_KEY:", "set (V)" if ok else "MISSING (X) (can't run models)")
    print(" ODDS_API_KEY:", "set (V)" if odk else "MISSING (X) (can't fetch odds)")

    # step 1 always works (free)
    from data import fetch_results
    con = fetch_results.db()
    n = fetch_results.fetch(con, "06/01/2026")
    print(f" [1] results: {n} completed games in DB")

    # step 1b: player-level data + rest/b2b (free, no key) — the new data layer
    from data import fetch_players
    from data.db import connect as _connect
    pc = _connect()
    okg, total, _ = fetch_players.backfill(pc, limit=None)
    fetch_players.compute_rest_b2b(pc)
    print(f" [1b] player stats: {total} rows across {okg} new games; rest/b2b computed")

    # step 1c: run the analysis modules for all final games (free) -> module_outputs
    from analysis.registry import run_all
    finals = [r[0] for r in pc.execute("SELECT game_id FROM games WHERE status='final'")]
    for gid in finals:
        run_all(gid, pc)
    print(f" [1c] analysis modules run for {len(finals)} games -> module_outputs")

    # step 1d: modules-only consensus (works WITHOUT any key)
    from engine.consensus import consensus_for_game
    print(" [1d] module-only consensus (no keys needed):")
    for gid in finals[:5]:
        c = consensus_for_game(gid, pc)
        print(f"      {c['away_team']} @ {c['home_team']}: p(home)={c['home_win_prob']} ({c['source']})")

    # step 2: upcoming odds (needs ODDS_API_KEY). An empty result in the off-season is
    # expected, not a failure — the-odds-api only lists basketball_nba once games are
    # actually scheduled (preseason/regular season).
    n_events = 0
    if odk:
        from data import fetch_odds
        n_events, n_rows, note = fetch_odds.fetch_upcoming(pc)
        print(f" [2] odds: {note}")
    else:
        print(" [2] odds: skipped (ODDS_API_KEY missing)")

    # step 3-5: predict + edge + store, only for games that actually need it
    # (status != 'final' — i.e. scheduled/live games with no result yet).
    upcoming = [dict(r) for r in pc.execute(
        "SELECT game_id,home_team,away_team FROM games WHERE status!='final'"
    )]
    if not ok:
        print(" [3-5] skipped (OPENROUTER_API_KEY missing) — module-only consensus above is the fallback.")
    elif not upcoming:
        print(" [3-5] no upcoming (non-final) games in DB to predict — nothing to do right now "
              "(expected in the NBA off-season; re-run once games are scheduled).")
    else:
        from engine import edge, settle
        from engine.consensus import modules_to_home_prob
        from data.fetch_odds import game_odds_dict
        from models.predict import game_dict_from_db, predict_all, to_home_perspective

        weights = settle.weights_from_record(pc)
        n_pred, n_rec = 0, 0
        for g in upcoming:
            gid, home = g["game_id"], g["home_team"]
            game = game_dict_from_db(gid, pc)
            if not game:
                continue
            odds = game_odds_dict(pc, gid)
            game["odds"] = odds or "n/a (no odds stored yet for this game)"
            preds = predict_all(game)
            home_preds = to_home_perspective(preds, home)

            for model, p in home_preds.items():
                if not isinstance(p, dict) or "error" in p:
                    continue
                pc.execute(
                    """INSERT INTO predictions(game_id,model,market,pick,win_prob,pred_margin,
                        pred_total,confidence,reasoning,reasoning_zh,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))
                       ON CONFLICT(game_id,model,market) DO UPDATE SET
                         pick=excluded.pick, win_prob=excluded.win_prob,
                         pred_margin=excluded.pred_margin, pred_total=excluded.pred_total,
                         confidence=excluded.confidence, reasoning=excluded.reasoning,
                         reasoning_zh=excluded.reasoning_zh,
                         created_at=excluded.created_at""",
                    (gid, model, "moneyline", p.get("winner"), p.get("win_prob"),
                     (p.get("pred_home_score", 0) or 0) - (p.get("pred_away_score", 0) or 0),
                     p.get("pred_total"), p.get("confidence"), p.get("reasoning"), p.get("reasoning_zh")),
                )
                n_pred += 1
            pc.commit()

            consensus = edge.weighted_consensus(home_preds, weights=weights)
            if not consensus or not odds:
                continue
            recs = edge.recommend(consensus, odds)
            for r in recs:
                pc.execute(
                    """INSERT INTO recommendations(game_id,market,pick,edge,confidence,why,created_at)
                       VALUES(?,?,?,?,?,?,datetime('now'))
                       ON CONFLICT(game_id,market,pick) DO UPDATE SET
                         edge=excluded.edge, confidence=excluded.confidence,
                         why=excluded.why, created_at=excluded.created_at""",
                    (gid, r["market"], r["pick"], r.get("edge"), r["confidence"], r["why"]),
                )
                n_rec += 1
            pc.commit()
        print(f" [3-5] predicted {len(upcoming)} game(s): {n_pred} model predictions, {n_rec} recommendations stored.")

    # step 6: settle any final games against stored moneyline predictions -> model_record
    from engine.settle import settle
    rec = settle(pc)
    if rec:
        print(f" [6] settled {len(rec)} model(s) vs final results -> model_record updated.")
    else:
        print(" [6] nothing to settle (no final games with stored moneyline predictions).")


if __name__ == "__main__":
    main()
