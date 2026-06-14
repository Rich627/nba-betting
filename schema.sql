-- NBA betting predictor — SQLite schema (P0)
CREATE TABLE IF NOT EXISTS games (
  game_id TEXT PRIMARY KEY,
  game_date TEXT,            -- YYYY-MM-DD (ET)
  season TEXT,
  home_team TEXT, away_team TEXT,
  home_team_id INTEGER, away_team_id INTEGER,
  status TEXT,               -- scheduled / live / final
  home_score INTEGER, away_score INTEGER,
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS team_game_stats (
  game_id TEXT, team_id INTEGER, team TEXT, is_home INTEGER,
  pts INTEGER, fg_pct REAL, fg3_pct REAL, reb INTEGER, ast INTEGER, tov INTEGER,
  PRIMARY KEY (game_id, team_id)
);
CREATE TABLE IF NOT EXISTS odds (        -- filled once the-odds-api key is set
  game_id TEXT, book TEXT, market TEXT, selection TEXT, line REAL, price REAL,
  fetched_at TEXT, PRIMARY KEY (game_id, book, market, selection)
);
CREATE TABLE IF NOT EXISTS predictions (  -- per-model picks
  game_id TEXT, model TEXT, market TEXT, pick TEXT, win_prob REAL,
  pred_margin REAL, pred_total REAL, confidence TEXT, reasoning TEXT,
  created_at TEXT, PRIMARY KEY (game_id, model, market)
);
CREATE TABLE IF NOT EXISTS model_record ( -- running track record / wallet
  model TEXT PRIMARY KEY, bets INTEGER DEFAULT 0, wins INTEGER DEFAULT 0,
  pnl REAL DEFAULT 0, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS user_bets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, market TEXT, selection TEXT,
  stake REAL, odds REAL, result TEXT, pnl REAL, placed_at TEXT
);
