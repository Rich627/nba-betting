#!/bin/bash
# cron-nba.sh — NBA betting scheduler sweep wrapper (reference copy; deployed as
# ~/whatsapp-cron-nba.sh on the mini). Runs scripts/run_scheduler.py; the python writes
# its own pending files (~/.whatsapp-channel/pending/nba-sweep-*.md) for the Kai agent.
# This wrapper only handles env, mode, timeout and logging. No claude processes.
#
# Modes:
#   sweep [hours] (default 3h) ESPN + odds (2 credits, only if a game tips inside the
#                 window) + 7 LLMs + edge + settle. Every game is picked up by the first
#                 sweep whose window reaches it, i.e. 1-3h before tip-off (Rich asked for
#                 ~2h, 2026-11-05).
#   settle        results + settlement only. 0 odds credits, 0 model spend.
#   digest        next-24h slate with current favourite + spread. 2 credits.
#
# Crontab. Rich and the mini are both in Arizona (America/Phoenix, MST, UTC-7, no DST —
# verified 2026-11-05 via /etc/localtime). NBA tips: 17:00-20:30 MST on a normal day,
# 10:00-13:00 MST for weekend/holiday afternoon games:
#   0 8       * * *  ~/whatsapp-cron-nba.sh digest   # today's slate + lines
#   0 9-19/2  * * *  ~/whatsapp-cron-nba.sh sweep    # 09,11,13,15,17,19 — 3h window each
#   45 23     * * *  ~/whatsapp-cron-nba.sh settle   # west-coast games are final by ~22:30
#   0 7       * * *  ~/whatsapp-cron-nba.sh settle   # backstop (sweeps settle too)
# Budget: a normal day is 3 sweeps with games + digest = ~8 odds credits (~240/month of
# the 500 free; weekends ~12). 7 OpenRouter calls/game, ~$0.09/game.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

MODE="${1:-sweep}"
LOG="$HOME/whatsapp-cron-nba.log"
REPO="${NBA_REPO:-$HOME/nba-betting}"
PY="/usr/bin/python3"
TIMEOUT="$(command -v gtimeout || command -v timeout || true)"

cd "$REPO" || { echo "[$(date)] missing $REPO" >>"$LOG"; exit 1; }

case "$MODE" in
  sweep)  ARGS="--hours-ahead ${2:-3}" ;;
  settle) ARGS="--skip-models --hours-ahead 0"; export NBA_SKIP_ODDS=1 ;;
  digest) ARGS="--digest" ;;   # fetches h2h+spreads (2 credits) so the evening list shows lines
  *) echo "[$(date)] unknown mode $MODE" >>"$LOG"; exit 2 ;;
esac

echo "[$(date)] === nba $MODE start ===" >>"$LOG"
if [ -n "$TIMEOUT" ]; then
  "$TIMEOUT" --kill-after=30s 40m "$PY" scripts/run_scheduler.py $ARGS >>"$LOG" 2>&1
else
  "$PY" scripts/run_scheduler.py $ARGS >>"$LOG" 2>&1
fi
EXIT=$?
[ "$EXIT" -eq 124 ] && echo "[$(date)] TIMED OUT after 40m" >>"$LOG"
# Refresh the public site (no-op push when nothing changed). Never blocks the sweep.
${TIMEOUT:+"$TIMEOUT" --kill-after=30s 5m} bash scripts/deploy_site.sh >>"$LOG" 2>&1 || echo "[$(date)] site deploy failed (non-fatal)" >>"$LOG"
echo "[$(date)] === nba $MODE done exit=$EXIT ===" >>"$LOG"
exit 0
