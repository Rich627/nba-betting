#!/bin/bash
# cron-nba.sh — NBA betting scheduler sweep wrapper (reference copy; deployed as
# ~/whatsapp-cron-nba.sh on the mini). Runs scripts/run_scheduler.py; the python writes
# its own pending files (~/.whatsapp-channel/pending/nba-sweep-*.md) for the Kai agent.
# This wrapper only handles env, mode, timeout and logging. No claude processes.
#
# Modes:
#   sweep   (default) ESPN + odds (3 credits, only if a game is in the window) + 6 LLMs
#                     + edge + settle. Window 14h.
#   settle            results + settlement only. 0 odds credits, 0 model spend.
#   digest            tomorrow's slate (Taiwan times). 0 credits.
#
# Crontab (mini runs Asia/Taipei, UTC+8). NBA tips are 07:00-11:30 TW on a normal day,
# 01:00-04:00 TW for US-afternoon games (weekends/holidays):
#   30 5  * * *  ~/whatsapp-cron-nba.sh sweep    # morning slate, before Rich sleeps
#   0  20 * * *  ~/whatsapp-cron-nba.sh sweep    # early (US afternoon) tips; usually 0 games -> 0 credits
#   15 15 * * *  ~/whatsapp-cron-nba.sh settle   # all games final by ~14:00 TW
#   15 19 * * *  ~/whatsapp-cron-nba.sh settle   # backstop
#   0  21 * * *  ~/whatsapp-cron-nba.sh digest   # tomorrow's schedule
# Budget: <= 6 odds credits/day (~180/month of the 500 free), 6 OpenRouter calls/game.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

MODE="${1:-sweep}"
LOG="$HOME/whatsapp-cron-nba.log"
REPO="$HOME/Desktop/nba-betting"
PY="/usr/bin/python3"
TIMEOUT="$(command -v gtimeout || command -v timeout || true)"

cd "$REPO" || { echo "[$(date)] missing $REPO" >>"$LOG"; exit 1; }

case "$MODE" in
  sweep)  ARGS="--hours-ahead 14" ;;
  settle) ARGS="--skip-models --hours-ahead 0"; export NBA_SKIP_ODDS=1 ;;
  digest) ARGS="--digest"; export NBA_SKIP_ODDS=1 ;;
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
echo "[$(date)] === nba $MODE done exit=$EXIT ===" >>"$LOG"
exit 0
