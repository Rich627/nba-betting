#!/usr/bin/env python3
"""Edge engine — turn multi-model predictions + market odds into BET RECOMMENDATIONS.
Core idea (WC lesson): recommend only where weighted model consensus beats the line (+EV),
NOT just the favorite. Models predict; this engine decides the bet.
Pure logic, no network — unit-testable with mock data.

Every recommendation carries `odds` (the median price we would take) and, for
spread/total, `line` (the picked side's line) so engine.settle can grade it without
parsing prose. 2026-11-05: spread/total buffers widened (1.5 -> 3 pts, 4 -> 6 pts) —
a 1.5-pt disagreement with the market is inside model noise, not an edge.
"""
from statistics import mean

EDGE_THRESHOLD = 0.05     # moneyline: EV >= +5%
MIN_AGREEMENT = 0.66      # share of models on the consensus side
SPREAD_BUFFER = 3.0       # points the consensus margin must clear the spread by
TOTAL_BUFFER = 6.0        # points the consensus total must differ from the line by


def weighted_consensus(preds: dict, weights: dict = None):
    """preds: {model: {home_win_prob, pred_home_score, pred_away_score, pred_total}}
       Returns consensus dict (HOME perspective) or None if no usable prediction."""
    rows = [(m, p) for m, p in preds.items() if isinstance(p, dict) and "error" not in p]
    if not rows:
        return None
    w = {m: (weights.get(m, 1.0) if weights else 1.0) for m, _ in rows}
    tot = sum(max(w[m], 0.01) for m, _ in rows)

    def num(p, k, default=0.0):
        try:
            return float(p.get(k) if p.get(k) is not None else default)
        except (TypeError, ValueError):
            return default

    def wavg(f):
        return sum(max(w[m], 0.01) * f(p) for m, p in rows) / tot

    home_prob = wavg(lambda p: num(p, "home_win_prob", num(p, "win_prob", 0.5)))
    margin = wavg(lambda p: num(p, "pred_home_score") - num(p, "pred_away_score"))
    total = wavg(lambda p: num(p, "pred_total", num(p, "pred_home_score") + num(p, "pred_away_score")))
    home_side = home_prob >= 0.5
    agree = mean([1.0 if ((num(p, "pred_home_score") >= num(p, "pred_away_score")) == home_side) else 0.0
                  for _, p in rows])
    return {"home_win_prob": round(home_prob, 3), "pred_margin": round(margin, 1),
            "pred_total": round(total, 1), "agreement": round(agree, 2), "n_models": len(rows)}


def ev(prob, decimal_odds):
    """Expected value per 1 unit staked. >0 = +EV. decimal_odds includes stake."""
    return round(prob * decimal_odds - 1, 3)


def recommend(consensus, odds, edge_threshold=EDGE_THRESHOLD, min_agreement=MIN_AGREEMENT):
    """odds: data.fetch_odds.game_odds_dict() output.
       Returns list of {market, pick, odds, line, edge, confidence, why}."""
    if not consensus or not odds:
        return []
    recs = []
    c = consensus
    hp, agree = c["home_win_prob"], c["agreement"]
    if agree < min_agreement:
        return []
    # Moneyline — the only market with a real probability behind it.
    for side, prob, key in (("home", hp, "ml_home"), ("away", 1 - hp, "ml_away")):
        d = odds.get(key)
        if d and d > 1:
            e = ev(prob, d)
            if e >= edge_threshold:
                recs.append({"market": "moneyline", "pick": side, "odds": d, "line": None, "edge": e,
                             "confidence": "high" if e > 0.12 and agree >= 0.8 else "med",
                             "why": f"共識 {side} 勝率 {prob:.0%} vs 賠率 {d:.2f} → EV {e:+.0%}"})
    # Spread (ATS): home covers if pred_margin + home_line > 0.
    sl = odds.get("spread_line")
    if sl is not None:
        diff = c["pred_margin"] + sl
        if abs(diff) >= SPREAD_BUFFER:
            cover_home = diff > 0
            side = "home" if cover_home else "away"
            line = sl if cover_home else -sl
            d = odds.get("spread_home_odds" if cover_home else "spread_away_odds")
            if d and d > 1:
                recs.append({"market": "spread", "pick": f"{side} {line:+g}", "odds": d, "line": line, "edge": None,
                             "confidence": "med",
                             "why": f"共識分差 {c['pred_margin']:+.1f} vs 盤口 {sl:+g}（差 {abs(diff):.1f} 分）"})
    # Total O/U
    tl = odds.get("total_line")
    if tl is not None and abs(c["pred_total"] - tl) >= TOTAL_BUFFER:
        over = c["pred_total"] > tl
        d = odds.get("over_odds" if over else "under_odds")
        if d and d > 1:
            recs.append({"market": "total", "pick": "over" if over else "under",
                         "odds": d, "line": tl, "edge": None,
                         "confidence": "med",
                         "why": f"共識總分 {c['pred_total']:.0f} vs 盤口 {tl:g}"})
    return recs


if __name__ == "__main__":
    preds = {
        "GPT": {"home_win_prob": 0.60, "pred_home_score": 108, "pred_away_score": 102, "pred_total": 210},
        "Claude": {"home_win_prob": 0.58, "pred_home_score": 106, "pred_away_score": 101, "pred_total": 207},
        "Gemini": {"home_win_prob": 0.55, "pred_home_score": 104, "pred_away_score": 103, "pred_total": 207},
        "DeepSeek": {"home_win_prob": 0.62, "pred_home_score": 105, "pred_away_score": 99, "pred_total": 204},
        "Kimi": {"home_win_prob": 0.57, "pred_home_score": 103, "pred_away_score": 100, "pred_total": 203},
        "Grok": {"home_win_prob": 0.59, "pred_home_score": 107, "pred_away_score": 101, "pred_total": 208},
    }
    odds = {"ml_home": 1.74, "ml_away": 2.10, "spread_line": -2.5, "spread_home_odds": 1.91, "spread_away_odds": 1.91,
            "total_line": 215.5, "over_odds": 1.91, "under_odds": 1.91, "n_books": 8}
    c = weighted_consensus(preds)
    print("consensus:", c)
    print("market implied home prob:", round(1 / odds["ml_home"], 3), "| our home prob:", c["home_win_prob"])
    for r in recommend(c, odds):
        print("  REC:", r)
