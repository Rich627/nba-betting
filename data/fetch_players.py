#!/usr/bin/env python3
"""Player-level data layer — per-game box + advanced stats via nba_api (FREE, no key).

Pulls BoxScoreTraditionalV2 (box) + BoxScoreAdvancedV2 (TS%/USG%/ORTG/DRTG/PACE/PIE...)
for finished games and writes ~45 metrics per player into player_game_stats. Also fills
the players dimension and computes rest_days / is_b2b on games + team_game_stats.

GRACEFUL: any network failure on a game is caught and skipped (never hangs the run).

  python3 data/fetch_players.py                 # backfill all final games in DB w/o player rows
  python3 data/fetch_players.py --game 0042400401
  python3 data/fetch_players.py --limit 2       # small test pull (proves the path)
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import connect  # noqa: E402


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


# START_POSITION in box is G/F/C; map to a coarse 5-position bucket for z-score grouping.
_POS_MAP = {"G": "PG", "F": "SF", "C": "C"}


def fetch_game_players(con, game_id, game_date=None, home_team_id=None, timeout=30):
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


def backfill(con, limit=None, only_game=None):
    """Fetch player rows for final games that don't have them yet."""
    if only_game:
        rows = con.execute(
            "SELECT game_id,game_date,home_team_id FROM games WHERE game_id=?", (only_game,)
        ).fetchall()
    else:
        rows = con.execute(
            """SELECT g.game_id,g.game_date,g.home_team_id FROM games g
               WHERE g.status='final'
                 AND NOT EXISTS (SELECT 1 FROM player_game_stats p WHERE p.game_id=g.game_id)
               ORDER BY g.game_date DESC"""
        ).fetchall()
    if limit:
        rows = rows[:limit]
    total, ok = 0, 0
    for g in rows:
        try:
            n = fetch_game_players(con, g["game_id"], g["game_date"], g["home_team_id"])
            total += n
            ok += 1
            print(f"  {g['game_id']} ({g['game_date']}): {n} player rows")
            time.sleep(0.6)
        except Exception as e:
            print(f"  {g['game_id']}: SKIP {type(e).__name__}: {str(e)[:70]}")
    return ok, total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--game")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    con = connect()
    ok, total = backfill(con, limit=a.limit, only_game=a.game)
    ng = compute_rest_b2b(con)
    print(f"player rows: {total} across {ok} games | rest/b2b computed for {ng} games")
    print("sample players:")
    for r in con.execute(
        "SELECT pl.name,p.team,p.position,p.pts,p.reb,p.ast,p.ts_pct,p.usg_pct "
        "FROM player_game_stats p JOIN players pl ON pl.player_id=p.player_id "
        "ORDER BY p.pts DESC LIMIT 5"
    ):
        print("  ", tuple(r))
