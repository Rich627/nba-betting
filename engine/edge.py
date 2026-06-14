#!/usr/bin/env python3
"""Edge engine — turn multi-model predictions + market odds into BET RECOMMENDATIONS.
Core idea (WC lesson): recommend only where weighted model consensus beats the line (+EV edge),
NOT just the favorite. Models predict; this engine decides the bet.
Pure logic, no network — unit-testable with mock data.
"""
from statistics import mean

def weighted_consensus(preds: dict, weights: dict = None):
    """preds: {model: {winner, win_prob(home-perspective optional), pred_home_score, pred_away_score, pred_total}}
       Returns consensus dict. win_prob is HOME win prob (we normalize each model to home-perspective)."""
    rows = [(m, p) for m, p in preds.items() if isinstance(p, dict) and "error" not in p]
    if not rows: return None
    w = {m: (weights.get(m, 1.0) if weights else 1.0) for m, _ in rows}
    tot = sum(max(w[m], 0.01) for m, _ in rows)
    def wavg(f):
        return sum(max(w[m], 0.01) * f(p) for m, p in rows) / tot
    home_prob = wavg(lambda p: float(p.get("home_win_prob", p.get("win_prob", 0.5))))
    margin = wavg(lambda p: p.get("pred_home_score", 0) - p.get("pred_away_score", 0))
    total = wavg(lambda p: p.get("pred_total", (p.get("pred_home_score", 0) + p.get("pred_away_score", 0))))
    # agreement = share picking the consensus side
    home_side = home_prob >= 0.5
    agree = mean([1.0 if ((p.get("pred_home_score",0) >= p.get("pred_away_score",0)) == home_side) else 0.0 for _, p in rows])
    return {"home_win_prob": round(home_prob,3), "pred_margin": round(margin,1),
            "pred_total": round(total,1), "agreement": round(agree,2), "n_models": len(rows)}

def ev(prob, decimal_odds):
    """Expected value per 1 unit staked. >0 = +EV. decimal_odds includes stake (台彩 style)."""
    return round(prob * decimal_odds - 1, 3)

def recommend(consensus, odds, edge_threshold=0.05, min_agreement=0.66):
    """odds: {ml_home, ml_away, spread_line(home -x), spread_home_odds, spread_away_odds, total_line, over_odds, under_odds}
       Returns list of {market, pick, edge, confidence, why} for +EV spots with enough agreement."""
    if not consensus: return []
    recs = []; c = consensus; hp = c["home_win_prob"]; agree = c["agreement"]
    # Moneyline
    for side, prob, key in (("home", hp, "ml_home"), ("away", 1-hp, "ml_away")):
        d = odds.get(key)
        if d:
            e = ev(prob, d)
            if e >= edge_threshold and agree >= min_agreement:
                recs.append({"market":"moneyline","pick":side,"edge":e,
                             "confidence": "high" if e>0.12 and agree>=0.8 else "med",
                             "why": f"consensus {side} prob {prob:.0%} vs odds {d} → +EV {e:+.0%}"})
    # Spread (ATS): home covers if pred_margin > -spread_line (home -x means line=-x)
    sl = odds.get("spread_line")
    if sl is not None:
        cover_home = c["pred_margin"] + sl > 0   # margin beats the spread
        # only flag if margin clears the line by a buffer (>1.5) and agreement high
        if abs(c["pred_margin"] + sl) >= 1.5 and agree >= min_agreement:
            side = "home" if cover_home else "away"
            d = odds.get("spread_home_odds" if cover_home else "spread_away_odds")
            recs.append({"market":"spread","pick":f"{side} {sl:+g}","edge":None,"odds":d,
                         "confidence":"med","why":f"consensus margin {c['pred_margin']:+.1f} vs line {sl:+g}"})
    # Total O/U
    tl = odds.get("total_line")
    if tl is not None and abs(c["pred_total"] - tl) >= 4 and agree >= min_agreement:
        over = c["pred_total"] > tl
        recs.append({"market":"total","pick":"over" if over else "under","edge":None,
                     "odds": odds.get("over_odds" if over else "under_odds"),
                     "confidence":"med","why":f"consensus total {c['pred_total']:.0f} vs line {tl}"})
    return recs

if __name__ == "__main__":
    # mock: 6 models lean home (SAS) modestly, predict ~5-pt margin, ~205 total
    preds = {
        "GPT":{"home_win_prob":0.60,"pred_home_score":108,"pred_away_score":102,"pred_total":210},
        "Claude":{"home_win_prob":0.58,"pred_home_score":106,"pred_away_score":101,"pred_total":207},
        "Gemini":{"home_win_prob":0.55,"pred_home_score":104,"pred_away_score":103,"pred_total":207},
        "DeepSeek":{"home_win_prob":0.62,"pred_home_score":105,"pred_away_score":99,"pred_total":204},
        "Kimi":{"home_win_prob":0.57,"pred_home_score":103,"pred_away_score":100,"pred_total":203},
        "Grok":{"home_win_prob":0.59,"pred_home_score":107,"pred_away_score":101,"pred_total":208},
    }
    odds = {"ml_home":1.74,"ml_away":2.10,"spread_line":-2.5,"spread_home_odds":1.91,"spread_away_odds":1.91,
            "total_line":211.5,"over_odds":1.91,"under_odds":1.91}
    c = weighted_consensus(preds)
    print("consensus:", c)
    print("market implied home prob:", round(1/odds["ml_home"],3), "| our home prob:", c["home_win_prob"])
    for r in recommend(c, odds): print("  REC:", r)
