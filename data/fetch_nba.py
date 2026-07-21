#!/usr/bin/env python3
"""P0 data layer — fetch NBA games + results from nba_api (FREE, no key) into SQLite.
Usage: python3 data/fetch_nba.py --date 2026-06-13   |   --recent 7
"""
import sqlite3, argparse, datetime, os, sys, time
from nba_api.stats.endpoints import scoreboardv2
from nba_api.stats.static import teams as _teams
# offline team_id -> abbreviation map (no network), reliable vs sparse line_score
TEAM_ABBR = {t["id"]: t["abbreviation"] for t in _teams.get_teams()}

DB = os.path.join(os.path.dirname(__file__), "..", "nba.db")

def db():
    con = sqlite3.connect(DB)
    with open(os.path.join(os.path.dirname(__file__), "..", "schema.sql")) as f:
        con.executescript(f.read())
    return con

def _int(v):
    try:
        if v is None or str(v).strip() in ("", "None"): return None
        return int(float(v))
    except (TypeError, ValueError): return None

def fetch_date(con, d):
    sb = scoreboardv2.ScoreboardV2(game_date=d, timeout=20)
    hdr = sb.game_header.get_data_frame()
    line = sb.line_score.get_data_frame()
    n = 0
    for _, g in hdr.iterrows():
        gid = g["GAME_ID"]
        ls = line[line["GAME_ID"] == gid]
        home_id, away_id = g["HOME_TEAM_ID"], g["VISITOR_TEAM_ID"]
        def team_abbr(tid):
            if tid in TEAM_ABBR: return TEAM_ABBR[tid]
            row = ls[ls["TEAM_ID"] == tid]
            return row["TEAM_ABBREVIATION"].iloc[0] if len(row) else str(tid)
        def team_pts(tid):
            row = ls[ls["TEAM_ID"] == tid]
            v = row["PTS"].iloc[0] if len(row) else None
            return int(v) if v is not None and str(v).strip() not in ("", "None") else None
        status = g["GAME_STATUS_TEXT"]
        st = "final" if "Final" in str(status) else ("live" if g["GAME_STATUS_ID"] == 2 else "scheduled")
        con.execute("""INSERT INTO games(game_id,game_date,season,home_team,away_team,home_team_id,away_team_id,status,home_score,away_score,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(game_id) DO UPDATE SET status=excluded.status,home_score=excluded.home_score,away_score=excluded.away_score,updated_at=datetime('now')""",
            (gid, d, str(g.get("SEASON","")), team_abbr(home_id), team_abbr(away_id), _int(home_id), _int(away_id), st, team_pts(home_id), team_pts(away_id)))
        n += 1
    con.commit()
    return n

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date"); ap.add_argument("--recent", type=int)
    a = ap.parse_args()
    con = db()
    dates = []
    today = datetime.date.today()
    if a.date: dates = [a.date]
    elif a.recent:
        dates = [(today - datetime.timedelta(days=i)).isoformat() for i in range(a.recent)]
    else: dates = [today.isoformat()]
    total = 0
    for d in dates:
        try:
            c = fetch_date(con, d); total += c; print(f"{d}: {c} games stored"); time.sleep(0.6)
        except Exception as e:
            print(f"{d}: ERR {type(e).__name__}: {str(e)[:80]}")
    print(f"TOTAL {total} games -> {os.path.abspath(DB)}")
    for row in con.execute("SELECT game_date,away_team,home_team,status,away_score,home_score FROM games ORDER BY game_date DESC LIMIT 8"):
        print("  ", row)
