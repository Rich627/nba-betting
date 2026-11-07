import contextlib
import datetime
import io
import json
from pathlib import Path
import re
import zlib
from unittest.mock import patch

from analysis import injuries_rest_b2b
from data import fetch_official_injuries as F, injuries as INJ
from scripts import run_scheduler as scheduler
from tests.helpers import DatabaseTest

NOW = datetime.datetime(2026, 11, 7, 0, 5, tzinfo=datetime.timezone.utc)   # 17:05 MST, 19:05 ET
GOOD = {"winner": "BOS", "win_prob": 0.7, "pred_home_score": 118, "pred_away_score": 108, "pred_total": 226}


def _cell(x, y, *words):
    runs = " ".join(f"({w}) Tj" for w in words)
    return f"q\n{x} {y} 50 20 re\nW* n\nBT\n/F0 -10 Tf\n{x + 4} {y + 15} Td {runs}\nET\nQ\n"


def _pdf(pages):
    """Minimal PDF-like bytes: one Flate stream per page, cells drawn like the GemBox report."""
    out = b"%PDF-1.4\n"
    for i, cells in enumerate(pages, 1):
        body = _cell(84, 35, "Injury", "Report:", "11/06/26", "06:15", "PM") + _cell(84, 35, "Page", str(i), "of",
                                                                                         str(len(pages)))
        body += "".join(_cell(*c) for c in cells)
        out += b"1 0 obj\n<<>>\nstream\n" + zlib.compress(body.encode("cp1252")) + b"\nendstream\nendobj\n"
    return out


HEADER = [(19, 100, "Game", "Date"), (116, 100, "Game", "Time"), (196, 100, "Matchup"), (260, 100, "Team"),
          (421, 100, "Player", "Name"), (582, 100, "Current", "Status"), (662, 100, "Reason")]


class OfficialReportTests(DatabaseTest):
    def test_parses_multipage_report_with_carried_columns_and_wrapped_reasons(self):
        page1 = HEADER + [
            (19, 122, "11/06/2026"), (116, 122, "07:00", "(ET)"), (196, 122, "LAL@BOS"),
            (260, 122, "Boston", "Celtics"), (421, 122, "Brown,", "Jaylen"), (582, 122, "Questionable"),
            (662, 122, "Injury/Illness", "-", "Left", "Knee"),
            (421, 145, "Carter,", "Devin"), (582, 145, "Out"), (662, 145, "G", "League", "-", "Two-", "Way"),
            (260, 167, "Los", "Angeles", "Lakers"), (421, 167, "Doncic,", "Luka"), (582, 167, "Out"),
            (662, 167, "Injury/Illness", "-", "Right"),
        ]
        page2 = [(662, 75, "Hamstring", "Strain"), (421, 97, "James", "Jr.,", "LeBron"), (582, 97, "Probable"),
                 (662, 97, "-"), (196, 120, "NYK@MIA"), (260, 120, "Miami", "Heat"), (662, 120, "NOT", "YET",
                                                                                         "SUBMITTED")]
        data = _pdf([page1, page2])
        items = F.parse_pdf(data)
        self.assertEqual(F.report_label(data), "11/06/26 06:15 PM ET")
        by = {i["player"]: i for i in items}
        self.assertEqual(by["Jaylen Brown"]["status"], "Questionable")
        self.assertEqual(by["Luka Doncic"]["reason"], "Injury/Illness - Right Hamstring Strain")
        self.assertEqual(by["LeBron James Jr."]["team"], "Los Angeles Lakers")
        self.assertEqual(by["LeBron James Jr."]["matchup"], "LAL@BOS")
        self.assertEqual(by["LeBron James Jr."]["reason"], "")
        self.assertEqual(by[None]["status"], "NOT YET SUBMITTED")
        self.assertEqual(by[None]["team"], "Miami Heat")

        n = F.store(self.con, items, "2026-11-06_06_15PM", "11/06/26 06:15 PM ET")
        self.assertEqual(n, 4)  # G League assignment dropped
        teams = {r["team"] for r in self.con.execute("SELECT team FROM official_injuries")}
        self.assertEqual(teams, {"BOS", "LAL", "MIA"})

    def test_candidate_stamps_walk_back_on_the_eastern_15_minute_grid(self):
        stamps = F.candidate_stamps(NOW, steps=3)
        self.assertEqual(stamps, ["2026-11-06_07_00PM", "2026-11-06_06_45PM", "2026-11-06_06_30PM"])


class InjuryViewTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        self.game(gid="g", tip=NOW + datetime.timedelta(minutes=25))
        self.con.executemany(
            "INSERT INTO player_season_stats(player_key,player,team,gp,avg_min,avg_pts,as_of) VALUES(?,?,?,?,?,?,?)",
            [(INJ.norm_name(p), p, t, 8, m, 20, "x") for p, t, m in
             [("Jaylen Brown", "BOS", 34.0), ("Sam Hauser", "BOS", 6.0), ("Luka Dončić", "LAL", 36.0)]])
        self.con.commit()

    def official(self, rows, day="11/06/2026"):
        self.con.execute("DELETE FROM official_injuries")
        self.con.executemany(
            "INSERT INTO official_injuries(team,player,status,reason,matchup,game_date,report,report_label) "
            "VALUES(?,?,?,?,?,?,?,?)", [(t, p, s, "", "LAL@BOS", day, "r", "11/06/26 06:15 PM ET") for t, p, s in rows])
        self.con.commit()

    def test_official_report_wins_for_the_game_date_and_espn_is_the_fallback(self):
        self.con.execute("INSERT INTO team_injuries(team,player,status,comment,as_of) VALUES"
                         "('BOS','Jaylen Brown','Day-To-Day','knee','t1'),('LAL','Luka Doncic','Out','x','t1')")
        self.official([("BOS", "Jaylen Brown", "Questionable"), ("LAL", "", "NOT YET SUBMITTED")])
        snap = INJ.snapshot(self.con, "g")
        self.assertEqual(snap["teams"]["BOS"][0]["status"], "Questionable")
        self.assertEqual(snap["teams"]["BOS"][0]["source"], "official")
        self.assertEqual(snap["teams"]["LAL"][0]["source"], "espn")       # not filed yet -> ESPN
        self.assertEqual(snap["teams"]["LAL"][0]["min"], 36.0)            # accent-insensitive name match
        self.official([("BOS", "Jaylen Brown", "Out")], day="11/07/2026")  # tomorrow's game only
        self.assertEqual(INJ.snapshot(self.con, "g")["teams"]["BOS"][0]["source"], "espn")

    def test_diff_flags_rotation_players_only(self):
        self.official([("BOS", "Jaylen Brown", "Questionable"), ("BOS", "Sam Hauser", "Questionable"),
                       ("BOS", "Never Played", "Questionable")])
        before = INJ.snapshot(self.con, "g")
        self.official([("BOS", "Jaylen Brown", "Out"), ("BOS", "Sam Hauser", "Out"), ("BOS", "Never Played", "Out")])
        changes = INJ.diff(before, INJ.snapshot(self.con, "g"))
        self.assertEqual([(c["player"], c["old"], c["new"]) for c in changes],
                         [("Jaylen Brown", "Questionable", "Out")])
        self.assertAlmostEqual(changes[0]["delta"], 20.4)

    def test_injury_module_runs_without_nba_api_player_rows(self):
        self.official([("LAL", "Luka Doncic", "Out")])
        res = injuries_rest_b2b.analyze("g", self.con)
        self.assertGreater(res["score"], 0)  # away team missing its star favours home
        self.assertIn("LAL -36 min", res["reasoning_md"])


class RecheckTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        self.contexts.enter_context(patch.object(scheduler, "utcnow", return_value=NOW))
        self.contexts.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.queued = self.contexts.enter_context(patch.object(scheduler, "queue_message"))
        self.contexts.enter_context(patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}))
        self.con.execute("INSERT INTO player_season_stats(player_key,player,team,gp,avg_min,avg_pts,as_of) "
                         "VALUES('jaylen brown','Jaylen Brown','BOS',8,34,25,'x')")
        self.con.commit()

    def predicted_game(self, status, minutes=20):
        self.game(gid="g", tip=NOW + datetime.timedelta(minutes=minutes))
        self.set_status(status)
        with patch.object(scheduler.M, "predict_all", return_value={"GPT": GOOD}):
            scheduler.notification_pass(self.con, 3, False, False)
        self.recommendation(gid="g", pick="home", odds=1.8)

    def set_status(self, status):
        self.con.execute("DELETE FROM official_injuries")
        self.con.execute(
            "INSERT INTO official_injuries(team,player,status,reason,matchup,game_date,report,report_label) "
            "VALUES('BOS','Jaylen Brown',?,'knee','LAL@BOS','11/06/2026','r','label')", (status,))
        self.con.commit()

    def test_prediction_stores_the_snapshot_the_models_saw(self):
        self.predicted_game("Questionable", minutes=120)
        snap = INJ.load_snapshot(self.con, "g")
        self.assertEqual(snap["teams"]["BOS"][0]["status"], "Questionable")

    def test_material_change_voids_recs_reruns_once_and_alerts(self):
        self.predicted_game("Questionable")
        self.queued.reset_mock()
        self.set_status("Out")
        refresh = []
        with patch.object(scheduler.M, "predict_all", return_value={"GPT": {**GOOD, "win_prob": 0.55}}) as rerun:
            n = scheduler.recheck_pass(self.con, False, refresh_odds=lambda: refresh.append(1))
            again = scheduler.recheck_pass(self.con, False, refresh_odds=lambda: refresh.append(1))
        self.assertEqual((n, again, len(refresh), rerun.call_count), (1, 0, 1, 1))
        section, msg = self.queued.call_args[0][:2]
        self.assertEqual(section, "賽前異動")
        self.assertIn("Jaylen Brown：出賽成疑 → *缺陣*", msg)
        self.assertIn("原建議作廢：獨贏", msg)
        payload = json.loads(self.con.execute("SELECT payload FROM injury_snapshots WHERE kind='recheck'").fetchone()[0])
        self.assertEqual(payload["voided"][0]["odds"], 1.8)
        self.assertIsNotNone(self.con.execute("SELECT rechecked_at FROM games").fetchone()[0])
        # baseline stays what the original prediction saw
        self.assertEqual(INJ.load_snapshot(self.con, "g")["teams"]["BOS"][0]["status"], "Questionable")

    def test_no_change_marks_checked_without_message_or_spend(self):
        self.predicted_game("Out")
        self.queued.reset_mock()
        with patch.object(scheduler.M, "predict_all") as rerun:
            n = scheduler.recheck_pass(self.con, False, refresh_odds=lambda: self.fail("no odds call"))
        self.assertEqual(n, 0)
        rerun.assert_not_called()
        self.queued.assert_not_called()
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0], 1)
        self.assertIsNotNone(self.con.execute("SELECT rechecked_at FROM games").fetchone()[0])

    def test_failed_rerun_still_voids_and_warns(self):
        self.predicted_game("Questionable")
        self.set_status("Out")
        with patch.object(scheduler.M, "predict_all", return_value={"GPT": {"error": "timeout"}}):
            scheduler.recheck_pass(self.con, False)
        self.assertIn("重跑失敗，這場先別下", self.queued.call_args[0][1])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0], 0)

    def test_games_outside_the_window_are_left_for_a_later_run(self):
        self.predicted_game("Questionable", minutes=60)
        self.set_status("Out")
        self.assertEqual(scheduler.recheck_pass(self.con, False), 0)
        self.assertIsNone(self.con.execute("SELECT rechecked_at FROM games").fetchone()[0])

    def test_documented_recheck_cron_reaches_every_tip(self):
        wrapper = (Path(scheduler.ROOT) / "scripts/cron-nba.sh").read_text()
        mins, start, end = re.search(r"#\s+([\d,]+)\s+(\d+)-(\d+)\s+\* \* \*.*recheck", wrapper).groups()
        runs = [h * 60 + int(m) for h in range(int(start), int(end) + 1) for m in mins.split(",")]
        lo, hi = scheduler.RECHECK_WINDOW_MIN
        for tip in range(10 * 60, 20 * 60 + 31, 5):   # 10:00-20:30 MST
            hits = [r for r in runs if lo < tip - r <= hi]
            self.assertTrue(hits, f"no recheck reaches a {tip // 60}:{tip % 60:02d} tip")
            if tip % 30 == 0:  # the usual :00/:30 tips also get a fallback run
                self.assertGreaterEqual(len(hits), 2, f"{tip // 60}:{tip % 60:02d} tip has no fallback run")
