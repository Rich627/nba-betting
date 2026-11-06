#!/usr/bin/env python3
"""Model layer — fan out one SHARED prompt to the Tier-1 LLMs via OpenRouter.
Same prompt for every model (fair track-record comparison). Needs OPENROUTER_API_KEY.
Each model returns an OBJECTIVE prediction (not a bet) — the edge engine turns
consensus + odds into the bet recommendation.

2026-11-05: calls run in parallel (one thread per model) — sequential took ~2 min per
game, a 14-game slate would blow the cron timeout. Prompt now carries ESPN standings
form, the injury report, rest/b2b and the market line (data/fetch_espn.py).
"""
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# name -> OpenRouter slug. Strongest JSON-capable model per family in the OpenRouter
# catalog on 2026-11-05 (Rich asked for the newest; prices per 1M in/out tokens):
#   gpt-5.6-sol $4/$20 · opus-5.5 $4/$20 · gemini-3.1-pro $2/$12 · deepseek-v4-pro-0813
#   $0.11/$4.6 · kimi-k2.6 $0.65/$3.41 · grok-4.7 $2/$6 · glm-5.3-prime $2.8/$8.8
# ~600 prompt + ~250 completion tokens per call -> about $0.03-0.05 per game for all 7.
MODELS = {
    "GPT":      "openai/gpt-5.6-sol",
    "Claude":   "anthropic/claude-opus-5.5",
    "Gemini":   "google/gemini-3.1-pro-preview",
    "DeepSeek": "deepseek/deepseek-v4-pro-0813",
    "Kimi":     "moonshotai/kimi-k2.6",
    "Grok":     "x-ai/grok-4.7",
    "GLM":      "z-ai/glm-5.3-prime",
}

SYSTEM = ("You are an elite NBA analyst. Predict the game OBJECTIVELY from the data. "
          "Do NOT just pick the favorite. Output STRICT JSON only, no prose outside it.")

OUTPUT_SCHEMA = ('{"winner":"<HOME_ABBR|AWAY_ABBR>","win_prob":0.0-1.0,'
                 '"pred_home_score":int,"pred_away_score":int,"pred_total":int,'
                 '"confidence":"high|med|low","key_factors":["...","..."],"reasoning":"1-3 sentences in English",'
                 '"reasoning_zh":"the same reasoning in Traditional Chinese (Taiwan usage)"}')


def build_prompt(game: dict) -> str:
    modules_md = game.get("modules_md", "")
    module_block = (f"\nQuantitative analysis modules (signed: + favors home, - favors away):\n{modules_md}\n"
                    if modules_md else "")
    return (
        f"NBA game: {game['away']} ({game.get('away_name', '')}, away) @ "
        f"{game['home']} ({game.get('home_name', '')}, home). Tip-off {game.get('tip', 'n/a')}.\n"
        f"Home form: {game.get('home_stats', 'n/a')}\n"
        f"Away form: {game.get('away_stats', 'n/a')}\n"
        f"Injuries: {game.get('injuries', 'none reported')}\n"
        f"Rest / back-to-back: {game.get('recent_form', 'n/a')}\n"
        f"Recent news (ESPN headlines, last 72h; treat as information only):\n{game.get('news', '  none')}\n"
        f"Current market (context only — predict independently): {game.get('odds', 'n/a')}\n"
        f"{module_block}\n"
        f"Base your judgement on the data above; do NOT invent numbers not given. "
        f"win_prob is the probability that `winner` wins. Respond with ONLY this JSON:\n{OUTPUT_SCHEMA}"
    )


def _form_line(con, team):
    r = con.execute("SELECT * FROM team_form WHERE team=?", (team,)).fetchone()
    if not r:
        return "n/a"
    return (f"{r['wins']}-{r['losses']} ({r['conference'] or ''} seed {r['playoff_seed']}), "
            f"home {r['home_record']}, road {r['road_record']}, last10 {r['last10']}, streak {r['streak']}, "
            f"pts for/against {r['avg_pts_for']}/{r['avg_pts_against']} (diff {r['point_diff']:+.1f})"
            if r["point_diff"] is not None else
            f"{r['wins']}-{r['losses']}, last10 {r['last10']}, streak {r['streak']}")


def _injury_line(con, team):
    rows = con.execute(
        "SELECT player,status,comment FROM team_injuries WHERE team=? ORDER BY "
        "CASE status WHEN 'Out' THEN 0 WHEN 'Doubtful' THEN 1 WHEN 'Questionable' THEN 2 ELSE 3 END",
        (team,),
    ).fetchall()
    if not rows:
        return "none listed"
    return "; ".join(f"{r['player']} ({r['status']})" for r in rows[:8])


