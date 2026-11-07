#!/usr/bin/env python3
"""One injury view for a game, shared by the prompt, the injury module and the re-check.

Sources, best first:
  1. official_injuries  — the league's 15-minute PDF (data/fetch_official_injuries.py).
     Used for a team when the latest report has that team's game on the game's ET date
     and the team has actually filed (not "NOT YET SUBMITTED").
  2. team_injuries      — ESPN (data/fetch_espn.py). Only Out / Day-To-Day.

Role weight comes from player_season_stats (ESPN season averages, one free call per run).
A player with no minutes this season is already missing from the team's form, so his
absence carries no new information and weighs 0.

snapshot() is stored per game at prediction time (injury_snapshots, kind='predict') so
the pre-tip re-check can diff against exactly what the models saw.
"""
import datetime
import json
import os
import re
import sys
import unicodedata
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import teams as T  # noqa: E402

# status -> probability the player misses the game (used for minute-weighted impact)
STATUS_W = {
    "out": 1.0, "doubtful": 0.75, "questionable": 0.4, "gtd": 0.4, "day-to-day": 0.4,
    "probable": 0.1, "available": 0.0,
}
UNKNOWN_MIN = 12.0      # role guess when no season stats are loaded at all
MATERIAL_MIN = 8.0      # expected-minutes swing that counts as real news
STATS_URL = ("https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/statistics/byathlete"
             "?region=us&lang=en&contentorigin=espn&isqualified=false&limit=1000&season={season}&seasontype=2")
_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm_name(name):
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z ]", " ", s.replace("'", ""))
    return " ".join(_SUFFIX.sub(" ", s).split())


def weight(status):
    return STATUS_W.get((status or "").strip().lower(), 0.3)


# ----------------------------------------------------------------------------- season stats
def season_for(now=None):
    """ESPN labels the 2026-27 season 2027."""
    d = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(T.ET)
    return d.year + 1 if d.month >= 8 else d.year


