#!/usr/bin/env python3
"""Settlement + model track-record (port of WC model-wallet concept).
After games go final: grade each model's prediction vs the result, update model_record (bets/wins/pnl).
Grades the moneyline pick @ a flat assumed price (or stored odds) — extend to spread/total later.
Pure logic; reads/writes the SQLite DB.
"""
import sqlite3, os, argparse
DB = os.path.join(os.path.dirname(__file__), "..", "nba.db")

def settle(con, flat_odds=1.91, stake=100):
    """For each final game with predictions, grade moneyline pick; update model_record."""
    rows = con.execute("""SELECT p.model,p.game_id,p.pick,g.home_team,g.away_team,g.home_score,g.away_score
        FROM predictions p JOIN games g ON g.game_id=p.game_id
        WHERE g.status='final' AND p.market='moneyline'""").fetchall()
    rec = {}  # model -> [bets, wins, pnl]
    for model, gid, pick, ht, at, hs, as_ in rows:
        if hs is None or as_ is None: continue
        winner = ht if hs > as_ else at
        won = (pick == winner)
        r = rec.setdefault(model, [0,0,0.0])
        r[0]+=1; r[1]+= 1 if won else 0; r[2]+= stake*(flat_odds-1) if won else -stake
    for model,(b,w,pnl) in rec.items():
        con.execute("""INSERT INTO model_record(model,bets,wins,pnl,updated_at) VALUES(?,?,?,?,datetime('now'))
            ON CONFLICT(model) DO UPDATE SET bets=excluded.bets,wins=excluded.wins,pnl=excluded.pnl,updated_at=datetime('now')""",
            (model,b,w,round(pnl,1)))
    con.commit(); return rec

def weights_from_record(con):
    """Convert track record into consensus weights (shift min->0, normalize). Feeds edge.weighted_consensus."""
    rows = con.execute("SELECT model,pnl FROM model_record").fetchall()
    if not rows: return {}
    mn = min(p for _,p in rows)
    shifted = {m:(p-mn+1) for m,p in rows}  # +1 so worst still has tiny weight
    tot = sum(shifted.values()) or 1
    return {m: round(v/tot*len(rows),3) for m,v in shifted.items()}

if __name__ == "__main__":
    con = sqlite3.connect(DB)
    # SELF-TEST: inject mock moneyline predictions for the real Finals games, settle, show record
    finals = con.execute("SELECT game_id,home_team,away_team,home_score,away_score FROM games WHERE status='final'").fetchall()
    if finals:
        # mock: 'AlwaysHome' picks home, 'AlwaysNYK' picks NYK, 'AlwaysSAS' picks SAS
        for gid,ht,at,hs,as_ in finals:
            for model,pick in (("AlwaysHome",ht),("AlwaysNYK","NYK"),("AlwaysSAS","SAS")):
                con.execute("INSERT OR REPLACE INTO predictions(game_id,model,market,pick,created_at) VALUES(?,?,'moneyline',?,datetime('now'))",(gid,model,pick))
        con.commit()
        rec = settle(con)
        print("settled mock predictions vs real Finals results:")
        for m,(b,w,pnl) in sorted(rec.items(), key=lambda x:-x[1][2]):
            print(f"  {m:10} {w}/{b} wins  pnl {pnl:+.0f}")
        print("derived weights:", weights_from_record(con))
    else:
        print("no final games — run data/fetch_results.py first")
