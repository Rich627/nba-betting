import datetime
from unittest.mock import patch

from engine import settle
from pipeline.build_site_json import build
from scripts import run_scheduler as scheduler
from tests.helpers import DatabaseTest


class SettlementTests(DatabaseTest):
    def test_recommendations_settle_at_captured_prices_and_are_idempotent(self):
        self.game(status="final")
        self.recommendation()
        self.recommendation(market="spread", pick="away +12", odds=1.9, line=12)
        self.recommendation(market="total", pick="over", odds=2.1, line=210)
        self.con.execute(
            "INSERT INTO predictions(game_id,model,market,pick,odds) "
            "VALUES('game','GPT','moneyline','BOS',1.7)"
        )
        with patch.object(scheduler, "queue_message") as queue:
            scheduler.settlement_pass(self.con, False)
            scheduler.settlement_pass(self.con, False)
        self.assertEqual(queue.call_count, 1)
        self.assertEqual(settle.settle(self.con), {"GPT": [1, 1, 70.0]})
        grades = self.con.execute("SELECT market,result,pnl,settled_at FROM recommendations ORDER BY market").fetchall()
        self.assertEqual([(r["market"], r["result"], r["pnl"]) for r in grades],
                         [("moneyline", "WON", 70.0), ("spread", "WON", 90.0), ("total", "PUSH", 0.0)])
        self.assertTrue(all(r["settled_at"] for r in grades))

    def test_no_price_is_ungraded_for_winning_and_losing_recommendations(self):
        self.game(status="final")
        self.recommendation(pick="home", odds=None)
        self.recommendation(pick="away", odds=None)
        grades = settle.grade_recommendations_for_game(self.con, "game")
        self.assertEqual([(r["result"], r["pnl"]) for r in grades], [("UNGRADED", None)] * 2)

    def test_prediction_push_needs_no_price(self):
        self.game(status="final", home_score=100, away_score=100)
        self.con.execute(
            "INSERT INTO predictions(game_id,model,market,pick) VALUES('game','GPT','moneyline','BOS')"
        )
        self.assertEqual(settle.settle(self.con), {})
        p = self.con.execute("SELECT result,pnl FROM predictions").fetchone()
        self.assertEqual(tuple(p), ("PUSH", 0.0))

    def test_summary_keeps_old_profit_when_result_cards_age_out(self):
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        self.game(gid="old", status="final", tip=old)
        self.recommendation(gid="old")
        self.game(gid="recent", status="final", home_score=90, away_score=100)
        self.recommendation(gid="recent")
        for gid in ("old", "recent"):
            settle.grade_recommendations_for_game(self.con, gid)
        summary = build(self.con)
        self.assertEqual(len(summary["results"]), 1)
        self.assertEqual(summary["summary"]["n_recs"], 2)
        self.assertEqual(summary["summary"]["recs_won"], 1)
        self.assertEqual(summary["summary"]["recs_graded"], 2)
        self.assertEqual(summary["summary"]["recs_pnl"], -30.0)

    def test_empty_summary_has_zero_counts(self):
        s = build(self.con)["summary"]
        self.assertEqual((s["n_recs"], s["recs_won"], s["recs_graded"], s["recs_pnl"]), (0, 0, 0, 0))


class BilingualSiteTests(DatabaseTest):
    def test_site_carries_english_and_chinese_fields(self):
        self.game()  # LAL @ BOS, tipping in an hour
        self.recommendation(market="spread", pick="away +4.5", odds=1.9, line=4.5)
        self.recommendation(market="total", pick="under", odds=1.9, line=221.5)
        self.con.execute(
            "INSERT INTO predictions(game_id,model,market,pick,win_prob,pred_margin,pred_total,reasoning,reasoning_zh) "
            "VALUES('game','GPT','moneyline','BOS',0.6,4,220,'Boston is rested.','塞爾提克體力充足。')"
        )
        self.con.commit()
        g = build(self.con)["upcoming"][0]
        self.assertEqual((g["home_en"], g["away_en"]), ("Celtics", "Lakers"))
        self.assertEqual((g["home_zh"], g["away_zh"]), ("塞爾提克", "湖人"))
        m = g["models"][0]
        self.assertEqual((m["reasoning"], m["reasoning_zh"]), ("Boston is rested.", "塞爾提克體力充足。"))
        self.assertEqual((m["pick_en"], m["pick_zh"]), ("Celtics", "塞爾提克"))
        self.assertEqual(g["consensus"]["fav_en"], "Celtics")
        labels = {r["market"]: (r["label"], r["label_en"]) for r in g["recs"]}
        self.assertEqual(labels["spread"], ("讓分 湖人 +4.5", "Lakers +4.5"))
        self.assertEqual(labels["total"], ("總分 小 221.5", "Under 221.5"))

    def test_prompt_asks_for_chinese_reasoning(self):
        from models.predict import build_prompt
        self.assertIn('"reasoning_zh"', build_prompt({"home": "BOS", "away": "LAL"}))
