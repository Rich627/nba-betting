-- NBA betting predictor — schema v2 (ADDITIVE layered upgrade).
-- Mirrors football-betting's player-level + analysis-module design.
-- SAFE TO RE-RUN: every statement is IF NOT EXISTS / ALTER-guarded by the migration runner.
-- DOES NOT touch existing P0 tables (games, team_game_stats, odds, predictions, model_record, user_bets).

-- ===== PLAYER DIMENSION =====
CREATE TABLE IF NOT EXISTS players (
  player_id   INTEGER PRIMARY KEY,        -- nba_api PLAYER_ID
  name        TEXT NOT NULL,
  team        TEXT,                        -- current team abbreviation
  team_id     INTEGER,
  position    TEXT                         -- PG/SG/SF/PF/C (same-position z-score)
);

-- ===== PLAYER PER-GAME BOX + ADVANCED (~45 NBA metrics) =====
-- One row per (game_id, player_id). Mirrors football's player_match_stats granularity.
CREATE TABLE IF NOT EXISTS player_game_stats (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id     TEXT,
  player_id   INTEGER,
  team        TEXT,
  team_id     INTEGER,
  is_home     INTEGER,
  game_date   TEXT,                        -- denormalized for fast rolling/cutoff queries
  position    TEXT,                        -- position played that game (z-score grouping)
  -- usage / minutes (1-2)
  min         REAL, started INTEGER,
  -- scoring (3)
  pts         REAL,
  -- rebounding (4-6)
  reb REAL, oreb REAL, dreb REAL,
  -- playmaking / disruption (7-12)
  ast REAL, stl REAL, blk REAL, tov REAL, pf REAL, plus_minus REAL,
  -- field goals (13-15)
  fgm REAL, fga REAL, fg_pct REAL,
  -- three point (16-18)
  fg3m REAL, fg3a REAL, fg3_pct REAL,
  -- free throw (19-21)
  ftm REAL, fta REAL, ft_pct REAL,
  -- shooting efficiency advanced (22-23)
  ts_pct REAL,                             -- true shooting %
  efg_pct REAL,                            -- effective FG %
  -- usage / rate advanced (24-27)
  usg_pct REAL,                            -- usage rate
  ast_pct REAL,                            -- assist %
  reb_pct REAL,                            -- total rebound %
  to_pct REAL,                             -- turnover %
  -- impact advanced (28-33)
  off_rating REAL,                         -- ORTG
  def_rating REAL,                         -- DRTG
  net_rating REAL,
  pace REAL,
  pie REAL,                                -- player impact estimate
  poss REAL,                               -- possessions
  -- shot quality / extra (34-39)
  oreb_pct REAL, dreb_pct REAL,
  ast_to_ratio REAL,                       -- assist/turnover
  pts_off_tov REAL, pts_2nd_chance REAL, pts_fb REAL,  -- second chance / fastbreak
  -- contextual (40-45)
  pts_paint REAL,
  fantasy_pts REAL,
  blka REAL,                               -- blocked attempts (shots of player blocked)
  pfd REAL,                                -- personal fouls drawn
  dd2 REAL,                                -- double-doubles
  td3 REAL,                                -- triple-doubles
  UNIQUE(game_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_pgs_player ON player_game_stats(player_id, game_date);
CREATE INDEX IF NOT EXISTS idx_pgs_game   ON player_game_stats(game_id);
CREATE INDEX IF NOT EXISTS idx_pgs_pos    ON player_game_stats(position, game_date);

-- ===== ANALYSIS-MODULE OUTPUTS =====
-- Each independent analysis/* module writes one structured row per game.
-- Mirrors football predictions(source=module). Kept SEPARATE from the existing
-- per-model `predictions` table so module edge can be backtested independently.
CREATE TABLE IF NOT EXISTS module_outputs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id     TEXT,
  module      TEXT,                        -- player_ability / lineup_pace_matchup / injuries_rest_b2b / odds_value
  score       REAL,                        -- signed: + favors HOME, - favors AWAY (range ~[-1,1])
  confidence  REAL,                        -- 0..1
  reasoning_md TEXT,                       -- one-paragraph human-readable judgement
  created_at  TEXT DEFAULT (datetime('now')),
  UNIQUE(game_id, module)
);
CREATE INDEX IF NOT EXISTS idx_modout_game ON module_outputs(game_id, module);

-- ===== PRE-MATCH NEWS / INJURIES (raw material for the injuries module) =====
CREATE TABLE IF NOT EXISTS prematch_news (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id     TEXT,
  team        TEXT,
  source      TEXT,
  fetched_at  TEXT,
  raw_text    TEXT,
  injuries_json TEXT                       -- [{player, status}]
);
CREATE INDEX IF NOT EXISTS idx_news_game ON prematch_news(game_id);
