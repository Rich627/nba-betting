#!/usr/bin/env python3
"""Model layer — fan out one SHARED prompt to all Tier-1 LLMs via OpenRouter.
Same prompt for every model (fair track-record comparison). Needs OPENROUTER_API_KEY.
Each model returns an OBJECTIVE prediction (not a bet) — the edge engine turns consensus+odds into the bet rec.
"""
import os, json, urllib.request, urllib.error

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# name -> OpenRouter slug (one key for all). Update slugs to current versions when wiring.
MODELS = {
    "GPT":      "openai/gpt-5.5",
    "Claude":   "anthropic/claude-opus-4.8",
    "Gemini":   "google/gemini-3.5-flash",
    "DeepSeek": "deepseek/deepseek-v4-pro",
    "Kimi":     "moonshotai/kimi-k2.7",
    "Grok":     "x-ai/grok-4",
}

SYSTEM = ("You are an elite NBA analyst. Predict the game OBJECTIVELY from the data. "
          "Do NOT just pick the favorite. Output STRICT JSON only, no prose outside it.")

OUTPUT_SCHEMA = ('{"winner":"<HOME_ABBR|AWAY_ABBR>","win_prob":0.0-1.0,'
                 '"pred_home_score":int,"pred_away_score":int,"pred_total":int,'
                 '"confidence":"high|med|low","key_factors":["...","..."],"reasoning":"1-3 sentences"}')

def build_prompt(game: dict) -> str:
    """game: {home, away, home_stats, away_stats, injuries, recent_form, odds, modules}"""
    modules_md = game.get("modules_md", "")
    module_block = f"\nQuantitative analysis modules (signed: + favors home, - favors away):\n{modules_md}\n" if modules_md else ""
    return (
        f"NBA game: {game['away']} (away) @ {game['home']} (home).\n"
        f"Home recent/stats: {game.get('home_stats','n/a')}\n"
        f"Away recent/stats: {game.get('away_stats','n/a')}\n"
        f"Injuries/load mgmt: {game.get('injuries','none reported')}\n"
        f"Recent form / rest / b2b: {game.get('recent_form','n/a')}\n"
        f"Current market odds (for context, predict independently): {game.get('odds','n/a')}\n"
        f"{module_block}\n"
        f"Base your judgement on the data above; do NOT invent numbers not given. "
        f"Predict the result. Respond with ONLY this JSON:\n{OUTPUT_SCHEMA}"
    )


def game_dict_from_db(game_id, con=None):
    """Assemble the model-input game dict from the DB, INCLUDING analysis-module outputs.
    This is the wiring point: analysis/* -> module_outputs -> LLM prompt."""
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from data.db import connect
    con = con or connect()
    g = con.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
    if not g:
        return None
    mods = con.execute(
        "SELECT module,score,confidence,reasoning_md FROM module_outputs WHERE game_id=?", (game_id,)
    ).fetchall()
    modules_md = "\n".join(
        f"  - {m['module']}: score={m['score']:+.2f} conf={m['confidence']:.2f} — {m['reasoning_md']}"
        for m in mods
    )
    rest = (f"home rest {g['rest_days_home']}d (b2b={g['is_b2b_home']}), "
            f"away rest {g['rest_days_away']}d (b2b={g['is_b2b_away']})")
    return {
        "home": g["home_team"], "away": g["away_team"],
        "recent_form": rest, "modules_md": modules_md,
    }

def predict_one(slug: str, prompt: str, key: str, timeout=60) -> dict:
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

def predict_all(game: dict, key: str = None) -> dict:
    key = key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set — add it to .env to run live predictions")
    prompt = build_prompt(game); out = {}
    for name, slug in MODELS.items():
        try:
            out[name] = predict_one(slug, prompt, key)
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    return out

if __name__ == "__main__":
    # offline demo of the prompt (no key needed) so you can eyeball it
    demo = {"home":"SAS","away":"NYK","home_stats":"107.5 ORtg, pace 99","away_stats":"112 ORtg",
            "injuries":"NYK: none; SAS: Wembanyama questionable","recent_form":"NYK 4-1 series lead, no b2b",
            "odds":"SAS -2.5 (1.91) / total 211.5"}
    print("=== SHARED PROMPT (sent identically to all 6 models) ===\n")
    print(build_prompt(demo))
    print("\n=== MODELS via OpenRouter ===")
    for n,s in MODELS.items(): print(f"  {n:9} {s}")
