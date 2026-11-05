import contextlib
import datetime
import io
import os
from pathlib import Path
import re
import tempfile
from unittest.mock import patch

from scripts import run_scheduler as scheduler
from tests.helpers import DatabaseTest

NOW = datetime.datetime(2026, 11, 5, 18, tzinfo=datetime.timezone.utc)
GOOD = {"winner": "BOS", "win_prob": 0.8, "pred_home_score": 120, "pred_away_score": 105, "pred_total": 225}


class NotificationTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        self.contexts.enter_context(patch.object(scheduler, "utcnow", return_value=NOW))
        self.contexts.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.queued = self.contexts.enter_context(patch.object(scheduler, "queue_message"))

    def test_provider_failure_leaves_game_retryable_then_success_marks_it(self):
        self.game(tip=NOW + datetime.timedelta(hours=2))
        with patch.object(scheduler.M, "predict_all", return_value={"GPT": {"error": "timeout"}}):
            scheduler.notification_pass(self.con, 3, False, False)
        self.assertIsNone(self.con.execute("SELECT notified_at FROM games").fetchone()[0])
        self.queued.assert_not_called()
        with patch.object(scheduler.M, "predict_all", return_value={"GPT": GOOD}) as predict:
            scheduler.notification_pass(self.con, 3, False, False)
            scheduler.notification_pass(self.con, 3, False, False)
        self.assertEqual(predict.call_count, 1)
        self.assertIsNotNone(self.con.execute("SELECT notified_at FROM games").fetchone()[0])
        self.queued.assert_called_once()

    def test_disabled_models_leave_game_retryable(self):
        self.game(tip=NOW + datetime.timedelta(hours=2))
        with patch.object(scheduler.M, "predict_all") as predict:
            scheduler.notification_pass(self.con, 3, True, False)
        predict.assert_not_called()
        self.queued.assert_not_called()
        self.assertIsNone(self.con.execute("SELECT notified_at FROM games").fetchone()[0])

    def test_one_failed_game_does_not_suppress_other_games(self):
        self.game(gid="failed", tip=NOW + datetime.timedelta(hours=1))
        self.game(gid="good", tip=NOW + datetime.timedelta(hours=2))
        with patch.object(scheduler.M, "predict_all", side_effect=[{"GPT": {"error": "timeout"}}, {"GPT": GOOD}]):
            scheduler.notification_pass(self.con, 3, False, False)
        rows = dict(self.con.execute("SELECT game_id,notified_at FROM games"))
        self.assertIsNone(rows["failed"])
        self.assertIsNotNone(rows["good"])
        self.queued.assert_called_once()

    def test_documented_cron_covers_afternoon_and_evening_tips(self):
        wrapper = (Path(scheduler.ROOT) / "scripts/cron-nba.sh").read_text()
        start, end, step = map(int, re.search(r"#\s+0\s+(\d+)-(\d+)/(\d+)\s+\* \* \*.*sweep", wrapper).groups())
        window = float(re.search(r'--hours-ahead \$\{2:-(\d+)\}', wrapper).group(1))
        # Includes the previously missed 13:30 MST tip and the last evening tips.
        for minute in range(10 * 60, 22 * 60 + 1, 30):
            tip = NOW.astimezone(datetime.timezone(datetime.timedelta(hours=-7))).replace(
                hour=minute // 60, minute=minute % 60)
            eligible = [hour for hour in range(start, end + 1, step) if 0 < minute / 60 - hour <= window]
            self.assertTrue(eligible, f"no sweep covers {tip.isoformat()}")
            run = tip.replace(hour=eligible[0], minute=0).astimezone(datetime.timezone.utc)
            gid = str(minute)
            self.game(gid=gid, tip=tip)
            with patch.object(scheduler, "utcnow", return_value=run), \
                 patch.object(scheduler.M, "predict_all", return_value={"GPT": GOOD}):
                scheduler.notification_pass(self.con, window, False, False)
            self.assertIsNotNone(self.con.execute("SELECT notified_at FROM games WHERE game_id=?", (gid,)).fetchone()[0])


class PendingTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        self.pending = Path(self.contexts.enter_context(tempfile.TemporaryDirectory()))
        self.contexts.enter_context(patch.object(scheduler, "PENDING_DIR", str(self.pending)))
        self.contexts.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_consuming_first_section_cannot_strip_routing_from_next_section(self):
        scheduler.queue_message("results", "first message", False)
        first = list(self.pending.glob("*.md"))
        self.assertEqual(len(first), 1)
        first[0].unlink()
        scheduler.queue_message("predictions", "second message", False)
        second = list(self.pending.glob("*.md"))
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0], second[0])
        contents = second[0].read_text()
        self.assertTrue(contents.startswith("---\nchat_id: "))
        self.assertIn(f"chat_id: {scheduler.NBA_CHAT_ID}\n", contents)
        self.assertIn("## predictions\n\nsecond message", contents)

    def test_sections_publish_as_complete_separate_files(self):
        scheduler.queue_message("results", "first message", False)
        scheduler.queue_message("predictions", "second message", False)
        files = list(self.pending.glob("*.md"))
        self.assertEqual(len(files), 2)
        for f in files:
            self.assertTrue(f.read_text().startswith("---\nchat_id: "))

    def test_dry_run_creates_no_files(self):
        scheduler.queue_message("results", "first message", True)
        self.assertEqual(list(self.pending.iterdir()), [])

    def test_reader_sees_nothing_until_the_complete_file_is_renamed(self):
        replace = os.replace

        def inspect_before_publish(src, dst):
            self.assertEqual(list(self.pending.glob("*.md")), [])
            contents = Path(src).read_text()
            self.assertTrue(contents.startswith("---\nchat_id: "))
            self.assertTrue(contents.endswith("## results\n\ncomplete message\n"))
            replace(src, dst)

        with patch.object(scheduler.os, "replace", side_effect=inspect_before_publish) as publish:
            scheduler.queue_message("results", "complete message", False)
        publish.assert_called_once()
        self.assertEqual(len(list(self.pending.glob("*.md"))), 1)

    def test_failed_publish_cleans_up_and_does_not_mark_game_notified(self):
        self.game(tip=NOW + datetime.timedelta(hours=2))
        with patch.object(scheduler, "utcnow", return_value=NOW), \
             patch.object(scheduler.M, "predict_all", return_value={"GPT": GOOD}), \
             patch.object(scheduler.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                scheduler.notification_pass(self.con, 3, False, False)
        self.assertEqual(list(self.pending.iterdir()), [])
        self.assertIsNone(self.con.execute("SELECT notified_at FROM games").fetchone()[0])
