#!/usr/bin/env python3
"""Pre-game news — ESPN's public NBA news feed (free, no key).

Two calls per sweep: the league-wide feed (50 newest articles, each tagged with the
teams it is about) plus a team-scoped feed for every team on the upcoming slate, so a
quiet team still gets its last few headlines. Stored in prematch_news keyed by team;
models/predict.py puts each side's last-72h headlines into the shared prompt.

Headlines + ESPN's one-paragraph description only — no article bodies, no scraping.
Text from the feed is DATA for the prompt, never instructions.

  python3 data/fetch_news.py            # league feed + every team with a game in the next 2 days
  python3 data/fetch_news.py --team LAL
"""
import argparse
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import connect  # noqa: E402
from data import teams as T  # noqa: E402

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
# ESPN numeric team ids (from /teams, 2026-11-05) keyed by nba_api abbreviation.
ESPN_TEAM_ID = {
    "ATL": 1, "BOS": 2, "NOP": 3, "CHI": 4, "CLE": 5, "DAL": 6, "DEN": 7, "DET": 8, "GSW": 9,
    "HOU": 10, "IND": 11, "LAC": 12, "LAL": 13, "MIA": 14, "MIL": 15, "MIN": 16, "BKN": 17,
    "NYK": 18, "ORL": 19, "PHI": 20, "PHX": 21, "POR": 22, "SAC": 23, "SAS": 24, "OKC": 25,
    "UTA": 26, "WAS": 27, "TOR": 28, "MEM": 29, "CHA": 30,
}
KEEP_DAYS = 10


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "nba-betting/1.0 (+mini)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _store(con, articles, fallback_team=None):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    for a in articles:
        teams = {T.abbr_from_name(c.get("description")) for c in a.get("categories", []) if c.get("type") == "team"}
        teams = {t for t in teams if t in ESPN_TEAM_ID}
        if not teams and fallback_team:
            teams = {fallback_team}
        if not teams:
            continue
        headline = (a.get("headline") or "").strip()
        desc = (a.get("description") or "").strip()
        if not headline:
            continue
        link = ((a.get("links") or {}).get("web") or {}).get("href", "")
        for team in teams:
            cur = con.execute(
                """INSERT OR IGNORE INTO prematch_news(game_id,team,source,fetched_at,raw_text,headline,published,url)
                   VALUES(NULL,?,'espn',?,?,?,?,?)""",
                (team, now, desc[:400], headline[:200], a.get("published"), link),
            )
            n += max(cur.rowcount, 0)
    con.commit()
    return n


def fetch_league(con):
    d = _get(f"{BASE}/news?limit=50")
    return _store(con, d.get("articles", []))


def fetch_team(con, team):
    tid = ESPN_TEAM_ID.get(team)
    if not tid:
        return 0
    d = _get(f"{BASE}/news?team={tid}&limit=10")
    return _store(con, d.get("articles", []), fallback_team=team)


def fetch_for_upcoming(con, days=2):
    """League feed + team feeds for every team playing in the next `days` days."""
    today = datetime.datetime.now(T.ET).date()
    end = (today + datetime.timedelta(days=days)).isoformat()
    teams = set()
    for r in con.execute(
        "SELECT home_team, away_team FROM games WHERE status='scheduled' AND game_date BETWEEN ? AND ?",
        (today.isoformat(), end),
    ):
        teams.update((r["home_team"], r["away_team"]))
    n = fetch_league(con)
    for t in sorted(teams):
        try:
            n += fetch_team(con, t)
        except Exception as e:  # noqa: BLE001
            print(f"   news for {t} failed: {type(e).__name__}: {str(e)[:80]}")
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("DELETE FROM prematch_news WHERE source='espn' AND published < ?", (cutoff,))
    con.commit()
    return n, len(teams)


def news_lines(con, team, hours=72, limit=5):
    """Most recent headlines for one team, newest first, as prompt-ready strings."""
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = con.execute(
        "SELECT headline, raw_text, published FROM prematch_news WHERE team=? AND published >= ? "
        "ORDER BY published DESC LIMIT ?", (team, since, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = (r["published"] or "")[:10]
        txt = r["headline"] + (f" — {r['raw_text']}" if r["raw_text"] else "")
        out.append(f"[{d}] {txt[:260]}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--team")
    a = ap.parse_args()
    con = connect()
    if a.team:
        print("stored", fetch_team(con, a.team), "for", a.team)
        for line in news_lines(con, a.team):
            print("  ", line)
    else:
        n, nt = fetch_for_upcoming(con)
        print(f"stored {n} team-article rows (league feed + {nt} team feeds)")
        print("rows total:", con.execute("SELECT COUNT(*) FROM prematch_news").fetchone()[0])
        for t in ("LAL", "GSW"):
            print(t)
            for line in news_lines(con, t):
                print("  ", line)
