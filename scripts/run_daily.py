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
    if not (ok and odk):
        print(" → add both keys to .env to run steps 2-5 (odds + models + edge + recs). Engine code is ready.")
        return
    print(" [2-5] keys present — wire fetch_odds + predict_all + edge.recommend here (next build step).")

if __name__ == "__main__":
    main()
