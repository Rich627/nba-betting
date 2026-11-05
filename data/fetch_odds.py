#!/usr/bin/env python3
"""Odds layer — the-odds-api.com basketball_nba (FREE tier: 500 credits/month).
Needs ODDS_API_KEY. One bulk call = (number of markets) credits, regardless of how
many games it returns: h2h,spreads,totals = 3 credits.

Every bookmaker's price is stored (odds PK = game_id/book/market/selection, upserted),
and game_odds_dict() collapses them to the MEDIAN across books. Median, not max: the
MLB sibling learned on 2026-07-25 that one bad book (betrivers quoting 8.5/1.04 on a
~1.9/1.9 game) turns "best price" into fake +591% edges.

Games are keyed by data.teams.game_key(ET date, home, away) — the same id ESPN's
scoreboard produces — so odds attach to the schedule row directly; if the odds feed
lists a game ESPN hasn't shown yet, a 'scheduled' row is created with the same key.

  python3 data/fetch_odds.py                      # h2h,spreads,totals (3 credits)
  python3 data/fetch_odds.py --markets h2h        # 1 credit
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import connect  # noqa: E402
from data import teams as T  # noqa: E402

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "basketball_nba"


def _find_or_create_game(con, home, away, commence_iso, event_id):
    gdate = T.et_date(commence_iso)
    gid = T.game_key(gdate, home, away)
    row = con.execute("SELECT game_id FROM games WHERE game_id=?", (gid,)).fetchone()
    if row:
        con.execute(
            "UPDATE games SET odds_event_id=?, commence_time=COALESCE(commence_time, ?) WHERE game_id=?",
            (event_id, T.iso_utc(T.parse_utc(commence_iso)), gid),
        )
        return gid
    # Same matchup within a day but under a different key (should not happen with ET
    # dating on both sides, kept as a guard against a source shifting a tip-off date).
    row = con.execute(
        "SELECT game_id FROM games WHERE home_team=? AND away_team=? AND status!='final' "
        "AND game_date BETWEEN date(?, '-1 day') AND date(?, '+1 day')",
        (home, away, gdate, gdate),
    ).fetchone()
    if row:
        con.execute("UPDATE games SET odds_event_id=? WHERE game_id=?", (event_id, row["game_id"]))
        return row["game_id"]
    con.execute(
        """INSERT INTO games(game_id,game_date,season,home_team,away_team,status,commence_time,odds_event_id,updated_at)
           VALUES(?,?,?,?,?,'scheduled',?,?,datetime('now'))""",
        (gid, gdate, "", home, away, T.iso_utc(T.parse_utc(commence_iso)), event_id),
    )
    return gid


def fetch_upcoming(con, regions="us", markets="h2h,spreads,totals", timeout=25):
    """Fetch current NBA odds. Returns (n_events, n_odds_rows, note, events)."""
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return 0, 0, "ODDS_API_KEY not set", []
    url = (f"{ODDS_API_BASE}/sports/{SPORT_KEY}/odds/"
           f"?apiKey={key}&regions={regions}&markets={markets}&oddsFormat=decimal")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            events = json.load(r)
            remaining = r.headers.get("x-requests-remaining")
    except urllib.error.HTTPError as e:
        return 0, 0, f"HTTPError {e.code}: {e.read().decode()[:200]}", []
    except Exception as e:  # noqa: BLE001
        return 0, 0, f"{type(e).__name__}: {str(e)[:150]}", []

    if not events:
        note = "0 NBA events (no market open right now)"
        if remaining is not None:
            note += f" [{remaining} credits left]"
        return 0, 0, note, []

    n_rows = n_live = 0
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now = T.iso_utc(now_dt)
    for ev in events:
        home, away = T.abbr_from_name(ev["home_team"]), T.abbr_from_name(ev["away_team"])
        gid = _find_or_create_game(con, home, away, ev["commence_time"], ev.get("id"))
        if T.parse_utc(ev["commence_time"]) <= now_dt:
            # In-play prices (1.01 / 23.0 with five minutes left) must never overwrite the
            # pre-game line we grade against — the MLB sibling's CLV was contaminated this way.
            n_live += 1
            continue
        for book in ev.get("bookmakers") or []:
            for mk in book.get("markets", []):
                mkey = mk["key"]
                for oc in mk.get("outcomes", []):
                    name, price, point = oc.get("name"), oc.get("price"), oc.get("point")
                    if name in ("Over", "Under"):
                        sel = name
                    elif name == ev["home_team"]:
                        sel = home
                    elif name == ev["away_team"]:
                        sel = away
                    else:
                        sel = T.abbr_from_name(name)
                    con.execute(
                        """INSERT INTO odds(game_id,book,market,selection,line,price,fetched_at)
                           VALUES(?,?,?,?,?,?,?)
                           ON CONFLICT(game_id,book,market,selection) DO UPDATE SET
                             line=excluded.line, price=excluded.price, fetched_at=excluded.fetched_at""",
                        (gid, book["key"], mkey, sel, point, price, now),
                    )
                    n_rows += 1
    con.commit()
    note = f"{len(events)} NBA event(s), {n_rows} odds rows stored, {n_live} in-play skipped"
    if remaining is not None:
        note += f" [{remaining} credits left]"
    return len(events), n_rows, note, events


def game_odds_dict(con, game_id):
    """{ml_home, ml_away, spread_line (home line), spread_home_odds, spread_away_odds,
        total_line, over_odds, under_odds, n_books, fetched_at} — MEDIAN across books.
    Empty dict when no odds are stored for the game."""
    g = con.execute("SELECT home_team,away_team FROM games WHERE game_id=?", (game_id,)).fetchone()
    if not g:
        return {}
    home, away = g["home_team"], g["away_team"]
    rows = con.execute(
        "SELECT book,market,selection,line,price,fetched_at FROM odds WHERE game_id=?", (game_id,)
    ).fetchall()
    if not rows:
        return {}
    prices, lines, books = {}, {}, set()
    latest = max(r["fetched_at"] or "" for r in rows)
    for r in rows:
        k = (r["market"], r["selection"])
        if r["price"] is not None:
            prices.setdefault(k, []).append(float(r["price"]))
        if r["line"] is not None:
            lines.setdefault(k, []).append(float(r["line"]))
        books.add(r["book"])

    def med(d, k):
        v = d.get(k)
        return round(median(v), 3) if v else None

    out = {"n_books": len(books), "fetched_at": latest}
    if med(prices, ("h2h", home)):
        out["ml_home"] = med(prices, ("h2h", home))
        out["ml_away"] = med(prices, ("h2h", away))
    if med(lines, ("spreads", home)) is not None:
        out["spread_line"] = med(lines, ("spreads", home))
        out["spread_home_odds"] = med(prices, ("spreads", home))
        out["spread_away_odds"] = med(prices, ("spreads", away))
    if med(lines, ("totals", "Over")) is not None:
        out["total_line"] = med(lines, ("totals", "Over"))
        out["over_odds"] = med(prices, ("totals", "Over"))
        out["under_odds"] = med(prices, ("totals", "Under"))
    return out


def odds_summary(o: dict, home: str, away: str) -> str:
    """One-line human summary for prompts and WhatsApp."""
    if not o:
        return "無盤口"
    parts = []
    if o.get("ml_home"):
        parts.append(f"獨贏 {T.zh(home)} {o['ml_home']:.2f} / {T.zh(away)} {o['ml_away']:.2f}")
    if o.get("spread_line") is not None:
        parts.append(f"讓分 {T.zh(home)} {o['spread_line']:+g} ({o.get('spread_home_odds') or 0:.2f})")
    if o.get("total_line") is not None:
        parts.append(f"大小 {o['total_line']:g}")
    return " · ".join(parts) + f"（{o.get('n_books', 0)} 家中位數）"


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="h2h,spreads,totals")
    a = ap.parse_args()
    con = connect()
    n_ev, n_rows, note, events = fetch_upcoming(con, markets=a.markets)
    print(f"odds fetch: {note}")
    for ev in events[:5]:
        home, away = T.abbr_from_name(ev["home_team"]), T.abbr_from_name(ev["away_team"])
        gid = T.game_key(T.et_date(ev["commence_time"]), home, away)
        print(" ", gid, T.tw_label(ev["commence_time"]), odds_summary(game_odds_dict(con, gid), home, away))
