#!/usr/bin/env python3
"""ESPN public JSON — schedule + results + standings + injuries. FREE, no key, no auth.

Why ESPN and not nba_api for the live season: stats.nba.com started timing out from
the mini on 2026-11-04 (nba_api's LeagueGameFinder hung 60s), and it has a history of
blocking non-browser clients. ESPN's site API answers in <1s and carries everything the
scheduler needs. nba_api stays as the optional player-level layer (data/fetch_players.py)
for when it is reachable.

Endpoints (all GET, no key):
  scoreboard?dates=YYYYMMDD  -> games of that US/Eastern date: tip-off (UTC), status, scores
  standings                  -> W-L, home/road, last-10, streak, pts for/against
  injuries                   -> per-team injury report with status + one-line comment

  python3 data/fetch_espn.py                 # today-2 .. today+1 (ET) + standings + injuries
  python3 data/fetch_espn.py --date 2026-11-05
"""
import argparse
import datetime
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import connect  # noqa: E402
from data import teams as T  # noqa: E402

BASE = "https://site.api.espn.com/apis"
SCOREBOARD = BASE + "/site/v2/sports/basketball/nba/scoreboard?dates={d}"
STANDINGS = BASE + "/v2/sports/basketball/nba/standings"
INJURIES = BASE + "/site/v2/sports/basketball/nba/injuries"

STATUS_MAP = {
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_FINAL": "final",
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "postponed",
}


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "nba-betting/1.0 (+mini)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _status(comp):
    t = comp["status"]["type"]
    if t.get("completed"):
        return "final"
    return STATUS_MAP.get(t.get("name"), "live")


def fetch_scoreboard(con, et_date_str: str):
    """Upsert every game ESPN lists for one US/Eastern date. Returns (n_games, n_final)."""
    d = _get(SCOREBOARD.format(d=et_date_str.replace("-", "")))
    season = str((d.get("leagues") or [{}])[0].get("season", {}).get("year", ""))
    n = n_final = 0
    for e in d.get("events", []):
        comp = e["competitions"][0]
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        if "home" not in sides or "away" not in sides:
            continue
        home = T.abbr_from_espn(sides["home"]["team"]["abbreviation"])
        away = T.abbr_from_espn(sides["away"]["team"]["abbreviation"])
        commence = T.iso_utc(T.parse_utc(e["date"]))
        gdate = T.et_date(commence)
        gid = T.game_key(gdate, home, away)
        status = _status(comp)
        hs = as_ = None
        if status in ("final", "live"):
            try:
                hs, as_ = int(sides["home"].get("score") or 0), int(sides["away"].get("score") or 0)
            except ValueError:
                hs = as_ = None
        con.execute(
            """INSERT INTO games(game_id,game_date,season,home_team,away_team,status,home_score,away_score,
                                 commence_time,espn_event_id,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))
               ON CONFLICT(game_id) DO UPDATE SET
                 status=excluded.status, home_score=excluded.home_score, away_score=excluded.away_score,
                 commence_time=excluded.commence_time, espn_event_id=excluded.espn_event_id,
                 season=CASE WHEN excluded.season!='' THEN excluded.season ELSE games.season END,
                 updated_at=datetime('now')""",
            (gid, gdate, season, home, away, status, hs, as_, commence, str(e.get("id", ""))),
        )
        n += 1
        n_final += status == "final"
    con.commit()
    return n, n_final


def fetch_window(con, days_back=2, days_fwd=1):
    """Scoreboard for today-days_back .. today+days_fwd (ET). Returns {date: (n, n_final)}."""
    today = datetime.datetime.now(T.ET).date()
    out = {}
    for i in range(-days_back, days_fwd + 1):
        d = (today + datetime.timedelta(days=i)).isoformat()
        try:
            out[d] = fetch_scoreboard(con, d)
        except Exception as e:  # noqa: BLE001
            out[d] = f"FAILED {type(e).__name__}: {str(e)[:100]}"
    return out


