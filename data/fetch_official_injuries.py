#!/usr/bin/env python3
"""Official NBA injury report — the league's own PDF, FREE, no key.

The league publishes a new report every 15 minutes on game days at
  https://ak-static.cms.nba.com/referee/injury/Injury-Report_<YYYY-MM-DD>_<HH>_<MM><AM|PM>.pdf
(Eastern time in the file name). It carries the statuses ESPN collapses into
"Day-To-Day": Probable / Questionable / Doubtful / Out / Available, plus a reason and
"NOT YET SUBMITTED" for teams that have not filed yet.

The PDF is plain WinAnsi text drawn cell by cell (GemBox), so the standard library is
enough: inflate every content stream, read each clipped table cell's x/y and its Tj
strings, and map the x position to the header column of that page.

  python3 data/fetch_official_injuries.py            # fetch latest, print + store
"""
import datetime
import os
import re
import sys
import urllib.error
import urllib.request
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import teams as T  # noqa: E402

BASE = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{}.pdf"
UA = {"User-Agent": "Mozilla/5.0 (nba-betting injury check)"}
HEADERS = {
    "Game Date": "game_date", "Game Time": "game_time", "Matchup": "matchup",
    "Team": "team", "Player Name": "player", "Current Status": "status", "Reason": "reason",
}
STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
TOKEN = re.compile(
    rb"(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) re\s+W\*? n"  # cell clip rectangle
    rb"|\(((?:\\.|[^\\)])*)\) Tj"                                  # a text run
    rb"|\bBT\b|\bET\b"
)


def et_now(now=None):
    return (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(T.ET)


def report_stamp(dt_et):
    """datetime (ET) -> the file-name stamp, e.g. 2026-11-06_06_15PM."""
    return dt_et.strftime("%Y-%m-%d_%I_%M%p")


def candidate_stamps(now=None, steps=16):
    """Newest-first stamps on the 15-minute grid, going back `steps` slots (4h)."""
    t = et_now(now).replace(second=0, microsecond=0)
    t = t.replace(minute=t.minute - t.minute % 15)
    return [report_stamp(t - datetime.timedelta(minutes=15 * i)) for i in range(steps)]


def _unescape(raw):
    out = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
                                                  b"f": b"\f"}.get(m.group(1), m.group(1)), raw)
    out = re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8) & 0xFF]), out)
    return out.decode("cp1252", errors="replace")


def _cells(content):
    """[(x, y, text)] for every text block drawn inside a clipped cell, in draw order."""
    clip, runs, out = None, [], []
    for m in TOKEN.finditer(content):
        g = m.group(0)
        if m.group(1) is not None:
            clip = (round(float(m.group(1))), round(float(m.group(2))))
        elif m.group(5) is not None:
            runs.append(_unescape(m.group(5)))
        elif g == b"BT":
            runs = []
        elif g == b"ET" and runs and clip:
            out.append((clip[0], clip[1], " ".join(runs).strip()))
            runs = []
    return out


def _is_chrome(text):
    """Page title / page number cells repeated at the top and bottom of every page."""
    return text.startswith("Injury Report:") or bool(re.match(r"Page \d+ of \d+$", text))


def _page_rows(cells, cols):
    """One page's cells -> list of {column: text} rows. The header row is printed on the
    first page only, so `cols` ({x: column}) is learned there and reused on later pages."""
    for x, _y, text in cells:
        if text in HEADERS:
            cols[x] = HEADERS[text]
    if "player" not in cols.values():
        return []
    header_ys = [y for _x, y, t in cells if t in HEADERS]
    top = min(header_ys) if header_ys else None
    xs = sorted(cols)

    def column(x):
        best = max((c for c in xs if c <= x + 2), default=None)
        return cols.get(best) if best is not None else None

    rows, seen = {}, set()
    for x, y, text in cells:
        if _is_chrome(text) or text in HEADERS or (top is not None and y <= top) or (x, y, text) in seen:
            continue
        seen.add((x, y, text))
        col = column(x)
        if not col:
            continue
        row = rows.setdefault(y, {})
        row[col] = (row[col] + " " + text) if col in row else text
    return [rows[y] for y in sorted(rows)]


