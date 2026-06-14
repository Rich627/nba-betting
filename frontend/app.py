#!/usr/bin/env python3
"""Public read-only dashboard (FastAPI) for the NBA betting predictor.
No auth — no sensitive info (just games / predictions / model record), like lyihub.
Run: uvicorn frontend.app:app --host 0.0.0.0 --port 8800   (access via Tailscale/SSH tunnel)
JSON API: /api/games  /api/models  ; UI: /
"""
import sqlite3, os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

DB = os.path.join(os.path.dirname(__file__), "..", "nba.db")
app = FastAPI(title="NBA Predictor")

def q(sql, args=()):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    try: return [dict(r) for r in con.execute(sql, args)]
    finally: con.close()

@app.get("/api/games")
def games():
    return q("SELECT game_date,away_team,away_score,home_team,home_score,status FROM games ORDER BY game_date DESC LIMIT 50")

@app.get("/api/models")
def models():
    return q("SELECT model,bets,wins,pnl FROM model_record ORDER BY pnl DESC")

@app.get("/api/predictions")
def predictions():
    return q("SELECT game_id,model,market,pick,confidence FROM predictions ORDER BY created_at DESC LIMIT 100")

@app.get("/", response_class=HTMLResponse)
def home():
    rows = games()
    trs = "".join(
        f"<tr><td>{g['game_date']}</td><td>{g['away_team']} {g['away_score'] if g['away_score'] is not None else ''}</td>"
        f"<td>@</td><td>{g['home_team']} {g['home_score'] if g['home_score'] is not None else ''}</td>"
        f"<td>{g['status']}</td></tr>" for g in rows) or "<tr><td colspan=5>no games yet</td></tr>"
    return f"""<!doctype html><html><head><meta charset=utf-8><title>NBA Predictor</title>
<style>body{{font-family:system-ui;margin:2rem;background:#0f1115;color:#e6e6e6}}h1{{font-size:1.4rem}}
table{{border-collapse:collapse;width:100%;max-width:760px}}td,th{{padding:.4rem .7rem;border-bottom:1px solid #2a2d34;text-align:left}}
.muted{{color:#8a8f98;font-size:.85rem}}</style></head><body>
<h1>🏀 NBA Predictor <span class=muted>— games & model picks</span></h1>
<p class=muted>Read-only. Model predictions + edge vs line appear here once odds/models are wired.</p>
<table><tr><th>Date</th><th>Away</th><th></th><th>Home</th><th>Status</th></tr>{trs}</table>
<p class=muted>API: /api/games · /api/models · /api/predictions</p></body></html>"""