def fetch_standings(con):
    d = _get(STANDINGS)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    for conf in d.get("children", []):
        cname = conf.get("name", "")
        for entry in conf.get("standings", {}).get("entries", []):
            team = T.abbr_from_espn(entry["team"]["abbreviation"])
            st = {s["name"]: s for s in entry.get("stats", [])}

            def num(k, cast=float):
                v = st.get(k, {}).get("value")
                try:
                    return cast(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            def disp(k):
                return st.get(k, {}).get("displayValue")

            con.execute(
                """INSERT INTO team_form(team,team_name,conference,wins,losses,win_pct,home_record,road_record,
                                         last10,streak,avg_pts_for,avg_pts_against,point_diff,playoff_seed,as_of)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(team) DO UPDATE SET team_name=excluded.team_name,conference=excluded.conference,
                     wins=excluded.wins,losses=excluded.losses,win_pct=excluded.win_pct,
                     home_record=excluded.home_record,road_record=excluded.road_record,last10=excluded.last10,
                     streak=excluded.streak,avg_pts_for=excluded.avg_pts_for,avg_pts_against=excluded.avg_pts_against,
                     point_diff=excluded.point_diff,playoff_seed=excluded.playoff_seed,as_of=excluded.as_of""",
                (team, entry["team"].get("displayName"), cname,
                 num("wins", int), num("losses", int), num("winPercent"),
                 disp("Home"), disp("Road"), disp("Last Ten Games"), disp("streak"),
                 num("avgPointsFor"), num("avgPointsAgainst"), num("differential"),
                 num("playoffSeed", int), now),
            )
            n += 1
    con.commit()
    return n


def fetch_injuries(con):
    d = _get(INJURIES)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("DELETE FROM team_injuries")
    n = 0
    for t in d.get("injuries", []):
        team = T.abbr_from_name(t.get("displayName"))
        if team == t.get("displayName"):  # name not in the nba_api map -> try abbreviation field
            team = T.abbr_from_espn((t.get("abbreviation") or t.get("displayName") or "").upper())
        for inj in t.get("injuries", []):
            player = (inj.get("athlete") or {}).get("displayName")
            if not player:
                continue
            con.execute(
                "INSERT OR REPLACE INTO team_injuries(team,player,status,comment,as_of) VALUES(?,?,?,?,?)",
                (team, player, inj.get("status"), (inj.get("shortComment") or inj.get("longComment") or "")[:200], now),
            )
            n += 1
    con.commit()
    return n


def compute_rest(con):
    """rest_days_* / is_b2b_* on games, from the games table alone (no nba_api needed).
    rest = days since the team's previous game (ET dates). b2b = rest 1."""
    rows = con.execute(
        "SELECT game_id, game_date, home_team, away_team FROM games "
        "WHERE status IN ('scheduled','live','final') AND game_date IS NOT NULL ORDER BY game_date"
    ).fetchall()
    last = {}
    for r in rows:
        d = datetime.date.fromisoformat(r["game_date"])
        vals = {}
        for side, team in (("home", r["home_team"]), ("away", r["away_team"])):
            prev = last.get(team)
            rest = (d - prev).days if prev else None
            vals[side] = (rest, 1 if rest == 1 else 0 if rest is not None else None)
            last[team] = d
        con.execute(
            "UPDATE games SET rest_days_home=?, is_b2b_home=?, rest_days_away=?, is_b2b_away=? WHERE game_id=?",
            (vals["home"][0], vals["home"][1], vals["away"][0], vals["away"][1], r["game_id"]),
        )
    con.commit()
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="one ET date YYYY-MM-DD (default: today-2..today+1)")
    a = ap.parse_args()
    con = connect()
    if a.date:
        print("scoreboard", a.date, fetch_scoreboard(con, a.date))
    else:
        for d, res in fetch_window(con).items():
            print("scoreboard", d, res)
    print("standings teams:", fetch_standings(con))
    print("injury rows:", fetch_injuries(con))
    print("rest/b2b computed over", compute_rest(con), "games")
    for r in con.execute(
        "SELECT game_date,away_team,away_score,home_team,home_score,status,commence_time FROM games "
        "ORDER BY game_date DESC, commence_time LIMIT 8"
    ):
        print(f"  {r['game_date']} {r['away_team']} {r['away_score'] or ''} @ {r['home_team']} {r['home_score'] or ''} [{r['status']}] {r['commence_time']}")
