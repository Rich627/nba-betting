#!/usr/bin/env python3
"""Build docs/data/site.json for the public GitHub Pages site from nba.db.

Read-only on the DB. Everything the page shows comes from here: model wallets +
history, upcoming games with per-model picks / consensus / edge recs, and the last
two weeks of results with per-model grading. No keys, no network.

  python3 pipeline/build_site_json.py            # -> docs/data/site.json
  python3 pipeline/build_site_json.py --out /tmp/site.json
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from data import teams as T  # noqa: E402
from data.db import connect  # noqa: E402
from data.fetch_odds import game_odds_dict  # noqa: E402
from engine.edge import weighted_consensus  # noqa: E402
from models.predict import MODELS  # noqa: E402

OUT = os.path.join(ROOT, "docs", "data", "site.json")
RESULT_DAYS = 14


def _pick_label(home, away, pick, market, line=None):
    side = pick.split(" ")[0]
    team = T.zh(home) if side == "home" else T.zh(away)
    if market == "moneyline":
        return f"獨贏 {team}"
    if market == "spread":
        return f"讓分 {team} {pick.split(' ')[1]}"
    return f"總分 {'大' if pick == 'over' else '小'} {line:g}" if line is not None else "總分"


def _models_for(con, gid, home, away):
    rows = con.execute(
        "SELECT model,pick,win_prob,pred_margin,pred_total,confidence,reasoning,odds,result,pnl "
        "FROM predictions WHERE game_id=? AND market='moneyline'", (gid,)
    ).fetchall()
    out, preds = [], {}
    for r in rows:
        out.append({
            "name": r["model"], "pick": r["pick"], "pick_zh": T.zh(r["pick"]),
            "home_win_prob": r["win_prob"], "pred_margin": r["pred_margin"], "pred_total": r["pred_total"],
            "confidence": r["confidence"], "reasoning": r["reasoning"], "odds": r["odds"],
            "result": r["result"], "pnl": r["pnl"],
        })
        if r["win_prob"] is not None and r["pred_total"] is not None:
            m = r["pred_margin"] or 0
            preds[r["model"]] = {"home_win_prob": r["win_prob"],
                                 "pred_home_score": (r["pred_total"] + m) / 2,
                                 "pred_away_score": (r["pred_total"] - m) / 2,
                                 "pred_total": r["pred_total"]}
    order = list(MODELS)
    out.sort(key=lambda m: order.index(m["name"]) if m["name"] in order else 99)
    return out, (weighted_consensus(preds) if preds else None)


def _recs_for(con, gid, home, away):
    return [{
        "market": r["market"], "pick": r["pick"], "label": _pick_label(home, away, r["pick"], r["market"], r["line"]),
        "odds": r["odds"], "line": r["line"], "edge": r["edge"], "confidence": r["confidence"], "why": r["why"],
        "result": r["result"], "pnl": r["pnl"],
    } for r in con.execute("SELECT * FROM recommendations WHERE game_id=? ORDER BY market", (gid,))]


def _game(con, g):
    home, away = g["home_team"], g["away_team"]
    models, consensus = _models_for(con, g["game_id"], home, away)
    d = {
        "game_id": g["game_id"], "game_date": g["game_date"], "status": g["status"],
        "commence_time": g["commence_time"], "tw": T.local_label(g["commence_time"]) if g["commence_time"] else "",
        "home": home, "away": away, "home_zh": T.zh(home), "away_zh": T.zh(away),
        "home_name": T.ABBR_TO_FULL.get(home, home), "away_name": T.ABBR_TO_FULL.get(away, away),
        "home_score": g["home_score"], "away_score": g["away_score"],
        "odds": game_odds_dict(con, g["game_id"]) or None,
        "consensus": consensus, "models": models, "recs": _recs_for(con, g["game_id"], home, away),
    }
    if consensus:
        fav = home if consensus["home_win_prob"] >= 0.5 else away
        d["consensus"]["fav"] = fav
        d["consensus"]["fav_zh"] = T.zh(fav)
        d["consensus"]["fav_prob"] = consensus["home_win_prob"] if fav == home else round(1 - consensus["home_win_prob"], 3)
    graded = [m for m in models if m["result"] in ("WON", "LOST")]
    d["hits"] = f"{sum(1 for m in graded if m['result'] == 'WON')}/{len(graded)}" if graded else None
    return d


def build(con):
    now = datetime.datetime.now(datetime.timezone.utc)
    # live games + anything tipping in the next 48h (the odds feed also lists far-off
    # showcase dates like Christmas; those stay out until they are near).
    upcoming = [_game(con, g) for g in con.execute(
        "SELECT * FROM games WHERE status IN ('scheduled','live') AND commence_time IS NOT NULL "
        "AND commence_time BETWEEN ? AND ? ORDER BY commence_time",
        ((now - datetime.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         (now + datetime.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    )]
    since = (now.date() - datetime.timedelta(days=RESULT_DAYS)).isoformat()
    results = [_game(con, g) for g in con.execute(
        "SELECT * FROM games WHERE status='final' AND game_date >= ? AND home_score IS NOT NULL "
        "ORDER BY commence_time DESC", (since,),
    )]
    wallet = [dict(r) for r in con.execute(
        "SELECT model,bets,wins,pnl FROM model_record WHERE bets>0 ORDER BY pnl DESC")]
    hist_rows = con.execute(
        "SELECT as_of_date,model,pnl FROM model_wallet_history ORDER BY as_of_date").fetchall()
    dates = sorted({r["as_of_date"] for r in hist_rows})
    series = {}
    for r in hist_rows:
        series.setdefault(r["model"], {})[r["as_of_date"]] = r["pnl"]
    history = {"dates": dates, "series": {m: [vals.get(d) for d in dates] for m, vals in series.items()}}
    recs_graded = con.execute(
        "SELECT COUNT(*) AS bets, COALESCE(SUM(result='WON'),0) AS wins, COALESCE(SUM(pnl),0) AS pnl "
        "FROM recommendations WHERE result IN ('WON','LOST') AND pnl IS NOT NULL"
    ).fetchone()
    summary = {
        "n_predicted_games": con.execute("SELECT COUNT(DISTINCT game_id) FROM predictions WHERE market='moneyline'").fetchone()[0],
        "n_recs": con.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0],
        "recs_won": recs_graded["wins"],
        "recs_graded": recs_graded["bets"],
        "recs_pnl": round(recs_graded["pnl"], 1),
    }
    return {
        "generated_at": now.astimezone(T.DISPLAY_TZ).isoformat(timespec="seconds"),
        "timezone": str(T.DISPLAY_TZ),
        "season": "2026-27",
        "models": list(MODELS),
        "stake": 100,
        "summary": summary,
        "wallet": wallet,
        "wallet_history": history,
        "upcoming": upcoming,
        "results": results,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    data = build(connect())
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"wrote {a.out}: {len(data['upcoming'])} upcoming, {len(data['results'])} results, "
          f"{len(data['wallet'])} wallets, {len(data['wallet_history']['dates'])} history days")
