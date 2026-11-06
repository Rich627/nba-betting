#!/usr/bin/env python3
"""Fill predictions.reasoning_zh for rows made before the prompt asked for it.

New predictions get reasoning_zh straight from each model (models/predict.py). Older
rows only have the English reasoning, so this translates them in one OpenRouter call
per batch. It only writes rows whose reasoning_zh is still empty, so it is safe to
re-run.

  python3 scripts/backfill_reasoning_zh.py --dry-run   # list what would be translated
  python3 scripts/backfill_reasoning_zh.py
"""
import argparse
import json
import os
import sys
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass
from data import teams as T  # noqa: E402
from data.db import connect  # noqa: E402
from models.predict import OPENROUTER_URL  # noqa: E402

TRANSLATOR = "anthropic/claude-opus-5.5"
BATCH = 20
SYSTEM = ("Translate NBA game analysis from English into Traditional Chinese as used in Taiwan. "
          "Keep numbers, records and player names unchanged. Use these team names: "
          + ", ".join(f"{T.ABBR_TO_NICK.get(a, a)}={z}" for a, z in sorted(T.ZH.items()))
          + ". Return STRICT JSON mapping each input key to its translation, nothing else.")


def translate(items: dict, key: str) -> dict:
    body = json.dumps({"model": TRANSLATOR, "temperature": 0.2,
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": json.dumps(items, ensure_ascii=False)}]}).encode()
    req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost", "X-Title": "nba-predictor"})
    with urllib.request.urlopen(req, timeout=180) as r:
        content = json.load(r)["choices"][0]["message"]["content"] or ""
    # Some models wrap the JSON in a ``` fence even in json_object mode.
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"translator returned no JSON object: {content[:120]!r}")
    return json.loads(content[start:end + 1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    con = connect()
    rows = con.execute(
        "SELECT rowid AS rid, game_id, model, reasoning FROM predictions "
        "WHERE reasoning IS NOT NULL AND reasoning != '' AND (reasoning_zh IS NULL OR reasoning_zh = '') "
        "ORDER BY game_id, model").fetchall()
    print(f"{len(rows)} prediction(s) need reasoning_zh")
    if a.dry_run or not rows:
        for r in rows:
            print(f"  {r['game_id']} {r['model']}")
        return
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY not set")
    done = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        out = translate({str(r["rid"]): r["reasoning"] for r in chunk}, key)
        for r in chunk:
            zh = out.get(str(r["rid"]))
            if isinstance(zh, str) and zh.strip():
                con.execute("UPDATE predictions SET reasoning_zh=? WHERE rowid=?", (zh.strip(), r["rid"]))
                done += 1
        con.commit()
    print(f"filled {done}/{len(rows)}")


if __name__ == "__main__":
    main()
