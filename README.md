# NBA Betting Predictor
Multi-LLM (via OpenRouter) NBA game-prediction + edge-vs-line betting system with a deep
**layered analysis architecture** (mirrors sibling `football-betting`). Public read-only
dashboard. See `PLAN.md` for full architecture.

Status: full pipeline verified working end-to-end (2026-07-22): data layer, player/analysis
layers, 6-model OpenRouter consensus, edge engine, settlement, and dashboard all run cleanly.
No live NBA games/odds right now (off-season) — re-verify once preseason/regular season odds
go live on the-odds-api.

## Layers (all FREE-data; no key required to run them)
```
data/      nba_api → SQLite. Player-level ~45 metrics + rest/b2b.
features/  rolling last-10 form + same-position z-score.
analysis/  4 independent modules → {score, confidence, reasoning_md}:
           player_ability · lineup_pace_matchup · injuries_rest_b2b · odds_value
engine/    consensus fuses module_outputs (+ optional LLM) → edge → bet recs.
backtest/  replay past games, strict pre-game cutoff, hit-rate/ROI/Brier per module.
frontend/  FastAPI read-only dashboard.
```

## Setup
```bash
pip install -r requirements.txt
python3 db/migrate.py                       # apply schema (additive, idempotent)
```

## Run the free pipeline (no keys needed)
```bash
python3 data/fetch_results.py --since 06/01/2026   # game results (P0)
python3 data/fetch_players.py                      # player box+advanced + rest/b2b
python3 analysis/registry.py <game_id>             # run all 4 analysis modules
python3 engine/consensus.py <game_id>              # fuse modules → home win prob
python3 backtest/run.py                            # backtest modules + consensus
python3 scripts/run_daily.py                       # full orchestrator: free layers always run;
                                                    # odds/models/edge/settle steps run automatically
                                                    # once both keys are set (skip gracefully otherwise)
```

## Full pipeline (needs both keys)
```bash
python3 data/fetch_odds.py      # upcoming NBA odds (the-odds-api; empty result off-season = expected)
python3 scripts/run_daily.py    # results -> players -> modules -> odds -> 6-LLM predict -> edge -> settle
```

## Smoke tests
```bash
python3 features/rolling.py     # last-10 rolling form
python3 features/zscore.py      # same-position z-scores
python3 analysis/player_ability.py        # (or any analysis/*.py)
```

## Dashboard
```bash
uvicorn frontend.app:app --host 0.0.0.0 --port 8800   # access via Tailscale/SSH tunnel
# API: /api/games /api/models /api/predictions /api/modules /api/players
```

## Keys (optional enhancers — system runs without them)
Copy `.env.example` → `.env`:
- `OPENROUTER_API_KEY` — blends 6-LLM consensus on top of the analysis modules.
- `ODDS_API_KEY` — the-odds-api.com free tier; enables the odds_value module + edge-vs-line recs.

Without keys: data → features → analysis → module consensus → backtest all still run on free nba_api data.
