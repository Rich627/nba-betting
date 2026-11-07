#!/usr/bin/env python3
"""Live-season orchestrator — run repeatedly by cron (see scripts/cron-nba.sh); does NOT
schedule itself and does NOT talk to WhatsApp. It writes the exact 繁體中文 message text
to ~/.whatsapp-channel/pending/nba-sweep-*.md, which the Kai agent forwards to the NBA
group. Idempotent: games.notified_at / settled_notified_at / digest_at gate repeats, so
re-running within the same hour never double-posts.

Each run:
  [1] ESPN scoreboard today-2..today+1 (ET): schedule + results + rest/b2b   (free)
  [2] ESPN standings + injury report                                          (free)
  [3] the-odds-api h2h/spreads/totals — ONLY if a game is inside the notify
      window and NBA_SKIP_ODDS != 1                                           (3 credits)
  [4] settlement pass: grade predictions + recs for newly-final games, rebuild
      model wallets, queue one 賽果 message + leaderboard
  [5] notification pass: every scheduled game tipping within --hours-ahead and
      not yet notified -> analysis modules (if player data) -> 6 LLMs -> consensus
      -> edge -> queue ONE consolidated 賽前 message
  [6] --digest: queue the next 24h slate (Arizona times) with current lines, no models
  [7] --recheck: 3-25 min before tip, re-read the injury picture (official NBA report +
      ESPN) and diff it against the snapshot stored at prediction time. A material change
      (>= 8 expected rotation minutes) voids the game's recommendations, re-runs the models
      at fresh odds (2 credits, once per run) and queues one ⚠️ 傷兵異動 message.
      Exit status 10 means something was queued (the cron wrapper redeploys the site).

Usage:
  python3 scripts/run_scheduler.py                    # normal sweep
  python3 scripts/run_scheduler.py --hours-ahead 14
  python3 scripts/run_scheduler.py --skip-models      # data + odds only (no OpenRouter spend)
  python3 scripts/run_scheduler.py --dry-run          # print, don't mark or queue
  python3 scripts/run_scheduler.py --digest           # tomorrow's schedule only
  python3 scripts/run_scheduler.py --recheck          # pre-tip injury re-check
  NBA_SKIP_ODDS=1 ...                                 # settlement-only (0 credits)
"""
import argparse
import datetime
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001
    pass

from data import fetch_espn, fetch_news, fetch_odds, fetch_official_injuries, injuries as INJ, teams as T  # noqa: E402
from data.db import connect  # noqa: E402
from engine import edge as E, settle as S  # noqa: E402
from models import predict as M  # noqa: E402

PENDING_DIR = os.path.expanduser("~/.whatsapp-channel/pending")
NBA_CHAT_ID = "120363428418779899@g.us"
MARKET_ZH = {"moneyline": "獨贏", "spread": "讓分", "total": "大小"}
STATUS_ZH = {"Out": "缺陣", "Doubtful": "可能缺陣", "Questionable": "出賽成疑", "Probable": "可能出賽",
             "Available": "可出賽", "Day-To-Day": "每日觀察", "not listed": "未列名單"}
RECHECK_WINDOW_MIN = (3, 25)    # (earliest, latest) min before tip; 3 lets the next run catch a skipped one


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


# ----------------------------------------------------------------------------- pending
_PENDING_PATHS = []


