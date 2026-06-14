"""Analysis modules — each independent, each outputs {score, confidence, reasoning_md}.

score convention (ALL modules): signed, + favors HOME, - favors AWAY, ~[-1, 1].
confidence: 0..1. reasoning_md: one short paragraph of human-readable judgement.

Modules:
  player_ability       — net same-position talent/form edge (rolling + z-score)
  lineup_pace_matchup  — pace + offensive/defensive rating style matchup
  injuries_rest_b2b    — FIRST-CLASS: rest days / back-to-back / injuries (biggest NBA line mover)
  odds_value           — model-implied vs market-implied probability (the edge layer)

Each exposes analyze(game_id, con=None, **kw) and is independently testable/backtestable.
"""
from analysis.registry import MODULES, run_all  # noqa: F401
