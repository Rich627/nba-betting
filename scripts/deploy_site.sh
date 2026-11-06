#!/bin/bash
# Rebuild docs/data/site.json from nba.db and push it so GitHub Pages (source: main /docs)
# redeploys. Same shape as ~/wc2026-agent/deploy_wc_site.sh: normal push only, never
# force, no-op when nothing changed. Called at the end of every cron sweep.
set -euo pipefail
REPO="${NBA_REPO:-$HOME/nba-betting}"
cd "$REPO"
/usr/bin/python3 pipeline/build_site_json.py >/dev/null
if git diff --quiet -- docs/data/site.json; then
  echo "[site] unchanged"
  exit 0
fi
GEN=$(/usr/bin/python3 -c "import json;print(json.load(open('docs/data/site.json'))['generated_at'])")
git add docs/data/site.json
git -c user.name="Kai (Rich's assistant)" -c user.email="rich.liu627@gmail.com" \
    commit -q -m "data: site snapshot ${GEN}"
git push -q origin main
echo "[site] pushed ${GEN}"
