#!/usr/bin/env python3
"""P0 data layer (odds) — upcoming NBA game odds via the-odds-api.com (FREE tier, 500 req/mo).
Needs ODDS_API_KEY. Matches events to existing `games` rows by team name + date; if a game
isn't in the DB yet (odds post before nba_api lists it as scheduled) it upserts a stub game row.

GRACEFUL: the-odds-api only lists a sport_key once its season/market is live. NBA regular
season markets go dark in the off-season (Jul off-season, only futures/outrights listed) —
that's a normal, expected empty result, not a bug. Any HTTP error is caught and reported.

  python3 data/fetch_odds.py                # fetch h2h/spreads/totals for basketball_nba
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import connect  # noqa: E402

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "basketball_nba"

# the-odds-api uses full team names ("Los Angeles Lakers"); nba_api games table uses
# abbreviations ("LAL"). Build the map once, offline (no network).
from nba_api.stats.static import teams as _teams  # noqa: E402

_FULLNAME_TO_ABBR = {t["full_name"]: t["abbreviation"] for t in _teams.get_teams()}


def _abbr(full_name):
    return _FULLNAME_TO_ABBR.get(full_name, full_name)


def _find_or_stub_game(con, home_abbr, away_abbr, commence_iso):
    """Find an existing games row for this matchup near this date; else insert a
    'scheduled' stub so odds have somewhere to attach (nba_api's own scoreboard fetch
    will fill in the real game_id/score later when it lists the game)."""
    game_date = commence_iso[:10]
    row = con.execute(
        "SELECT game_id FROM games WHERE home_team=? AND away_team=? AND game_date=?",
        (home_abbr, away_abbr, game_date),
    ).fetchone()
    if row:
        return row["game_id"]
    # also allow a 1-day slip (odds book date vs nba_api ET date can differ by a day)
    row = con.execute(
        "SELECT game_id FROM games WHERE home_team=? AND away_team=? "
        "AND game_date BETWEEN date(?, '-1 day') AND date(?, '+1 day')",
        (home_abbr, away_abbr, game_date, game_date),
    ).fetchone()
    if row:
        return row["game_id"]
    stub_id = f"ODDS-{home_abbr}-{away_abbr}-{game_date}"
    con.execute(
        """INSERT OR IGNORE INTO games(game_id,game_date,season,home_team,away_team,status,updated_at)
           VALUES(?,?,?,?,?, 'scheduled', datetime('now'))""",
        (stub_id, game_date, "", home_abbr, away_abbr),
    )
    return stub_id


def fetch_upcoming(con, regions="us", markets="h2h,spreads,totals", timeout=20):
    """Fetch current NBA odds. Returns (n_events, n_odds_rows, note).
    note is a human-readable status string (e.g. off-season / no active market)."""
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return 0, 0, "ODDS_API_KEY not set"

    url = (f"{ODDS_API_BASE}/sports/{SPORT_KEY}/odds/"
           f"?apiKey={key}&regions={regions}&markets={markets}&oddsFormat=decimal")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            events = json.load(r)
            remaining = r.headers.get("x-requests-remaining")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        # the-odds-api returns 422/404-style errors (via 200+empty or 4xx) when a sport
        # has no in-season market; surface it plainly instead of crashing the run.
        return 0, 0, f"HTTPError {e.code}: {body}"
    except Exception as e:
        return 0, 0, f"{type(e).__name__}: {str(e)[:150]}"

    if not events:
        note = "0 NBA events returned (off-season — basketball_nba game odds go dark until preseason/regular season tips off)"
        if remaining is not None:
            note += f" [{remaining} odds-api requests left this month]"
        return 0, 0, note

    n_rows = 0
    for ev in events:
        home_abbr, away_abbr = _abbr(ev["home_team"]), _abbr(ev["away_team"])
        gid = _find_or_stub_game(con, home_abbr, away_abbr, ev["commence_time"])
        books = ev.get("bookmakers") or []
        if not books:
            continue
        book = books[0]  # first bookmaker returned; fine for a free-tier single-line snapshot
        for mk in book.get("markets", []):
            mkey = mk["key"]
            for outcome in mk.get("outcomes", []):
                name = outcome.get("name")
                price = outcome.get("price")
                point = outcome.get("point")
                if mkey == "h2h":
                    selection = home_abbr if name == ev["home_team"] else (
                        away_abbr if name == ev["away_team"] else name)
                elif name in ("Over", "Under"):
                    selection = name
                else:
                    selection = home_abbr if name == ev["home_team"] else (
                        away_abbr if name == ev["away_team"] else name)
                con.execute(
                    """INSERT INTO odds(game_id,book,market,selection,line,price,fetched_at)
                       VALUES(?,?,?,?,?,?,datetime('now'))
                       ON CONFLICT(game_id,book,market,selection) DO UPDATE SET
                         line=excluded.line, price=excluded.price, fetched_at=excluded.fetched_at""",
                    (gid, book["key"], mkey, selection, point, price),
                )
                n_rows += 1
    con.commit()
    note = f"{len(events)} NBA event(s), {n_rows} odds rows stored"
    if remaining is not None:
        note += f" [{remaining} odds-api requests left this month]"
    return len(events), n_rows, note


def game_odds_dict(con, game_id):
    """Assemble the {ml_home, ml_away, spread_line, ...} dict engine.edge.recommend expects,
    from whatever rows are stored for this game_id (first bookmaker wins ties)."""
    g = con.execute("SELECT home_team,away_team FROM games WHERE game_id=?", (game_id,)).fetchone()
    if not g:
        return {}
    home, away = g["home_team"], g["away_team"]
    rows = con.execute(
        "SELECT market,selection,line,price FROM odds WHERE game_id=? ORDER BY fetched_at DESC",
        (game_id,),
    ).fetchall()
    out = {}
    for r in rows:
        m, sel, line, price = r["market"], r["selection"], r["line"], r["price"]
        if m == "h2h":
            if sel == home and "ml_home" not in out:
                out["ml_home"] = price
            elif sel == away and "ml_away" not in out:
                out["ml_away"] = price
        elif m == "spreads":
            if sel == home and "spread_line" not in out:
                out["spread_line"] = line
                out["spread_home_odds"] = price
            elif sel == away and "spread_away_odds" not in out:
                out["spread_away_odds"] = price
        elif m == "totals":
            if sel == "Over" and "total_line" not in out:
                out["total_line"] = line
                out["over_odds"] = price
            elif sel == "Under" and "under_odds" not in out:
                out["under_odds"] = price
    return out


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="h2h,spreads,totals")
    a = ap.parse_args()
    con = connect()
    n_ev, n_rows, note = fetch_upcoming(con, markets=a.markets)
    print(f"odds fetch: {note}")
