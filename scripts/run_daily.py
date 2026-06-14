#!/usr/bin/env python3
"""Orchestrator (skeleton) — one run: fetch data → run models → edge → store recs → (dashboard reads DB).
End-to-end live run needs OPENROUTER_API_KEY + ODDS_API_KEY in .env. Without keys it explains what's missing.
Flow:
  1. data/fetch_results.py  → completed games (track-record settling)
  2. fetch upcoming games + odds (the-odds-api)   [TODO: data/fetch_odds.py once ODDS_API_KEY set]
  3. models.predict.predict_all(game)  → 6-model predictions  [needs OPENROUTER_API_KEY]
  4. engine.edge.weighted_consensus + recommend (weights from engine.settle.weights_from_record)
  5. store predictions + recs to DB → dashboard (frontend/app.py) shows them
  6. engine.settle after finals → update model_record
"""
import os, sys, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # optional; falls back to os.environ
ROOT = os.path.join(os.path.dirname(__file__), "..")
try: load_dotenv(os.path.join(ROOT, ".env"))
except Exception: pass

def main():
    ok = os.environ.get("OPENROUTER_API_KEY"); odk = os.environ.get("ODDS_API_KEY")
    print("NBA predictor — daily run")
    print(" OPENROUTER_API_KEY:", "set ✓" if ok else "MISSING ✗ (can't run models)")
    print(" ODDS_API_KEY:", "set ✓" if odk else "MISSING ✗ (can't fetch odds)")
    # step 1 always works (free)
    from data import fetch_results
    con = fetch_results.db(); n = fetch_results.fetch(con, "06/01/2026")
    print(f" [1] results: {n} completed games in DB")

    # step 1b: player-level data + rest/b2b (free, no key) — the new data layer
    from data import fetch_players
    from data.db import connect as _connect
    pc = _connect()
    okg, total = fetch_players.backfill(pc, limit=None)
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

    if not (ok and odk):
        print(" → add OPENROUTER_API_KEY (LLM blend) + ODDS_API_KEY (odds/edge) to .env for full live recs.")
        print("   Layered analysis + module consensus already run above on free data.")
        return
    print(" [2-5] keys present — wire fetch_odds + predict_all (consumes module_outputs) + edge.recommend.")

if __name__ == "__main__":
    main()