def fetch_season_stats(con, now=None, timeout=25):
    """Replace player_season_stats from ESPN's league-wide per-athlete averages."""
    req = urllib.request.Request(STATS_URL.format(season=season_for(now)), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    cats = {c["name"]: c.get("names", []) for c in d.get("categories", [])}
    gen = cats.get("general", [])
    off = cats.get("offensive", [])
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for a in d.get("athletes", []):
        ath = a.get("athlete") or {}
        vals = {c["name"]: c.get("values", []) for c in a.get("categories", [])}

        def stat(cat, names, key):
            try:
                return float(vals[cat][names.index(key)])
            except (KeyError, ValueError, IndexError, TypeError):
                return None
        team = T.abbr_from_espn((ath.get("teamShortName") or "").upper())
        if not ath.get("displayName") or not team:
            continue
        rows.append((norm_name(ath["displayName"]), ath["displayName"], team,
                     stat("general", gen, "gamesPlayed"), stat("general", gen, "avgMinutes"),
                     stat("offensive", off, "avgPoints"), stamp))
    if not rows:
        return 0
    con.execute("DELETE FROM player_season_stats")
    con.executemany(
        "INSERT OR REPLACE INTO player_season_stats(player_key,player,team,gp,avg_min,avg_pts,as_of) "
        "VALUES(?,?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


def minutes_lookup(con, team):
    """(minutes-by-normalized-name, stats_loaded) for a team."""
    rows = con.execute("SELECT player_key, avg_min FROM player_season_stats WHERE team=?", (team,)).fetchall()
    return {r["player_key"]: float(r["avg_min"] or 0.0) for r in rows}, bool(rows)


def role_minutes(con, team, player):
    mins, loaded = minutes_lookup(con, team)
    if not loaded:
        return UNKNOWN_MIN
    return mins.get(norm_name(player), 0.0)


# ----------------------------------------------------------------------------- merged view
def _official_date(game_date_et):
    """'2026-11-06' -> '11/06/2026' (the PDF's Game Date format)."""
    try:
        return datetime.date.fromisoformat(game_date_et).strftime("%m/%d/%Y")
    except (TypeError, ValueError):
        return None


def team_report(con, team, game_date_et=None):
    """(entries, source) for one team. entries: [{player, status, reason, min, source}]."""
    day = _official_date(game_date_et)
    if day:
        rows = con.execute(
            "SELECT player,status,reason,report_label FROM official_injuries WHERE team=? AND game_date=?",
            (team, day)).fetchall()
        filed = [r for r in rows if r["status"] != "NOT YET SUBMITTED"]
        if filed:
            label = filed[0]["report_label"]
            return ([{"player": r["player"], "status": r["status"], "reason": r["reason"] or "",
                      "min": round(role_minutes(con, team, r["player"]), 1), "source": "official"}
                     for r in filed], f"NBA official {label}")
    rows = con.execute("SELECT player,status,comment,as_of FROM team_injuries WHERE team=?", (team,)).fetchall()
    return ([{"player": r["player"], "status": r["status"], "reason": r["comment"] or "",
              "min": round(role_minutes(con, team, r["player"]), 1), "source": "espn"} for r in rows],
            f"ESPN {rows[0]['as_of']}" if rows else "ESPN (none listed)")


def snapshot(con, game_id):
    g = con.execute("SELECT game_id, home_team, away_team, commence_time, game_date FROM games WHERE game_id=?",
                    (game_id,)).fetchone()
    if not g:
        return None
    day = T.et_date(g["commence_time"]) if g["commence_time"] else g["game_date"]
    teams, sources = {}, {}
    for team in (g["home_team"], g["away_team"]):
        teams[team], sources[team] = team_report(con, team, day)
    return {"game_id": game_id, "taken_at": datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"), "sources": sources, "teams": teams}


def save_snapshot(con, game_id, kind, snap, extra=None):
    payload = dict(snap or {})
    if extra:
        payload.update(extra)
    con.execute(
        "INSERT INTO injury_snapshots(game_id,kind,taken_at,payload) VALUES(?,?,?,?) "
        "ON CONFLICT(game_id,kind) DO UPDATE SET taken_at=excluded.taken_at, payload=excluded.payload",
        (game_id, kind, payload.get("taken_at"), json.dumps(payload, ensure_ascii=False)))
    con.commit()


def load_snapshot(con, game_id, kind="predict"):
    r = con.execute("SELECT payload FROM injury_snapshots WHERE game_id=? AND kind=?", (game_id, kind)).fetchone()
    return json.loads(r["payload"]) if r else None


def _order(entries):
    return sorted(entries, key=lambda e: (-weight(e["status"]) * max(e["min"], 1.0), e["player"]))


def prompt_line(snap, team):
    """Compact line for the LLM prompt: rotation players first, with role and reason."""
    entries = [e for e in _order(snap["teams"].get(team, [])) if weight(e["status"]) > 0 or e["min"] >= 15]
    if not entries:
        return "none listed"
    parts = []
    for e in entries[:10]:
        role = f", {e['min']:.0f} mpg" if e["min"] else ""
        why = f" — {e['reason']}" if e["reason"] else ""
        parts.append(f"{e['player']} ({e['status']}{role}){why}")
    return "; ".join(parts) + f" [{snap['sources'].get(team, '')}]"


def impact(entries):
    """Expected rotation minutes missing for a team."""
    return sum(weight(e["status"]) * e["min"] for e in entries)


def diff(old, new, threshold=MATERIAL_MIN):
    """Material status changes between two snapshots: [{team, player, old, new, min, delta}].
    delta > 0 means the player is now MORE likely to miss (bad for his team)."""
    changes = []
    for team in new["teams"]:
        before = {norm_name(e["player"]): e for e in old["teams"].get(team, [])}
        after = {norm_name(e["player"]): e for e in new["teams"][team]}
        for key in set(before) | set(after):
            b, a = before.get(key), after.get(key)
            mins = max((a or {}).get("min", 0.0), (b or {}).get("min", 0.0))
            wb = weight(b["status"]) if b else 0.0
            wa = weight(a["status"]) if a else 0.0
            delta = (wa - wb) * mins
            if abs(delta) >= threshold:
                changes.append({"team": team, "player": (a or b)["player"],
                                "old": b["status"] if b else "not listed",
                                "new": a["status"] if a else "not listed",
                                "min": round(mins, 1), "delta": round(delta, 1)})
    return sorted(changes, key=lambda c: -abs(c["delta"]))


def unresolved(snap, min_minutes=20.0):
    """Key players still undecided (Questionable / GTD / Day-To-Day)."""
    return [{"team": t, **e} for t, es in snap["teams"].items() for e in es
            if 0.2 < weight(e["status"]) < 0.75 and e["min"] >= min_minutes]
