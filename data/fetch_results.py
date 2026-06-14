#!/usr/bin/env python3
"""P0 data layer (results) — completed NBA games via nba_api LeagueGameFinder (FREE, no key).
Reliable teams + scores + home/away. Usage: python3 data/fetch_results.py --since 06/01/2026
"""
import sqlite3, argparse, os
from nba_api.stats.endpoints import leaguegamefinder

DB = os.path.join(os.path.dirname(__file__), "..", "nba.db")

def db():
    con = sqlite3.connect(DB)
    with open(os.path.join(os.path.dirname(__file__), "..", "schema.sql")) as f:
        con.executescript(f.read())
    return con

def fetch(con, since):
    df = leaguegamefinder.LeagueGameFinder(date_from_nullable=since, league_id_nullable="00", timeout=30).get_data_frames()[0]
    games = {}
    for _, r in df.iterrows():
        gid = r["GAME_ID"]; g = games.setdefault(gid, {"date": r["GAME_DATE"], "season": str(r.get("SEASON_ID","")), "rows": []})
        home = " vs. " in r["MATCHUP"]            # "NYK vs. SAS" => NYK home; "NYK @ SAS" => NYK away
        g["rows"].append({"team": r["TEAM_ABBREVIATION"], "team_id": int(r["TEAM_ID"]), "is_home": home,
                          "pts": int(r["PTS"]) if r["PTS"] is not None else None, "wl": r["WL"],
                          "fg_pct": r.get("FG_PCT"), "fg3_pct": r.get("FG3_PCT"), "reb": r.get("REB"), "ast": r.get("AST"), "tov": r.get("TOV")})
    n = 0
    for gid, g in games.items():
        if len(g["rows"]) != 2: continue
        home = next((x for x in g["rows"] if x["is_home"]), None)
        away = next((x for x in g["rows"] if not x["is_home"]), None)
        if not home or not away: continue
        con.execute("""INSERT INTO games(game_id,game_date,season,home_team,away_team,home_team_id,away_team_id,status,home_score,away_score,updated_at)
            VALUES(?,?,?,?,?,?,?,'final',?,?,datetime('now'))
            ON CONFLICT(game_id) DO UPDATE SET status='final',home_score=excluded.home_score,away_score=excluded.away_score,updated_at=datetime('now')""",
            (gid, g["date"], g["season"], home["team"], away["team"], home["team_id"], away["team_id"], home["pts"], away["pts"]))
        for side in (home, away):
            con.execute("""INSERT OR REPLACE INTO team_game_stats(game_id,team_id,team,is_home,pts,fg_pct,fg3_pct,reb,ast,tov)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (gid, side["team_id"], side["team"], 1 if side["is_home"] else 0, side["pts"],
                 side["fg_pct"], side["fg3_pct"], side["reb"], side["ast"], side["tov"]))
        n += 1
    con.commit(); return n

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--since", default="06/01/2026")
    a = ap.parse_args(); con = db()
    n = fetch(con, a.since)
    print(f"stored {n} completed games -> {os.path.abspath(DB)}")
    for row in con.execute("SELECT game_date,away_team,away_score,home_team,home_score FROM games WHERE status='final' ORDER BY game_date DESC LIMIT 10"):
        d,at,asc,ht,hs = row
        print(f"  {d}  {at} {asc} @ {ht} {hs}")
