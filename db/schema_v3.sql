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

-- 2026-11-06: official NBA injury report (15-minute PDF), cleared + reloaded each run.
-- game_date is the PDF's MM/DD/YYYY; status includes Probable/Questionable/Doubtful/
-- Available and 'NOT YET SUBMITTED' (player = '').
CREATE TABLE IF NOT EXISTS official_injuries (
  team TEXT, player TEXT, status TEXT, reason TEXT, matchup TEXT, game_date TEXT,
  report TEXT, report_label TEXT,
  PRIMARY KEY (team, player, game_date)
);

-- ESPN season averages per player (role weight for injury impact), reloaded each run.
CREATE TABLE IF NOT EXISTS player_season_stats (
  player_key TEXT, player TEXT, team TEXT, gp REAL, avg_min REAL, avg_pts REAL, as_of TEXT,
  PRIMARY KEY (player_key, team)
);

-- What the injury picture looked like when a game was predicted ('predict') and at the
-- pre-tip re-check ('recheck'; payload also archives any voided recommendations).
CREATE TABLE IF NOT EXISTS injury_snapshots (
  game_id TEXT, kind TEXT, taken_at TEXT, payload TEXT,
  PRIMARY KEY (game_id, kind)
);
