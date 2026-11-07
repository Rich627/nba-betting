#!/usr/bin/env python3
"""Analysis registry — run all modules for a game, persist to module_outputs.

odds_value runs LAST and is fed the other modules' scores (it's the edge layer).

  python3 analysis/registry.py <game_id>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis import injuries_rest_b2b, lineup_pace_matchup, odds_value, player_ability  # noqa: E402
from analysis._common import get_con  # noqa: E402

# ordered: odds_value last (depends on the others)
MODULES = {
    "player_ability": player_ability.analyze,
    "lineup_pace_matchup": lineup_pace_matchup.analyze,
    "injuries_rest_b2b": injuries_rest_b2b.analyze,
    "odds_value": odds_value.analyze,
}


def run_all(game_id, con=None, before_date=None, persist=True, modules=None, injury_snapshot=None):
    """Run every module (or only `modules`); return {module: {score, confidence, reasoning_md}}.
    Feeds the non-odds module scores into odds_value. Persists to module_outputs.
    `injury_snapshot` (data.injuries.snapshot) goes to injuries_rest_b2b in live runs."""
    con = get_con(con)
    out = {}
    for name, fn in MODULES.items():
        if modules is not None and name not in modules:
            continue
        try:
            if name == "odds_value":
                res = fn(game_id, con, module_scores={k: v for k, v in out.items()}, before_date=before_date)
            elif name == "injuries_rest_b2b":
                res = fn(game_id, con, before_date=before_date, injury_snapshot=injury_snapshot)
            else:
                res = fn(game_id, con, before_date=before_date)
        except Exception as e:
            res = {"score": 0.0, "confidence": 0.0, "reasoning_md": f"ERROR {type(e).__name__}: {str(e)[:80]}"}
        out[name] = res
        if persist:
            con.execute(
                """INSERT INTO module_outputs(game_id,module,score,confidence,reasoning_md,created_at)
                   VALUES(?,?,?,?,?,datetime('now'))
                   ON CONFLICT(game_id,module) DO UPDATE SET
                     score=excluded.score,confidence=excluded.confidence,
                     reasoning_md=excluded.reasoning_md,created_at=datetime('now')""",
                (game_id, name, res["score"], res["confidence"], res["reasoning_md"]),
            )
    if persist:
        con.commit()
    return out


if __name__ == "__main__":
    con = get_con(None)
    gid = sys.argv[1] if len(sys.argv) > 1 else (
        con.execute("SELECT game_id FROM games WHERE status='final' ORDER BY game_date DESC LIMIT 1").fetchone() or [None]
    )[0]
    print(f"=== analysis modules for game {gid} ===")
    res = run_all(gid, con)
    for name, r in res.items():
        print(f"\n[{name}] score={r['score']:+.3f} conf={r['confidence']:.2f}")
        print("  " + r["reasoning_md"])
