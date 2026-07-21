#!/usr/bin/env python3
"""Consensus layer — fuse analysis-module outputs (+ optional LLM model preds) into a
single home-win probability the edge engine can act on.

Two inputs, both optional:
  - module_outputs (analysis/*): always available (free, no key)
  - LLM model predictions (models/predict via OpenRouter): only if OPENROUTER_API_KEY set

This is the bridge between the new layered analysis and the existing engine/edge.py.
Pure logic; graceful when the LLM key is absent.

  python3 engine/consensus.py <game_id>
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import connect  # noqa: E402

# relative trust per module when fusing into a probability (tunable; backtest to set)
MODULE_WEIGHTS = {
    "injuries_rest_b2b": 1.4,   # biggest NBA line mover
    "player_ability": 1.2,
    "lineup_pace_matchup": 1.0,
    "odds_value": 0.8,
}


def modules_to_home_prob(module_rows):
    """module_rows: list of {module, score, confidence}. Returns home win prob 0..1.
    Confidence-and-weight-weighted sum of signed scores -> logistic."""
    num = 0.0
    den = 0.0
    for r in module_rows:
        w = MODULE_WEIGHTS.get(r["module"], 1.0) * float(r.get("confidence", 0.5) or 0.0)
        num += w * float(r.get("score", 0.0) or 0.0)
        den += w
    s = (num / den) if den else 0.0
    # scale: combined signal ~[-1,1] -> probability via gentle logistic
    return 1.0 / (1.0 + math.exp(-2.2 * s)), s


def consensus_for_game(game_id, con=None, llm_preds=None):
    """Combine stored module_outputs with optional LLM predictions.
    Returns dict consumable by engine.edge.recommend (home perspective)."""
    con = con or connect()
    rows = [dict(r) for r in con.execute(
        "SELECT module,score,confidence FROM module_outputs WHERE game_id=?", (game_id,)
    )]
    mod_prob, signal = modules_to_home_prob(rows) if rows else (0.5, 0.0)

    # optional LLM consensus (home win prob) — blend 50/50 if present
    llm_prob = None
    if llm_preds:
        try:
            from engine.edge import weighted_consensus
            c = weighted_consensus(llm_preds)
            if c:
                llm_prob = c["home_win_prob"]
        except Exception:
            llm_prob = None

    if llm_prob is not None:
        home_prob = round(0.5 * mod_prob + 0.5 * llm_prob, 3)
        source = "modules+LLM"
    else:
        home_prob = round(mod_prob, 3)
        reason = "no OPENROUTER_API_KEY" if not os.environ.get("OPENROUTER_API_KEY") else "no llm_preds passed in"
        source = f"modules-only ({reason})"

    g = con.execute("SELECT home_team,away_team FROM games WHERE game_id=?", (game_id,)).fetchone()
    return {
        "game_id": game_id,
        "home_team": g["home_team"] if g else None,
        "away_team": g["away_team"] if g else None,
        "home_win_prob": home_prob,
        "module_signal": round(signal, 3),
        "n_modules": len(rows),
        "source": source,
        "modules": rows,
    }


if __name__ == "__main__":
    con = connect()
    gid = sys.argv[1] if len(sys.argv) > 1 else (
        con.execute("SELECT game_id FROM module_outputs ORDER BY created_at DESC LIMIT 1").fetchone() or [None]
    )[0]
    if not gid:
        print("no module_outputs yet — run analysis/registry.py <game_id> first")
        sys.exit(0)
    c = consensus_for_game(gid, con)
    print(f"consensus for {c['away_team']} @ {c['home_team']} ({gid}):")
    print(f"  home_win_prob={c['home_win_prob']}  signal={c['module_signal']}  "
          f"n_modules={c['n_modules']}  source={c['source']}")