def flip_name(name):
    """'Cenac Jr., Christopher' -> 'Christopher Cenac Jr.'; anything else unchanged."""
    if "," not in name:
        return name.strip()
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}".strip()


def parse_pdf(data):
    """PDF bytes -> list of dicts {game_date, game_time, matchup, team, player, status, reason}.
    Values that the report only prints on a group's first row are carried forward.
    A team that has not filed yet gets one row with status 'NOT YET SUBMITTED'."""
    items, carry, cols = [], {}, {}
    for raw in STREAM.findall(data):
        try:
            content = zlib.decompress(raw)
        except zlib.error:
            continue
        if b"Tj" not in content:
            continue
        for row in _page_rows(_cells(content), cols):
            for k in ("game_date", "game_time", "matchup", "team"):
                if row.get(k):
                    carry[k] = row[k]
            rec = {k: carry.get(k) for k in ("game_date", "game_time", "matchup", "team")}
            reason = (row.get("reason") or "").strip()
            if "NOT YET SUBMITTED" in (row.get("reason", "") + row.get("player", "")).upper():
                items.append({**rec, "player": None, "status": "NOT YET SUBMITTED", "reason": ""})
                continue
            if not row.get("player"):
                if set(row) == {"reason"} and items:  # a reason wrapped onto the next row/page
                    items[-1]["reason"] = (items[-1]["reason"] + " " + reason).strip()
                continue
            items.append({**rec, "player": flip_name(row["player"]),
                          "status": (row.get("status") or "").strip(), "reason": "" if reason == "-" else reason})
    return items


def report_label(data):
    """'Injury Report: 11/06/26 06:15 PM' title cell -> '11/06/26 06:15 PM ET'."""
    for raw in STREAM.findall(data):
        try:
            content = zlib.decompress(raw)
        except zlib.error:
            continue
        for _x, _y, text in _cells(content):
            if text.startswith("Injury Report:"):
                return text.split(":", 1)[1].strip() + " ET"
    return None


def download_latest(now=None, timeout=20, opener=None):
    """(stamp, pdf_bytes) for the newest report on the 15-minute grid, or (None, None)."""
    opener = opener or urllib.request.urlopen
    for stamp in candidate_stamps(now):
        try:
            with opener(urllib.request.Request(BASE.format(stamp), headers=UA), timeout=timeout) as r:
                data = r.read()
            if data.startswith(b"%PDF"):
                return stamp, data
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                continue
            raise
    return None, None


def store(con, items, stamp, label):
    """Replace official_injuries with this report. Rows keep the abbreviation, the
    report stamp and the reason; G League assignments are dropped (not rotation news)."""
    con.execute("DELETE FROM official_injuries")
    n = 0
    for it in items:
        team = T.abbr_from_name(it.get("team") or "")
        if not team or team == it.get("team"):
            continue
        if (it.get("reason") or "").startswith("G League"):
            continue
        con.execute(
            "INSERT OR REPLACE INTO official_injuries(team,player,status,reason,matchup,game_date,report,report_label) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (team, it.get("player") or "", it["status"], (it.get("reason") or "")[:200], it.get("matchup"),
             it.get("game_date"), stamp, label),
        )
        n += 1
    con.commit()
    return n


def fetch(con, now=None):
    """Download + parse + store the newest report. Returns (rows, label) or (0, None)."""
    stamp, data = download_latest(now)
    if not data:
        return 0, None
    items = parse_pdf(data)
    label = report_label(data) or stamp
    return store(con, items, stamp, label), label


if __name__ == "__main__":
    from data.db import connect
    con = connect()
    n, label = fetch(con)
    print(f"official report {label}: {n} rows")
    for r in con.execute("SELECT team,player,status,reason FROM official_injuries ORDER BY team LIMIT 40"):
        print(" ", tuple(r))