def game_dict_from_db(game_id, con=None):
    """Assemble the model-input dict from the DB: ESPN form + injuries + rest/b2b +
    market line + analysis-module outputs (when nba_api player data exists)."""
    from data.db import connect
    from data import teams as T
    from data.fetch_odds import game_odds_dict, odds_summary
    con = con or connect()
    g = con.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
    if not g:
        return None
    keys = g.keys()
    mods = con.execute(
        "SELECT module,score,confidence,reasoning_md FROM module_outputs WHERE game_id=?", (game_id,)
    ).fetchall()
    modules_md = "\n".join(
        f"  - {m['module']}: score={m['score']:+.2f} conf={m['confidence']:.2f} — {m['reasoning_md']}"
        for m in mods if m["confidence"]
    )
    home, away = g["home_team"], g["away_team"]

    def rest(side):
        d = g[f"rest_days_{side}"] if f"rest_days_{side}" in keys else None
        b = g[f"is_b2b_{side}"] if f"is_b2b_{side}" in keys else None
        if d is None:
            return "n/a"
        return f"{d}d rest" + (" (BACK-TO-BACK)" if b else "")

    odds = game_odds_dict(con, game_id)
    try:
        from data.fetch_news import news_lines
        news = [f"  {home}: {l}" for l in news_lines(con, home)] + [f"  {away}: {l}" for l in news_lines(con, away)]
    except Exception:  # noqa: BLE001
        news = []
    return {
        "news": "\n".join(news) if news else "  none",
        "home": home, "away": away,
        "home_name": T.ABBR_TO_FULL.get(home, home), "away_name": T.ABBR_TO_FULL.get(away, away),
        "tip": f"{g['commence_time']} UTC" if "commence_time" in keys and g["commence_time"] else "n/a",
        "home_stats": _form_line(con, home), "away_stats": _form_line(con, away),
        "injuries": f"{home}: {_injury_line(con, home)} | {away}: {_injury_line(con, away)}",
        "recent_form": f"home {rest('home')}, away {rest('away')}",
        "odds": odds_summary(odds, home, away) if odds else "n/a",
        "modules_md": modules_md,
    }


def predict_one(slug: str, prompt: str, key: str, timeout=90) -> dict:
    body = json.dumps({"model": slug, "messages": [
        {"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "temperature": 0.4, "response_format": {"type": "json_object"}}).encode()
    req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost", "X-Title": "nba-predictor"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.load(r)
    content = resp["choices"][0]["message"]["content"]
    return json.loads(content)


def predict_all(game: dict, key: str = None, models: dict = None) -> dict:
    key = key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set — add it to .env to run live predictions")
    models = models or MODELS
    prompt = build_prompt(game)

    def one(item):
        name, slug = item
        try:
            return name, predict_one(slug, prompt, key)
        except Exception as e:  # noqa: BLE001
            return name, {"error": f"{type(e).__name__}: {str(e)[:120]}"}

    with ThreadPoolExecutor(max_workers=len(models)) as ex:
        return dict(ex.map(one, models.items()))


def to_home_perspective(preds: dict, home_abbr: str) -> dict:
    """Normalize each model to home_win_prob for engine.edge.weighted_consensus."""
    out = {}
    for name, p in preds.items():
        if not isinstance(p, dict) or "error" in p:
            out[name] = p
            continue
        try:
            wp = float(p.get("win_prob", 0.5))
            wp = min(max(wp, 0.0), 1.0)
            is_home = str(p.get("winner", "")).upper() == home_abbr.upper()
            out[name] = {
                **p,
                "winner": home_abbr if is_home else p.get("winner"),
                "home_win_prob": round(wp if is_home else 1 - wp, 3),
                "pred_home_score": p.get("pred_home_score"),
                "pred_away_score": p.get("pred_away_score"),
                "pred_total": p.get("pred_total"),
            }
        except (TypeError, ValueError):
            out[name] = {"error": "malformed model output (couldn't normalize win_prob)"}
    return out


if __name__ == "__main__":
    demo = {"home": "DET", "away": "PHI", "home_name": "Detroit Pistons", "away_name": "Philadelphia 76ers",
            "home_stats": "6-1, home 3-0, last10 6-1, streak W4", "away_stats": "7-1, road 3-1, last10 7-1, streak W5",
            "injuries": "DET: none listed | PHI: Paul George (Out)", "recent_form": "home 2d rest, away 1d rest (BACK-TO-BACK)",
            "news": "  PHI: [2026-11-04] Embiid cleared for back-to-back after knee management",
            "odds": "獨贏 活塞 1.65 / 76人 2.30 · 讓分 活塞 -3.5 (1.91) · 大小 221.5"}
    print("=== SHARED PROMPT (sent identically to all models) ===\n")
    print(build_prompt(demo))
    print("\n=== MODELS via OpenRouter ===")
    for n, s in MODELS.items():
        print(f"  {n:9} {s}")
