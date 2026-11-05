"""Team naming — ONE place that maps every source's spelling onto nba_api abbreviations.

Sources and their quirks:
  - nba_api       "Golden State Warriors" / GSW          (canonical abbreviations)
  - the-odds-api  full names, same as nba_api            ("Los Angeles Clippers")
  - ESPN          abbreviations differ for 6 teams       (GS, NO, NY, SA, UTAH, WSH)
                  and displayName "LA Clippers"

Also the canonical game id: every source creates/finds the same games row through
game_key(), so no cross-source reconciliation is ever needed:
    game_key("2026-11-05", home="DET", away="PHI") -> "2026-11-05_PHI@DET"
"""
import datetime
from zoneinfo import ZoneInfo

from nba_api.stats.static import teams as _teams

ET = ZoneInfo("America/New_York")
TW = ZoneInfo("Asia/Taipei")

TEAMS = _teams.get_teams()
FULL_TO_ABBR = {t["full_name"]: t["abbreviation"] for t in TEAMS}
ABBR_TO_FULL = {t["abbreviation"]: t["full_name"] for t in TEAMS}
ABBR_TO_NICK = {t["abbreviation"]: t["nickname"] for t in TEAMS}

NAME_ALIASES = {
    "LA Clippers": "LAC",
    "Los Angeles Clippers": "LAC",
    "LA Lakers": "LAL",
}

ESPN_ABBR = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}

# 繁體中文 (台灣慣用) 隊名
ZH = {
    "ATL": "老鷹", "BOS": "塞爾提克", "BKN": "籃網", "CHA": "黃蜂", "CHI": "公牛",
    "CLE": "騎士", "DAL": "獨行俠", "DEN": "金塊", "DET": "活塞", "GSW": "勇士",
    "HOU": "火箭", "IND": "溜馬", "LAC": "快艇", "LAL": "湖人", "MEM": "灰熊",
    "MIA": "熱火", "MIL": "公鹿", "MIN": "灰狼", "NOP": "鵜鶘", "NYK": "尼克",
    "OKC": "雷霆", "ORL": "魔術", "PHI": "76人", "PHX": "太陽", "POR": "拓荒者",
    "SAC": "國王", "SAS": "馬刺", "TOR": "暴龍", "UTA": "爵士", "WAS": "巫師",
}

WEEKDAY_ZH = "一二三四五六日"


def abbr_from_name(name: str) -> str:
    """Full team name (any source) -> nba_api abbreviation. Unknown -> returned as-is."""
    if not name:
        return name
    return NAME_ALIASES.get(name) or FULL_TO_ABBR.get(name) or name


def abbr_from_espn(abbr: str) -> str:
    return ESPN_ABBR.get(abbr, abbr)


def zh(abbr: str) -> str:
    return ZH.get(abbr, abbr)


def parse_utc(iso: str):
    """'2026-11-05T00:00Z' / '2026-11-05T00:10:00Z' / '+00:00' -> aware datetime (UTC)."""
    if not iso:
        return None
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def iso_utc(dt) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def et_date(iso: str) -> str:
    """UTC tip-off -> the US/Eastern calendar date the NBA files the game under."""
    return parse_utc(iso).astimezone(ET).date().isoformat()


def tw_label(iso: str) -> str:
    """UTC tip-off -> '11/5(四) 08:10' in Taiwan time."""
    dt = parse_utc(iso).astimezone(TW)
    return f"{dt.month}/{dt.day}({WEEKDAY_ZH[dt.weekday()]}) {dt:%H:%M}"


def game_key(et_date_str: str, home: str, away: str) -> str:
    return f"{et_date_str}_{away}@{home}"


def matchup_zh(home: str, away: str) -> str:
    return f"{zh(away)} @ {zh(home)}"
