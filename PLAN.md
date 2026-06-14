# NBA Betting Prediction System — Architecture & Dev Plan
_Drafted 2026-06-14 for Rich. Reuses the World Cup agent architecture (`~/wc2026-agent`). Constraints: maximize FREE data; include a frontend dashboard._

## North-star
Multi-LLM consensus predicts NBA games, **compares predictions to the betting line to find +EV edges**, tracks each model's real-money track record, and surfaces recommended bets on a dashboard + WhatsApp. Core lesson carried from WC: **bet the edge vs the line, not the winner; NBA markets are sharp → small stakes, find true edges only.**

## 1. Data layer — ALL FREE
| Need | Free source | How |
|------|-------------|-----|
| Schedule, box scores, results, team/player stats, advanced (net rtg, pace) | **nba_api** (Python, official stats.nba.com) | primary; pip install nba_api |
| Historical / splits / H2H backup | basketball-reference.com | light scrape (respect rate limits) |
| Injuries / load management / lineups | ESPN injuries page + NBA official injury report; Rotowire (scrape) | pull a few hrs pre-tip (biggest line mover) |
| Odds (spread/讓分, total/大小, moneyline) | **the-odds-api.com FREE tier** (500 req/mo) | the edge layer; player props = paid, skip v1 |
| News/context (injury rumors etc.) | agent WebSearch | reuse existing |

→ Only paid thing in the whole system = OpenRouter LLM tokens. All data free.

## 2. Model layer — OpenRouter (one key, all models)
GPT, Claude, Gemini, DeepSeek, Moonshot/Kimi, xAI Grok via OpenRouter. Each model gets a structured prompt (matchup + team/player stats + injuries + the current line) → returns JSON `{pick, win_prob, predicted_margin, predicted_total, confidence, reasoning}`. Fan out in parallel.

## 3. Edge / consensus engine  (port from WC)
- Convert each model's win_prob/margin → implied pick on each market; compare to the line → **edge %**.
- Weight models by track record (see §4). Consensus = weighted vote.
- Only output a bet when weighted edge > threshold AND consensus agrees. Skip coin-flips (WC discipline).

## 4. Settlement + track record  (port from WC `settle_v2.py` / model_wallets)
- Post-game: fetch results (nba_api) → settle each model's picks at real odds → update per-model "wallet"/record → settle user's actual bets → P&L.
- Store: SQLite (cleaner than WC's JSON files at scale) — tables: games, odds, predictions, results, model_record, user_bets.

## 5. Frontend dashboard (Rich's ask)
- **Stack:** FastAPI (serves JSON) + simple single-page UI (plain HTML+Alpine or small React). Runs LOCAL on the mini (loopback), accessed via Tailscale/SSH tunnel — same pattern as the finance dashboard ([[project_smartcard]]).
- **Views:** (1) Today's games → model consensus + recommended bets w/ edge%; (2) Model leaderboard / track record; (3) User P&L + open bets; (4) per-game drill-down (each model's reasoning).

## 6. Orchestration
- **Pre-tip cron:** pull games+odds+injuries → run models → edge engine → write predictions → push recs to WhatsApp (new "NBA" group, same setup as WC/News) + update dashboard.
- **Post-game cron:** results → settle → update records/dashboard.

## 7. Phasing
- **P0 data spike:** nba_api + the-odds-api + injuries → store to SQLite. Verify on current Finals games.
- **P1 models:** OpenRouter, 1 model end-to-end → fan to 6.
- **P2 engine:** edge calc + consensus + settlement/track-record (port WC).
- **P3 frontend:** FastAPI + dashboard.
- **P4 automation:** crons + WhatsApp group.
- Build/test on **2026 Finals now**; full value next season (**Oct 2026**).

## 7b. Layered architecture (v2 — mirrors football-betting)
Added 2026-06 to give NBA the same deep, decoupled layers football has. **ADDITIVE** — P0 tables/scripts untouched.

```
[1 data/]      nba_api → SQLite
               • games / team_game_stats / odds (P0, unchanged)
               • players + player_game_stats (~45 metrics) ← NEW player layer
               • rest_days_* / is_b2b_* on games + team_game_stats ← NEW
        ↓
[2 features/]  rolling.py (player last-10 form) + zscore.py (same-position z-score)
        ↓
[3 analysis/]  4 INDEPENDENT modules, each analyze(game_id)→{score,confidence,reasoning_md}
   ├─ player_ability        net same-position roster strength (minute-weighted)
   ├─ lineup_pace_matchup   ORTG vs DRTG efficiency + pace style matchup
   ├─ injuries_rest_b2b     FIRST-CLASS: rest/b2b + injuries (biggest NBA line mover)
   └─ odds_value            model-implied vs market-implied prob (the edge layer)
   → persisted to module_outputs table (independently backtestable)
        ↓
[4 models/ + engine/]  engine/consensus.py fuses module_outputs (+ optional OpenRouter
                       LLM preds) → home_win_prob → engine/edge.py recommends bets.
                       LLM prompt (models/predict.game_dict_from_db) NOW INCLUDES module outputs.
        ↓
[5 backtest/]  run.py replays final games, STRICT pre-game cutoff (before_date=game_date),
               hit-rate / ROI / Brier per module AND per consensus.
```

**Schema:** `db/schema_v2.sql` (new tables), applied additively by `db/migrate.py`
(also ALTERs rest/b2b columns onto existing tables — never drops). `data/db.py` is the
shared connection helper all layers import.

**Key principle (from football):** every module is decoupled — can be toggled / backtested
alone to prove it has real edge before it earns consensus weight. injuries_rest_b2b is
weighted highest (1.4) as the dominant NBA line mover.

**Free-data guarantee:** the entire layered pipeline (data → features → analysis →
module consensus → backtest) runs with NO API keys. OpenRouter (LLM blend) and
the-odds-api (odds/edge) are optional enhancers; absence is handled gracefully.

## 8. Risks / notes
- NBA lines are efficient — realistic edge is thin; set expectations, flat small stakes, no chasing ([[feedback_betting_advice_conservative]]).
- the-odds-api free tier = 500 req/mo → cache odds, don't over-poll.
- Injuries are the #1 edge & the #1 data-reliability risk (announced late) — pull close to tip.
- OpenRouter token cost = 6 models × games/day; estimate before scaling.
