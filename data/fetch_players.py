#!/usr/bin/env python3
"""Player-level data layer — per-game player box scores into player_game_stats. FREE, no key.

Source: ESPN's game summary (site.api.espn.com .../summary?event=<espn_event_id>), the
same free API the scheduler already uses for schedule/results (data/fetch_espn.py).
nba_api (stats.nba.com) was the original source but it times out from the mini
(2026-11-04 onwards; a 1-game BoxScoreTraditionalV2 pull read-timed-out again on
2026-11-10), and the live games table carries ESPN event ids, not nba_api GAME_IDs.
fetch_game_players_nba() is kept for when stats.nba.com is reachable again.

ESPN gives the traditional box only. The advanced columns are derived from the box and
the two teams' totals with the standard formulas (TS%, eFG%, USG%, AST%, REB%, TO%, PIE,
possessions, pace). off_rating / def_rating / net_rating are the TEAM's points per 100
possessions in that game (not an individual on-court rating), so a minute-weighted team
average of them is the team's game rating — what analysis/lineup_pace_matchup needs.
Percentages are stored as fractions (0.55, not 55). position is G / F / C (ESPN's box
label), the bucket features/zscore.py compares players within.

ids: player_id = ESPN athlete id, team_id = ESPN team id, game_id = games.game_id
("YYYY-MM-DD_AWY@HOM"). Games from before the scheduler went live (no games row) are
keyed the same way, so player history covers the whole season without touching games
(new games rows would be picked up by the settlement pass and announced).

GRACEFUL: every request has a timeout; a failing game is logged and skipped, and
fetch_new() stops at a time budget, so the scheduler can call it on every run.

  python3 data/fetch_players.py                      # all final games in DB w/o player rows
  python3 data/fetch_players.py --limit 2            # small test pull (proves the path)
  python3 data/fetch_players.py --game 2026-11-02_OKC@CLE
  python3 data/fetch_players.py --since 2026-10-01   # + season games before the games table
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import connect  # noqa: E402
from data import teams as T  # noqa: E402

SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={eid}"
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={d}"
REGULAR_SEASON, POSTSEASON = 2, 3


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "nba-betting/1.0 (+mini)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _f(v):
    """Parse a numeric cell; '' / None / 'None' -> None. MIN like '34:21' -> minutes float."""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "None"):
        return None
    if ":" in s:  # "MM:SS" minutes
        try:
            m, sec = s.split(":")
            return round(float(m) + float(sec) / 60.0, 2)
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


# ----------------------------------------------------------------------------- ESPN box
def _pos_bucket(abbr):
    """ESPN position label -> G / F / C (PG/SG -> G, SF/PF -> F, 'G-F' -> G)."""
    a = (abbr or "").upper().split("-")[0].strip()
    if not a:
        return None
    if a in ("PG", "SG", "G"):
        return "G"
    if a in ("SF", "PF", "F"):
        return "F"
    return "C" if a == "C" else None


def _made_att(s):
    try:
        m, a = str(s).split("-")
        return float(m), float(a)
    except (ValueError, TypeError):
        return None, None


def _div(a, b):
    return round(a / b, 4) if a is not None and b else None


def parse_summary(summary):
    """ESPN summary JSON -> list of player-game dicts (players who got on the floor).
    Pure function (no I/O) so it is unit-testable on a saved payload."""
    box = summary.get("boxscore") or {}
    comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    side_of = {str(c.get("team", {}).get("id")): c.get("homeAway") for c in comp.get("competitors", [])}

    teams = []
    for t in box.get("players", []):
        stat = (t.get("statistics") or [{}])[0]
        keys = stat.get("keys") or []
        team_id = str(t["team"]["id"])
        rows = []
        for a in stat.get("athletes", []):
            vals = a.get("stats") or []
            if a.get("didNotPlay") or len(vals) != len(keys):
                continue
            s = dict(zip(keys, vals))
            mins = _f(s.get("minutes"))
            if not mins:
                continue
            fgm, fga = _made_att(s.get("fieldGoalsMade-fieldGoalsAttempted"))
            fg3m, fg3a = _made_att(s.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
            ftm, fta = _made_att(s.get("freeThrowsMade-freeThrowsAttempted"))
            ath = a.get("athlete") or {}
            rows.append({
                "player_id": int(ath["id"]), "name": ath.get("displayName") or ath.get("shortName"),
                "position": _pos_bucket((ath.get("position") or {}).get("abbreviation")),
                "started": 1 if a.get("starter") else 0, "min": mins,
                "pts": _f(s.get("points")), "reb": _f(s.get("rebounds")),
                "oreb": _f(s.get("offensiveRebounds")), "dreb": _f(s.get("defensiveRebounds")),
                "ast": _f(s.get("assists")), "stl": _f(s.get("steals")), "blk": _f(s.get("blocks")),
                "tov": _f(s.get("turnovers")), "pf": _f(s.get("fouls")),
                "plus_minus": _f(str(s.get("plusMinus", "")).replace("+", "")),
                "fgm": fgm, "fga": fga, "fg3m": fg3m, "fg3a": fg3a, "ftm": ftm, "fta": fta,
            })
        if rows:
            teams.append({"team": T.abbr_from_espn(t["team"]["abbreviation"]), "team_id": int(team_id),
                          "is_home": {"home": 1, "away": 0}.get(side_of.get(team_id)), "rows": rows})
    if len(teams) != 2:
        return []

    def tot(rows, k):
        return sum(r[k] or 0.0 for r in rows)

    for t in teams:
        r = t["rows"]
        t["tot"] = {k: tot(r, k) for k in ("min", "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov",
                                           "pf", "fgm", "fga", "fg3m", "fta", "ftm")}
        t["tot"]["min"] = t["tot"]["min"] or 240.0
        tt = t["tot"]
        t["poss"] = tt["fga"] + 0.44 * tt["fta"] - tt["oreb"] + tt["tov"]

    def pie_part(x):
        return (x["pts"] + x["fgm"] + x["ftm"] - x["fga"] - x["fta"] + x["dreb"] + 0.5 * x["oreb"]
                + x["ast"] + x["stl"] + 0.5 * x["blk"] - x["pf"] - x["tov"])

    game_pie = pie_part(teams[0]["tot"]) + pie_part(teams[1]["tot"])
    game_poss = (teams[0]["poss"] + teams[1]["poss"]) / 2.0
    out = []
    for i, t in enumerate(teams):
        tm, opp = t["tot"], teams[1 - i]["tot"]
        five = tm["min"] / 5.0                       # team minutes / 5 = game length on the floor
        pace = 48.0 * game_poss / five if five else None
        ortg = 100.0 * tm["pts"] / game_poss if game_poss else None
        drtg = 100.0 * opp["pts"] / game_poss if game_poss else None
        for r in t["rows"]:
            x = {k: (r[k] or 0.0) for k in ("min", "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov",
                                           "pf", "fgm", "fga", "fg3m", "fta", "ftm")}
            m = x["min"]
            plays = x["fga"] + 0.44 * x["fta"] + x["tov"]
            tm_plays = tm["fga"] + 0.44 * tm["fta"] + tm["tov"]
            dd = sum(1 for k in ("pts", "reb", "ast", "stl", "blk") if x[k] >= 10)
            r.update({
                "team": t["team"], "team_id": t["team_id"], "is_home": t["is_home"],
                "fg_pct": _div(x["fgm"], x["fga"]), "fg3_pct": _div(r["fg3m"], r["fg3a"]),
                "ft_pct": _div(x["ftm"], x["fta"]),
                "ts_pct": _div(x["pts"], 2.0 * (x["fga"] + 0.44 * x["fta"])),
                "efg_pct": _div(x["fgm"] + 0.5 * x["fg3m"], x["fga"]),
                "usg_pct": _div(plays * five, m * tm_plays),
                "ast_pct": _div(x["ast"], (m / five) * tm["fgm"] - x["fgm"]) if (m / five) * tm["fgm"] - x["fgm"] > 0 else None,
                "reb_pct": _div(x["reb"] * five, m * (tm["reb"] + opp["reb"])),
                "oreb_pct": _div(x["oreb"] * five, m * (tm["oreb"] + opp["dreb"])),
                "dreb_pct": _div(x["dreb"] * five, m * (tm["dreb"] + opp["oreb"])),
                "to_pct": _div(x["tov"], plays),
                "off_rating": round(ortg, 2) if ortg else None,
                "def_rating": round(drtg, 2) if drtg else None,
                "net_rating": round(ortg - drtg, 2) if ortg and drtg else None,
                "pace": round(pace, 2) if pace else None,
                "pie": _div(pie_part(x), game_pie),
                "poss": round(game_poss * m / five, 2) if five else None,
                "ast_to_ratio": _div(x["ast"], x["tov"]),
                "fantasy_pts": round(x["pts"] + 1.2 * x["reb"] + 1.5 * x["ast"] + 3 * x["stl"]
                                     + 3 * x["blk"] - x["tov"], 2),
                "dd2": 1.0 if dd >= 2 else 0.0, "td3": 1.0 if dd >= 3 else 0.0,
            })
            out.append(r)
    return out


_COLS = ["game_id", "player_id", "team", "team_id", "is_home", "game_date", "position",
         "min", "started", "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "plus_minus",
         "fgm", "fga", "fg_pct", "fg3m", "fg3a", "fg3_pct", "ftm", "fta", "ft_pct",
         "ts_pct", "efg_pct", "usg_pct", "ast_pct", "reb_pct", "to_pct",
         "off_rating", "def_rating", "net_rating", "pace", "pie", "poss",
         "oreb_pct", "dreb_pct", "ast_to_ratio", "fantasy_pts", "dd2", "td3"]
_UPSERT = (f"INSERT INTO player_game_stats({','.join(_COLS)}) VALUES({','.join('?' * len(_COLS))}) "
           "ON CONFLICT(game_id,player_id) DO UPDATE SET "
           + ",".join(f"{c}=excluded.{c}" for c in _COLS if c not in ("game_id", "player_id")))


def store_rows(con, game_id, game_date, rows):
    """Upsert parsed rows into players + player_game_stats (one transaction per game)."""
    for r in rows:
        con.execute(
            """INSERT INTO players(player_id,name,team,team_id,position) VALUES(?,?,?,?,?)
               ON CONFLICT(player_id) DO UPDATE SET name=excluded.name,
                 team=CASE WHEN excluded.team IS NOT NULL THEN excluded.team ELSE players.team END,
                 team_id=COALESCE(excluded.team_id,players.team_id),
                 position=COALESCE(excluded.position,players.position)""",
            (r["player_id"], r["name"], r["team"], r["team_id"], r["position"]),
        )
        row = dict(r, game_id=game_id, game_date=game_date)
        con.execute(_UPSERT, [row.get(c) for c in _COLS])
    con.commit()
    return len(rows)


def fetch_game_players(con, game_id, event_id, game_date, timeout=15):
    """Pull one game's ESPN box and store it. Returns rows written (0 = no box yet).
    Network/parse errors raise before anything is written, so callers never need to roll
    back (and never roll back the caller's own uncommitted work)."""
    rows = parse_summary(_get(SUMMARY.format(eid=event_id), timeout=timeout))
    return store_rows(con, game_id, game_date, rows) if rows else 0


def _season_events(et_date, timeout=15):
    """Final regular-season / playoff games ESPN lists for one ET date: [(game_id, event_id)]."""
    d = _get(SCOREBOARD.format(d=et_date.replace("-", "")), timeout=timeout)
    out = []
    for e in d.get("events", []):
        comp = e["competitions"][0]
        if (e.get("season") or {}).get("type") not in (REGULAR_SEASON, POSTSEASON):
            continue
        if not comp["status"]["type"].get("completed"):
            continue
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        if "home" not in sides or "away" not in sides:
            continue
        home = T.abbr_from_espn(sides["home"]["team"]["abbreviation"])
        away = T.abbr_from_espn(sides["away"]["team"]["abbreviation"])
        out.append((T.game_key(T.et_date(T.iso_utc(T.parse_utc(e["date"]))), home, away), str(e["id"])))
    return out


# ----------------------------------------------------------------------------- nba_api (optional)
# START_POSITION in box is G/F/C; map to a coarse 5-position bucket for z-score grouping.
_POS_MAP = {"G": "PG", "F": "SF", "C": "C"}


def fetch_game_players_nba(con, game_id, game_date=None, home_team_id=None, timeout=30):
    """Pull traditional + advanced player box for one game; upsert player_game_stats.
    Returns rows written, or raises on network error (caller decides to skip)."""
    from nba_api.stats.endpoints import boxscoretraditionalv2, boxscoreadvancedv3

    trad = boxscoretraditionalv2.BoxScoreTraditionalV2(
        game_id=game_id, timeout=timeout
    ).player_stats.get_data_frame()
    try:
        # v3 is the reliable advanced endpoint (camelCase, keyed by personId)
        adv = boxscoreadvancedv3.BoxScoreAdvancedV3(
            game_id=game_id, timeout=timeout
        ).player_stats.get_data_frame()
        adv_by = {int(r["personId"]): r for _, r in adv.iterrows()}
    except Exception:
        adv_by = {}  # advanced is optional; box alone still useful

    n = 0
    for _, r in trad.iterrows():
        pid = int(r["PLAYER_ID"])
        a = adv_by.get(pid, {})
        team = r["TEAM_ABBREVIATION"]
        team_id = int(r["TEAM_ID"])
        is_home = 1 if (home_team_id is not None and team_id == home_team_id) else (0 if home_team_id is not None else None)
        pos_raw = (r.get("START_POSITION") or "").strip()
        pos = _POS_MAP.get(pos_raw, pos_raw or None)

        # players dimension
        con.execute(
            """INSERT INTO players(player_id,name,team,team_id,position) VALUES(?,?,?,?,?)
               ON CONFLICT(player_id) DO UPDATE SET name=excluded.name,team=excluded.team,
               team_id=excluded.team_id,position=COALESCE(excluded.position,players.position)""",
            (pid, r["PLAYER_NAME"], team, team_id, pos),
        )

        con.execute(
            """INSERT INTO player_game_stats(
                game_id,player_id,team,team_id,is_home,game_date,position,
                min,started,pts,reb,oreb,dreb,ast,stl,blk,tov,pf,plus_minus,
                fgm,fga,fg_pct,fg3m,fg3a,fg3_pct,ftm,fta,ft_pct,
                ts_pct,efg_pct,usg_pct,ast_pct,reb_pct,to_pct,
                off_rating,def_rating,net_rating,pace,pie,poss,
                oreb_pct,dreb_pct,ast_to_ratio)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(game_id,player_id) DO UPDATE SET
                 min=excluded.min,pts=excluded.pts,reb=excluded.reb,plus_minus=excluded.plus_minus,
                 ts_pct=excluded.ts_pct,usg_pct=excluded.usg_pct,off_rating=excluded.off_rating,
                 def_rating=excluded.def_rating,pace=excluded.pace,pie=excluded.pie""",
            (
                game_id, pid, team, team_id, is_home, game_date, pos,
                _f(r.get("MIN")), 1 if pos_raw else 0,
                _f(r.get("PTS")), _f(r.get("REB")), _f(r.get("OREB")), _f(r.get("DREB")),
                _f(r.get("AST")), _f(r.get("STL")), _f(r.get("BLK")), _f(r.get("TO")),
                _f(r.get("PF")), _f(r.get("PLUS_MINUS")),
                _f(r.get("FGM")), _f(r.get("FGA")), _f(r.get("FG_PCT")),
                _f(r.get("FG3M")), _f(r.get("FG3A")), _f(r.get("FG3_PCT")),
                _f(r.get("FTM")), _f(r.get("FTA")), _f(r.get("FT_PCT")),
                _f(a.get("trueShootingPercentage")), _f(a.get("effectiveFieldGoalPercentage")),
                _f(a.get("usagePercentage")), _f(a.get("assistPercentage")),
                _f(a.get("reboundPercentage")), _f(a.get("turnoverRatio")),
                _f(a.get("offensiveRating")), _f(a.get("defensiveRating")),
                _f(a.get("netRating")), _f(a.get("pace")), _f(a.get("PIE")),
                _f(a.get("possessions")),
                _f(a.get("offensiveReboundPercentage")), _f(a.get("defensiveReboundPercentage")),
                _f(a.get("assistToTurnover")),
            ),
        )
        n += 1
    con.commit()
    return n


def compute_rest_b2b(con):
    """Fill rest_days_* / is_b2b_* on games (per team, from each team's prior game date)
    and mirror onto team_game_stats. Pure SQL/python over stored games. ADDITIVE."""
    games = con.execute(
        "SELECT game_id,game_date,home_team_id,away_team_id FROM games "
        "WHERE game_date IS NOT NULL ORDER BY game_date"
    ).fetchall()
    from datetime import datetime

    def parse(d):
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y"):
            try:
                return datetime.strptime(d[:19] if "T" in d else d, fmt).date()
            except (ValueError, TypeError):
                continue
        return None

    last_played = {}  # team_id -> date
    for g in games:
        gd = parse(g["game_date"])
        upd = {}
        for side, tid in (("home", g["home_team_id"]), ("away", g["away_team_id"])):
            rest = None
            b2b = None
            if gd is not None and tid is not None and tid in last_played:
                rest = (gd - last_played[tid]).days
                b2b = 1 if rest == 1 else 0
            upd[side] = (rest, b2b)
            if gd is not None and tid is not None:
                last_played[tid] = gd
        con.execute(
            "UPDATE games SET rest_days_home=?,is_b2b_home=?,rest_days_away=?,is_b2b_away=? WHERE game_id=?",
            (upd["home"][0], upd["home"][1], upd["away"][0], upd["away"][1], g["game_id"]),
        )
        # mirror onto team_game_stats rows if present
        for side, tid in (("home", g["home_team_id"]), ("away", g["away_team_id"])):
            con.execute(
                "UPDATE team_game_stats SET rest_days=?,is_b2b=? WHERE game_id=? AND team_id=?",
                (upd[side][0], upd[side][1], g["game_id"], tid),
            )
    con.commit()
    return len(games)


def missing_games(con, only_game=None):
    """Final games in the games table that have no player rows yet, newest first."""
    if only_game:
        return con.execute("SELECT game_id, game_date, espn_event_id FROM games WHERE game_id=?",
                           (only_game,)).fetchall()
    return con.execute(
        """SELECT g.game_id, g.game_date, g.espn_event_id FROM games g
           WHERE g.status='final' AND g.espn_event_id IS NOT NULL AND g.espn_event_id != ''
             AND NOT EXISTS (SELECT 1 FROM player_game_stats p WHERE p.game_id=g.game_id)
           ORDER BY g.game_date DESC, g.commence_time DESC"""
    ).fetchall()


def fetch_new(con, limit=None, only_game=None, budget_s=None, timeout=15, pause=0.3, log=print):
    """Fetch player rows for final games that don't have them yet. Never raises for a
    network error: the game is skipped (and retried next run). Stops starting new games
    once `budget_s` seconds have passed. Returns (games_ok, rows, games_failed)."""
    rows = missing_games(con, only_game)
    if limit:
        rows = rows[:limit]
    t0 = time.monotonic()
    ok = total = failed = 0
    for g in rows:
        if budget_s is not None and time.monotonic() - t0 > budget_s:
            log(f"  [players] time budget {budget_s}s reached; {len(rows) - ok - failed} game(s) left for next run")
            break
        try:
            n = fetch_game_players(con, g["game_id"], g["espn_event_id"], g["game_date"], timeout=timeout)
            if n:
                ok += 1
                total += n
                log(f"  [players] {g['game_id']}: {n} player rows")
            else:
                failed += 1
                log(f"  [players] {g['game_id']}: no box score yet")
        except Exception as e:  # noqa: BLE001
            failed += 1
            log(f"  [players] {g['game_id']}: SKIP {type(e).__name__}: {str(e)[:80]}")
        time.sleep(pause)
    return ok, total, failed


backfill = fetch_new  # old name (scripts/run_daily.py)


def backfill_season(con, since, until=None, pause=0.3, log=print):
    """Player rows for every final game from `since` to `until` (ET dates, default: the
    day before the games table starts), straight from the ESPN scoreboard. Writes only
    players / player_game_stats — never games. Returns (games_ok, rows)."""
    if until is None:
        first = con.execute("SELECT MIN(game_date) FROM games").fetchone()[0]
        until = ((datetime.date.fromisoformat(first) - datetime.timedelta(days=1)).isoformat()
                 if first else datetime.date.today().isoformat())
    have = {r[0] for r in con.execute("SELECT DISTINCT game_id FROM player_game_stats")}
    d, end = datetime.date.fromisoformat(since), datetime.date.fromisoformat(until)
    ok = total = 0
    while d <= end:
        try:
            events = _season_events(d.isoformat())
        except Exception as e:  # noqa: BLE001
            log(f"  [players] scoreboard {d}: SKIP {type(e).__name__}: {str(e)[:80]}")
            events = []
        for gid, eid in events:
            if gid in have:
                continue
            try:
                n = fetch_game_players(con, gid, eid, d.isoformat())
                if n:
                    ok += 1
                    total += n
                    log(f"  [players] {gid}: {n} player rows")
            except Exception as e:  # noqa: BLE001
                log(f"  [players] {gid}: SKIP {type(e).__name__}: {str(e)[:80]}")
            time.sleep(pause)
        d += datetime.timedelta(days=1)
    return ok, total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--game")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--since", help="also backfill season games from this ET date (YYYY-MM-DD) "
                                    "up to the day before the games table starts")
    a = ap.parse_args()
    con = connect()
    if a.since:
        ok0, tot0 = backfill_season(con, a.since)
        print(f"pre-schedule season games: {tot0} player rows across {ok0} games")
    ok, total, failed = fetch_new(con, limit=a.limit, only_game=a.game)
    print(f"player rows: {total} across {ok} games ({failed} skipped)")
    print("sample players:")
    for r in con.execute(
        "SELECT pl.name,p.team,p.position,p.game_date,p.pts,p.reb,p.ast,p.ts_pct,p.usg_pct "
        "FROM player_game_stats p JOIN players pl ON pl.player_id=p.player_id "
        "ORDER BY p.game_date DESC, p.pts DESC LIMIT 5"
    ):
        print("  ", tuple(r))
