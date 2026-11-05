# NBA Betting Predictor

Multi-LLM (via OpenRouter) NBA game-prediction + edge-vs-line system, live for the
2026-27 season since 2026-11-05. Same shape as the sibling `football-betting` (World Cup)
and `mlb-betting` projects: free data layer -> multi-model consensus -> edge engine ->
settlement at real prices -> WhatsApp + public site. See `PLAN.md` for the original
architecture and `scripts/run_scheduler.py` for what actually runs.

**Status: paper-trading. No edge demonstrated yet.** The MLB sibling was stopped on
2026-09-01 after 332 graded recommendations at -12% ROI, and its wallets had been
inflated by grading every pick at a flat 1.91 — this repo grades at a captured quoted
price (see `engine/settle.py`). Read the wallet curve on the site before believing
anything.

## What runs (cron on the mini, America/Phoenix — Rich's timezone too)

```
09-19 every 2h  sweep   ESPN schedule/standings/injuries/news -> the-odds-api (h2h+spreads,
                        representative quotes, in-play prices skipped) -> 7 LLMs in parallel ->
                        consensus -> edge -> ONE message per sweep: the bets first, the first
                        tip-off as the deadline, then per-game detail. 3h window, so each game
                        is predicted 1-3h before tip-off.
23:45, 07:00    settle  ESPN results -> grade every model pick + recommendation at the price
                        captured when it was made -> wallets -> 賽果 message
08:00           digest  today's slate (Arizona time) with the current favourite + spread
each run      scripts/deploy_site.sh rebuilds docs/data/site.json and pushes (GitHub Pages)
```

The scheduler never sends WhatsApp itself: it atomically publishes complete sections to
`~/.whatsapp-channel/pending/nba-sweep-*.md` and the Kai agent forwards. Idempotent via
`games.notified_at / settled_notified_at / digest_at`.
Failed or disabled model runs leave games eligible for the next sweep; they do not
publish a no-bet conclusion or mark the game as notified.

## Layers

```
data/      fetch_espn.py   schedule + results + standings + injuries (free, no key, reliable)
           fetch_odds.py   all books stored; most common line + observed lower median price
           teams.py        one naming map for nba_api / ESPN / odds-api; canonical game id
                           = "<ET date>_<AWAY>@<HOME>" so no source needs reconciling
           fetch_players.py / fetch_results.py   optional nba_api layer (stats.nba.com is
                           often unreachable; the scheduler does not depend on it)
features/ analysis/        player-level modules (only run when nba_api data exists)
           fetch_news.py   ESPN news feed (league + per-team), last-72h headlines go into the prompt
models/    predict.py      ONE shared prompt -> gpt-5.6-sol / claude-opus-5.5 / gemini-3.1-pro /
                           deepseek-v4-pro-0813 / kimi-k2.6 / grok-4.7 / glm-5.3-prime (OpenRouter)
engine/    edge.py         consensus + EV; moneyline needs EV >= +5% and 66% agreement,
                           spread needs a 3-pt buffer, total a 6-pt buffer
           settle.py       real-price grading, z-gated model weights, wallet history
scripts/   run_scheduler.py  the loop; cron-nba.sh wrapper; deploy_site.sh
pipeline/  build_site_json.py -> docs/data/site.json
docs/      static site (GitHub Pages, source = main /docs)
frontend/  old FastAPI dashboard (loopback), still works
```

## Setup

```bash
pip install -r requirements.txt           # nba_api pandas fastapi uvicorn python-dotenv requests
cp .env.example .env                      # OPENROUTER_API_KEY, ODDS_API_KEY
python3 db/migrate.py                     # additive, idempotent (v1 + v2 + v3)
```

## Run

```bash
python3 scripts/run_scheduler.py --dry-run --skip-models   # data + odds + settlement preview; no pre-game alerts
python3 scripts/run_scheduler.py --dry-run --limit 1       # one real 6-model prediction
python3 scripts/run_scheduler.py                           # what cron runs
NBA_SKIP_ODDS=1 python3 scripts/run_scheduler.py --skip-models --hours-ahead 0   # settle only
python3 scripts/run_scheduler.py --digest                  # tomorrow's schedule
python3 pipeline/build_site_json.py                        # rebuild the site data
```

Each layer runs standalone too: `python3 data/fetch_espn.py`, `python3 data/fetch_odds.py`,
`python3 engine/settle.py`, `python3 engine/edge.py` (self-test), `python3 models/predict.py`
(prints the shared prompt, no key needed).

## Regression checks

```bash
python3 -m unittest discover -v
```

Tests use in-memory SQLite and isolated pending directories, without network calls,
API keys, model spend, or WhatsApp delivery. They cover settlement, retry after model
failure, complete pending-file publication, scheduled tip coverage, quoted line/price
pairs, and lifetime recommendation totals. Result cards show the last 14 days; summary
counts and P&L include all settled recommendations with a valid captured price.

## Budget

the-odds-api free tier = 500 credits/month. Each odds call is 2 credits (h2h+spreads;
totals dropped) and only runs when a game is inside the window, so a normal day is ~8
credits, a weekend ~12 (~250-300/month). OpenRouter: 7 calls per game, measured $0.085
per game on 2026-11-05 (opus-5.5 / gpt-5.6 reasoning tokens), ~12 games/day in season,
so roughly $30/month.

## Known limitations

- Spread/total recommendations have no probability behind them, only a buffer vs the
  consensus number; moneyline is the only market with a real EV.
- Model weights stay uniform until a model's hit rate is significant (>=30 bets, z>=2).
- Closing-line value (`recommendations.close_odds`) has a column but no capture job yet.
- Analysis modules (player-level) only contribute when stats.nba.com is reachable.