def queue_message(section: str, msg: str, dry_run: bool):
    """Publish one complete, immutable pending file per section. Readers may consume
    it immediately; later sections never append to a file the reader could delete."""
    print("\n" + "=" * 60 + f"\n[{section}]\n" + msg)
    if dry_run:
        return
    os.makedirs(PENDING_DIR, exist_ok=True)
    path = os.path.join(PENDING_DIR, f"nba-sweep-{utcnow():%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}.md")
    tmp = path + ".tmp"
    try:
        with open(tmp, "x", encoding="utf-8") as f:
            f.write(f"---\nchat_id: {NBA_CHAT_ID}\ntask: nba-sweep\n---\n\n")
            f.write("NBA 系統（~/nba-betting）自動產生的訊息。每個 ## 區塊各發成一則訊息到 NBA 群組，"
                    "原文照發（可微調語氣，數字、賠率、建議一律不得更動或自行補充）。\n")
            f.write(f"\n## {section}\n\n{msg}\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _PENDING_PATHS.append(path)
    return path


# ----------------------------------------------------------------------------- helpers
def pick_label(home, away, pick, market):
    side = pick.split(" ")[0]
    team = T.zh(home) if side == "home" else T.zh(away)
    if market == "moneyline":
        return f"獨贏 {team}"
    if market == "spread":
        return f"讓分 {team} {pick.split(' ')[1]}"
    return f"總分 {'大' if pick == 'over' else '小'}"


def rec_line(home, away, r):
    s = f"👉 {pick_label(home, away, r['pick'], r['market'])}"
    if r.get("line") is not None and r["market"] == "total":
        s += f" {r['line']:g}"
    if r.get("odds"):
        s += f" @{r['odds']:.2f}"
    if r.get("edge") is not None:
        s += f"（EV {r['edge']:+.0%}）"
    return s


def wallet_line(con):
    lb = S.leaderboard(con)
    if not lb:
        return ""
    return "📊 模型錢包：" + " · ".join(f"{m} {pnl:+.0f} ({w}/{b})" for m, b, w, pnl in lb)


# ----------------------------------------------------------------------------- passes
def settlement_pass(con, dry_run):
    rec = S.settle(con)
    if rec:
        print(" [settle] wallets:", {m: f"{w}/{b} {pnl:+.0f}" for m, (b, w, pnl) in rec.items()})
    newly_final = con.execute(
        "SELECT game_id, home_team, away_team, home_score, away_score, game_date FROM games "
        "WHERE status='final' AND settled_notified_at IS NULL AND home_score IS NOT NULL "
        "ORDER BY commence_time"
    ).fetchall()
    # Only games we said something about get a line; untouched games are listed compactly.
    detailed, others = [], []
    for g in newly_final:
        gid, home, away = g["game_id"], g["home_team"], g["away_team"]
        score = f"*{T.zh(away)} {g['away_score']} - {g['home_score']} {T.zh(home)}*"
        preds = con.execute(
            "SELECT model, pick, result FROM predictions WHERE game_id=? AND market='moneyline'", (gid,)
        ).fetchall()
        grades = S.grade_recommendations_for_game(con, gid)
        if not preds and not grades:
            others.append(score)
        else:
            hit = sum(1 for p in preds if p["result"] == "WON")
            graded = sum(1 for p in preds if p["result"] in ("WON", "LOST"))
            line = f"{score} · 模型 {hit}/{graded} 命中"
            for gr in grades:
                mark = {"WON": "✅", "LOST": "❌", "PUSH": "➖"}.get(gr["result"], "❔")
                line += f"\n   {mark} {pick_label(home, away, gr['pick'], gr['market'])}"
                if gr.get("odds"):
                    line += f" @{gr['odds']:.2f}"
                if gr.get("pnl") is not None:
                    line += f" → {gr['pnl']:+.0f}"
            detailed.append(line)
    if not newly_final:
        print(" [settle] no newly-final games")
        return
    parts = ["🏀 *NBA 賽果*"]
    parts += detailed
    if others:
        parts.append("其他：" + "、".join(others))
    wl = wallet_line(con)
    if wl:
        parts.append(wl)
    queue_message("賽果", "\n".join(parts), dry_run)
    if not dry_run:
        con.executemany("UPDATE games SET settled_notified_at=? WHERE game_id=?",
                        [(utcnow().isoformat(), g["game_id"]) for g in newly_final])
        con.commit()


def _run_modules(con, gid, snap=None):
    """All modules when nba_api player rows exist; otherwise only injuries_rest_b2b, which
    runs on the official/ESPN injury view + ESPN season minutes (no nba_api needed)."""
    has_players = con.execute("SELECT 1 FROM player_game_stats LIMIT 1").fetchone()
    try:
        from analysis.registry import run_all
        only = None if has_players else ["injuries_rest_b2b"]
        return len(run_all(gid, con, modules=only, injury_snapshot=snap) or {})
    except Exception as e:  # noqa: BLE001
        print(f"   modules failed for {gid}: {type(e).__name__}: {str(e)[:100]}")
        return 0


def predict_game(con, g, weights, skip_models, snap=None, save_kind="predict"):
    """Run models + edge for one game; store predictions/recs; return the message block.
    The injury snapshot the models see is stored as `save_kind` (the re-check baseline)."""
    gid, home, away = g["game_id"], g["home_team"], g["away_team"]
    head = f"*{T.matchup_zh(home, away)}* {T.local_label(g['commence_time'])}"
    odds = fetch_odds.game_odds_dict(con, gid)
    if skip_models:
        return head + "\n" + fetch_odds.odds_summary(odds, home, away) + "\n（未跑模型）"
    if snap is None:
        snap = INJ.snapshot(con, gid)
    if save_kind and snap:
        INJ.save_snapshot(con, gid, save_kind, snap)
    _run_modules(con, gid, snap)
    game = M.game_dict_from_db(gid, con, injury_snapshot=snap)
    preds = M.to_home_perspective(M.predict_all(game), home)
    now = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    n_ok = 0
    for model, p in preds.items():
        if not isinstance(p, dict) or "error" in p:
            print(f"   {model}: {p.get('error') if isinstance(p, dict) else p}")
            continue
        pick = home if p["home_win_prob"] >= 0.5 else away
        price = odds.get("ml_home") if pick == home else odds.get("ml_away")
        con.execute(
            """INSERT INTO predictions(game_id,model,market,pick,win_prob,pred_margin,pred_total,confidence,
                                        reasoning,reasoning_zh,odds,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(game_id,model,market) DO UPDATE SET pick=excluded.pick, win_prob=excluded.win_prob,
                 pred_margin=excluded.pred_margin, pred_total=excluded.pred_total, confidence=excluded.confidence,
                 reasoning=excluded.reasoning, reasoning_zh=excluded.reasoning_zh, odds=excluded.odds, created_at=excluded.created_at""",
            (gid, model, "moneyline", pick, p["home_win_prob"],
             (p.get("pred_home_score") or 0) - (p.get("pred_away_score") or 0),
             p.get("pred_total"), p.get("confidence"), p.get("reasoning"), p.get("reasoning_zh"), price, now),
        )
        n_ok += 1
    con.commit()
    c = E.weighted_consensus(preds, weights=weights or None)
    if not c:
        raise RuntimeError(f"no usable model predictions ({n_ok}/{len(preds)}); leaving game eligible for retry")
    fav = home if c["home_win_prob"] >= 0.5 else away
    fav_p = c["home_win_prob"] if fav == home else 1 - c["home_win_prob"]
    ph = round((c["pred_total"] + c["pred_margin"]) / 2)
    pa = round((c["pred_total"] - c["pred_margin"]) / 2)
    lines = [head,
             f"共識 {T.zh(fav)} 勝率 {fav_p:.0%}（{n_ok} 模型 · 一致 {c['agreement']:.0%}）· 預測 {T.zh(away)} {pa}-{ph} {T.zh(home)}",
             fetch_odds.odds_summary(odds, home, away)]
    recs = E.recommend(c, odds)
    for r in recs:
        con.execute(
            """INSERT INTO recommendations(game_id,market,pick,edge,confidence,why,odds,line,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(game_id,market,pick) DO UPDATE SET edge=excluded.edge, confidence=excluded.confidence,
                 why=excluded.why, odds=excluded.odds, line=excluded.line, created_at=excluded.created_at""",
            (gid, r["market"], r["pick"], r.get("edge"), r["confidence"], r["why"], r.get("odds"), r.get("line"), now),
        )
        lines.append(rec_line(home, away, r))
    con.commit()
    if not recs:
        lines.append("✋ 無 +EV，這場不買")
    return "\n".join(lines)


def notification_pass(con, hours_ahead, skip_models, dry_run, limit=None):
    if skip_models:
        print(" [notify] models disabled; leaving games eligible for retry")
        return
    now = utcnow()
    end = now + datetime.timedelta(hours=hours_ahead)
    cands = [g for g in con.execute(
        "SELECT game_id, home_team, away_team, commence_time FROM games "
        "WHERE status='scheduled' AND notified_at IS NULL AND commence_time IS NOT NULL ORDER BY commence_time"
    ) if now < T.parse_utc(g["commence_time"]) <= end]
    if limit:
        cands = cands[:limit]
    if not cands:
        print(f" [notify] no un-notified game tips within {hours_ahead}h")
        return
    weights = S.weights_from_record(con)
    blocks, done = [], []
    for g in cands:
        try:
            blocks.append(predict_game(con, g, weights, skip_models))
            done.append(g["game_id"])
        except Exception as e:  # noqa: BLE001
            print(f" [notify] {g['game_id']} FAILED: {type(e).__name__}: {str(e)[:200]}")
    if not blocks:
        return
    # Summary first: the bets, then the deadline, then the per-game detail. Rich reads
    # this ~2h before tip-off (Arizona time) — the first three lines must be enough.
    picks = []
    for b in blocks:
        head = b.split("\n")[0]
        for line in b.split("\n"):
            if line.startswith("👉"):
                picks.append(f"{line} — {head.strip('*').split('*')[0].strip()}")
    first_tip = min(T.parse_utc(g["commence_time"]) for g in cands if g["game_id"] in done)
    deadline = f"⏰ 最早開賽 {T.local_label(T.iso_utc(first_tip))}（{T.DISPLAY_TZ_ZH}），要下的話在那之前。"
    if picks:
        summary = f"🏀 *NBA 今日建議*（{len(picks)} 注 / {len(blocks)} 場）\n" + "\n".join(picks) + f"\n{deadline}\n紙上驗證中，小注或不下。"
    else:
        summary = f"🏀 *NBA 今日建議*：{len(blocks)} 場都沒有 +EV，今天不買。\n{deadline}"
    queue_message("賽前分析", summary + "\n\n— 各場明細 —\n\n" + "\n\n".join(blocks), dry_run)
    if not dry_run:
        con.executemany("UPDATE games SET notified_at=? WHERE game_id=?", [(now.isoformat(), gid) for gid in done])
        con.commit()


def _change_line(c):
    old = STATUS_ZH.get(c["old"], c["old"])
    new = STATUS_ZH.get(c["new"], c["new"])
    role = f"，場均 {c['min']:.0f} 分鐘" if c["min"] else ""
    return f"• {T.zh(c['team'])} {c['player']}：{old} → *{new}*{role}"


def recheck_pass(con, dry_run, window=RECHECK_WINDOW_MIN, refresh_odds=None):
    """Pre-tip injury re-check. Returns the number of games whose picks changed."""
    now = utcnow()
    lo, hi = (now + datetime.timedelta(minutes=m) for m in window)
    cands = [g for g in con.execute(
        "SELECT game_id, home_team, away_team, commence_time FROM games WHERE status='scheduled' "
        "AND notified_at IS NOT NULL AND rechecked_at IS NULL AND commence_time IS NOT NULL ORDER BY commence_time"
    ) if lo < T.parse_utc(g["commence_time"]) <= hi]
    if not cands:
        print(f" [recheck] no predicted game tips in {window[0]}-{window[1]} min")
        return 0
    have_models = bool(os.environ.get("OPENROUTER_API_KEY"))
    weights = S.weights_from_record(con)
    odds_fresh = False
    blocks, checked = [], []
    for g in cands:
        gid, home, away = g["game_id"], g["home_team"], g["away_team"]
        base = INJ.load_snapshot(con, gid, "predict")
        new = INJ.snapshot(con, gid)
        if not base or not new:
            print(f" [recheck] {gid}: no prediction-time snapshot; nothing to compare")
            checked.append(gid)
            continue
        changes = INJ.diff(base, new)
        open_q = INJ.unresolved(new)
        if not changes:
            print(f" [recheck] {gid}: no material change"
                  + (f" (still undecided: {', '.join(e['player'] for e in open_q)})" if open_q else ""))
            if not dry_run:
                INJ.save_snapshot(con, gid, "recheck", new, {"changes": []})
            checked.append(gid)
            continue
        print(f" [recheck] {gid}: {len(changes)} material change(s): "
              + "; ".join(f"{c['player']} {c['old']}->{c['new']}" for c in changes))
        old_recs = [dict(r) for r in con.execute(
            "SELECT market,pick,odds,line,edge FROM recommendations WHERE game_id=?", (gid,))]
        head = f"⚠️ *傷兵異動* {T.matchup_zh(home, away)} {T.local_label(g['commence_time'])}"
        lines = [head] + [_change_line(c) for c in changes[:6]]
        if old_recs:
            lines.append("原建議作廢：" + "、".join(
                pick_label(home, away, r["pick"], r["market"]) + (f" @{r['odds']:.2f}" if r.get("odds") else "")
                for r in old_recs))
        if dry_run:
            blocks.append("\n".join(lines + ["（dry-run：未重跑）"]))
            continue
        INJ.save_snapshot(con, gid, "recheck", new, {"changes": changes, "voided": old_recs})
        con.execute("DELETE FROM recommendations WHERE game_id=? AND result IS NULL", (gid,))
        con.commit()
        if not have_models:
            lines.append("模型未啟用，這場先別下。")
        else:
            if not odds_fresh and refresh_odds:
                refresh_odds()
                odds_fresh = True
            try:
                block = predict_game(con, g, weights, False, snap=new, save_kind=None)
                lines.append("— 依最新名單重跑 —")
                lines += block.split("\n")[1:]
            except Exception as e:  # noqa: BLE001
                print(f" [recheck] {gid} re-run FAILED: {type(e).__name__}: {str(e)[:200]}")
                lines.append("重跑失敗，這場先別下。")
        blocks.append("\n".join(lines))
        checked.append(gid)
    if blocks:
        queue_message("賽前異動", "\n\n".join(blocks) + "\n\n開賽前最後確認，紙上驗證中。", dry_run)
    if not dry_run and checked:
        con.executemany("UPDATE games SET rechecked_at=? WHERE game_id=?", [(now.isoformat(), gid) for gid in checked])
        con.commit()
    return len(blocks)


def digest_pass(con, dry_run, hours=24):
    now = utcnow()
    end = now + datetime.timedelta(hours=hours)
    games = [g for g in con.execute(
        "SELECT game_id, home_team, away_team, commence_time FROM games "
        "WHERE status='scheduled' AND digest_at IS NULL AND commence_time IS NOT NULL ORDER BY commence_time"
    ) if now < T.parse_utc(g["commence_time"]) <= end]
    if not games:
        print(" [digest] nothing new to list")
        return
    lines = [f"🗓 *NBA 今日賽程*（{T.DISPLAY_TZ_ZH}，{len(games)} 場）"]
    for g in games:
        o = fetch_odds.game_odds_dict(con, g["game_id"])
        home, away = g["home_team"], g["away_team"]
        mk = ""
        if o.get("ml_home"):
            fav, price = (home, o["ml_home"]) if o["ml_home"] < o["ml_away"] else (away, o["ml_away"])
            mk = f" · 看好 {T.zh(fav)} {price:.2f}"
            if o.get("spread_line") is not None:
                mk += f"（讓 {abs(o['spread_line']):g}）"
        lines.append(f"{T.local_label(g['commence_time'])} {T.matchup_zh(home, away)}{mk}")
    lines.append("模型分析＋下注建議會在每場開賽前 1-3 小時發。")
    queue_message("賽程", "\n".join(lines), dry_run)
    if not dry_run:
        con.executemany("UPDATE games SET digest_at=? WHERE game_id=?", [(now.isoformat(), g["game_id"]) for g in games])
        con.commit()


# ----------------------------------------------------------------------------- main
def refresh_injuries(con):
    """ESPN injuries, the official NBA report and ESPN season minutes. Each is free and
    independent: one failing source never blocks the others or the run."""
    for name, fn in (("espn injuries", lambda: f"{fetch_espn.fetch_injuries(con)} rows"),
                     ("official report", lambda: "{} rows ({})".format(*fetch_official_injuries.fetch(con))),
                     ("season minutes", lambda: f"{INJ.fetch_season_stats(con)} players")):
        try:
            print(f" [injuries] {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            print(f" [injuries] {name} FAILED: {type(e).__name__}: {str(e)[:120]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours-ahead", type=float, default=3.0)
    ap.add_argument("--skip-models", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="cap games sent to the model layer this run")
    ap.add_argument("--digest", action="store_true", help="queue tomorrow's schedule instead of predictions")
    ap.add_argument("--recheck", action="store_true", help="pre-tip injury re-check only (exit 10 if queued)")
    a = ap.parse_args()

    ok_key = None if a.skip_models else os.environ.get("OPENROUTER_API_KEY")
    odds_key = os.environ.get("ODDS_API_KEY")
    skip_odds = os.environ.get("NBA_SKIP_ODDS") == "1"
    now = utcnow()
    print(f"NBA scheduler — {now:%Y-%m-%dT%H:%M:%SZ} (window {a.hours_ahead}h, "
          f"models={'on' if ok_key else 'off'}, odds={'off' if skip_odds or not odds_key else 'on'})")
    con = connect()

    # [1] schedule + results
    for d, res in fetch_espn.fetch_window(con, days_back=2, days_fwd=1).items():
        print(f" [espn] {d}: {res}")
    fetch_espn.compute_rest(con)
    # [2] form + injuries + news
    refresh_injuries(con)
    if a.recheck:
        def refresh_odds():
            if odds_key and not skip_odds:
                print(f" [odds] {fetch_odds.fetch_upcoming(con, markets=os.environ.get('NBA_ODDS_MARKETS', 'h2h,spreads'))[2]}")
        n = recheck_pass(con, a.dry_run, refresh_odds=refresh_odds)
        for path in _PENDING_PATHS:
            print(f"\n[pending] {path}")
        return 10 if n and not a.dry_run else 0
    try:
        print(f" [espn] standings {fetch_espn.fetch_standings(con)} teams")
    except Exception as e:  # noqa: BLE001
        print(f" [espn] standings FAILED: {type(e).__name__}: {str(e)[:120]}")
    try:
        n_news, n_teams = fetch_news.fetch_for_upcoming(con)
        print(f" [news] {n_news} new team-headlines (league feed + {n_teams} team feeds)")
    except Exception as e:  # noqa: BLE001
        print(f" [news] FAILED: {type(e).__name__}: {str(e)[:120]}")

    # [3] odds — only when something is actually inside the window (credits are finite).
    # h2h+spreads = 2 credits per call (totals dropped 2026-11-05: no probability behind a
    # totals rec and the 2h-cadence sweeps need the budget; NBA_ODDS_MARKETS overrides).
    end = now + datetime.timedelta(hours=a.hours_ahead if not a.digest else 24)
    gate_col = "digest_at" if a.digest else "notified_at"
    pending_games = [g for g in con.execute(
        f"SELECT commence_time FROM games WHERE status='scheduled' AND {gate_col} IS NULL AND commence_time IS NOT NULL"
    ) if now < T.parse_utc(g["commence_time"]) <= end]
    if odds_key and not skip_odds and pending_games:
        n_ev, n_rows, note, _ = fetch_odds.fetch_upcoming(con, markets=os.environ.get("NBA_ODDS_MARKETS", "h2h,spreads"))
        print(f" [odds] {note}")
    else:
        print(f" [odds] skipped ({'no games in window' if not pending_games else 'NBA_SKIP_ODDS' if skip_odds else 'no key'})")

    # [4] settle
    settlement_pass(con, a.dry_run)
    # [5] / [6]
    if a.digest:
        digest_pass(con, a.dry_run)
    else:
        notification_pass(con, a.hours_ahead, skip_models=not ok_key, dry_run=a.dry_run, limit=a.limit)

    if _PENDING_PATHS:
        for path in _PENDING_PATHS:
            print(f"\n[pending] {path}")
    else:
        print("\n(nothing queued this run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
