-- NBA betting predictor — schema v3 (ADDITIVE, 2026-11-05): live-season scheduler.
-- Applied by db/migrate.py after v1 + v2. Column additions live in migrate.ADD_COLUMNS.
-- SAFE TO RE-RUN. Never drops.

-- ESPN standings snapshot, one row per team (refreshed every sweep; cheap, free).
CREATE TABLE IF NOT EXISTS team_form (
  team TEXT PRIMARY KEY,                 -- nba_api abbreviation (GSW, NOP, ...)
  team_name TEXT, conference TEXT,
  wins INTEGER, losses INTEGER, win_pct REAL,
  home_record TEXT, road_record TEXT, last10 TEXT, streak TEXT,
  avg_pts_for REAL, avg_pts_against REAL, point_diff REAL,
  playoff_seed INTEGER,
  as_of TEXT
);

-- ESPN injury report, cleared + reloaded every sweep (a healed player must disappear).
CREATE TABLE IF NOT EXISTS team_injuries (
  team TEXT, player TEXT, status TEXT, comment TEXT, as_of TEXT,
  PRIMARY KEY (team, player)
);

-- Daily snapshot of model_record so the site can draw the wallet curve.
CREATE TABLE IF NOT EXISTS model_wallet_history (
  as_of_date TEXT, model TEXT, bets INTEGER, wins INTEGER, pnl REAL,
  PRIMARY KEY (as_of_date, model)
);
