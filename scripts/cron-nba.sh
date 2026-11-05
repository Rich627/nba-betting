#!/bin/bash
# cron-nba.sh — NBA betting scheduler sweep wrapper (reference copy; deployed as
# ~/whatsapp-cron-nba.sh on the mini). Runs scripts/run_scheduler.py; the python writes
# its own pending files (~/.whatsapp-channel/pending/nba-sweep-*.md) for the Kai agent.
# This wrapper only handles env, mode, timeout and logging. No claude processes.
#
# Modes:
#   sweep [hours] (default, 14h) ESPN + odds (3 credits, only if a game is in the window)
#                 + 6 LLMs + edge + settle.
#   settle        results + settlement only. 0 odds credits, 0 model spend.
#   digest        tomorrow's slate (Taiwan times) with current lines. 2 credits.
#
# Crontab. The mini's system clock is America/Phoenix (MST, UTC-7, no DST — verified
# 2026-11-05 via /etc/localtime; it is NOT Asia/Taipei, whatever older notes say), so the
# slots below are MST = Taiwan time minus 15h. NBA tips are 07:00-11:30 TW (16:00-20:30
# MST) on a normal day, 01:00-04:00 TW (10:00-13:00 MST) for US-afternoon games:
#   30 14 * * *  ~/whatsapp-cron-nba.sh sweep      # 05:30 TW — morning slate, before Rich sleeps
#   0  5  * * *  ~/whatsapp-cron-nba.sh sweep 8    # 20:00 TW — early (US afternoon) tips only
#   15 0  * * *  ~/whatsapp-cron-nba.sh settle     # 15:15 TW — all games final by ~14:00 TW
#   15 4  * * *  ~/whatsapp-cron-nba.sh settle     # 19:15 TW — backstop
#   0  6  * * *  ~/whatsapp-cron-nba.sh digest     # 21:00 TW — tomorrow's schedule
# Budget: <= 8 odds credits/day (~240/month of the 500 free), 7 OpenRouter calls/game.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

MODE="${1:-sweep}"
LOG="$HOME/whatsapp-cron-nba.log"
REPO="$HOME/Desktop/nba-betting"
PY="/usr/bin/python3"
TIMEOUT="$(command -v gtimeout || command -v timeout || true)"

cd "$REPO" || { echo "[$(date)] missing $REPO" >>"$LOG"; exit 1; }

case "$MODE" in
  sweep)  ARGS="--hours-ahead ${2:-14}" ;;
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
bash scripts/deploy_site.sh >>"$LOG" 2>&1 || echo "[$(date)] site deploy failed (non-fatal)" >>"$LOG"
echo "[$(date)] === nba $MODE done exit=$EXIT ===" >>"$LOG"
exit 0
