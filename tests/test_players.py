import contextlib
import io
from unittest.mock import patch

import pandas as pd

from analysis import lineup_pace_matchup, player_ability
from data import fetch_players
from features.availability import team_strength
from scripts import run_scheduler as scheduler
from tests.helpers import DatabaseTest

KEYS = ["minutes", "points", "fieldGoalsMade-fieldGoalsAttempted",
        "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "freeThrowsMade-freeThrowsAttempted",
        "rebounds", "assists", "turnovers", "steals", "blocks", "offensiveRebounds", "defensiveRebounds",
        "fouls", "plusMinus"]


def _ath(pid, name, pos, stats, starter=True, dnp=False):
    return {"athlete": {"id": str(pid), "displayName": name, "position": {"abbreviation": pos}},
            "starter": starter, "didNotPlay": dnp, "stats": stats}


def summary(home_pts=(30, 20), away_pts=(25, 15)):
    """Two players a side (plus one DNP) playing the full 240 team minutes."""
    def side(tid, abbr, base, pts):
        return {"team": {"id": str(tid), "abbreviation": abbr}, "statistics": [{"keys": KEYS, "athletes": [
            _ath(base + 1, f"{abbr} Guard", "PG", ["120", str(pts[0]), "10-20", "2-6", "8-10", "5", "8", "3",
                                                   "2", "0", "1", "4", "2", "+5"]),
            _ath(base + 2, f"{abbr} Big", "C", ["120", str(pts[1]), "8-12", "0-0", "4-6", "12", "2", "2",
                                                "1", "3", "4", "8", "4", "+5"]),
            _ath(base + 3, f"{abbr} Bench", "SF", [], starter=False, dnp=True),
        ]}]}
    return {"header": {"competitions": [{"competitors": [
                {"team": {"id": "2"}, "homeAway": "home"}, {"team": {"id": "13"}, "homeAway": "away"}]}]},
            "boxscore": {"players": [side(13, "LAL", 100, away_pts), side(2, "BOS", 200, home_pts)]}}


class ParseTests(DatabaseTest):
    def test_box_parses_and_derives_advanced_stats(self):
        rows = fetch_players.parse_summary(summary())
        self.assertEqual(len(rows), 4)                      # DNP skipped
        g = next(r for r in rows if r["name"] == "BOS Guard")
        self.assertEqual((g["team"], g["is_home"], g["position"], g["started"]), ("BOS", 1, "G", 1))
        self.assertEqual((g["fgm"], g["fga"], g["fg3m"], g["fta"]), (10, 20, 2, 10))
        self.assertAlmostEqual(g["ts_pct"], 30 / (2 * (20 + 4.4)), places=3)
        self.assertAlmostEqual(g["efg_pct"], 11 / 20, places=3)
        for side in ("BOS", "LAL"):                          # minute-weighted usage sums to 100%
            team = [r for r in rows if r["team"] == side]
            self.assertAlmostEqual(sum(r["usg_pct"] * r["min"] / 48.0 for r in team), 1.0, places=2)
        self.assertAlmostEqual(sum(r["pie"] for r in rows), 1.0, places=2)
        lal = next(r for r in rows if r["team"] == "LAL")
        self.assertAlmostEqual(g["off_rating"], lal["def_rating"])
        self.assertGreater(g["net_rating"], 0)               # BOS scored 50, LAL 40

    def test_store_is_idempotent(self):
        rows = fetch_players.parse_summary(summary())
        fetch_players.store_rows(self.con, "g1", "2026-11-01", rows)
        fetch_players.store_rows(self.con, "g1", "2026-11-01", rows)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM player_game_stats").fetchone()[0], 4)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM players").fetchone()[0], 4)

    def test_fetch_new_skips_failures_and_stops_at_budget(self):
        for gid in ("a", "b"):
            self.game(gid, status="final")
            self.con.execute("UPDATE games SET espn_event_id=? WHERE game_id=?", ("e" + gid, gid))
        self.con.commit()
        with patch.object(fetch_players, "_get", side_effect=TimeoutError("slow")):
            ok, rows, failed = fetch_players.fetch_new(self.con, pause=0, log=lambda m: None)
        self.assertEqual((ok, rows, failed), (0, 0, 2))
        clock = iter([0.0, 0.0, 100.0])                    # t0, before game 1, before game 2
        with patch.object(fetch_players, "_get", return_value=summary()), \
                patch.object(fetch_players.time, "monotonic", side_effect=lambda: next(clock)):
            ok, rows, failed = fetch_players.fetch_new(self.con, pause=0, budget_s=10, log=lambda m: None)
        self.assertEqual(ok, 1)                              # first game, then budget hit
        with patch.object(fetch_players, "_get", return_value=summary()):
            ok, rows, failed = fetch_players.fetch_new(self.con, pause=0, log=lambda m: None)
        self.assertEqual((ok, rows), (1, 4))                 # only the remaining game

    def test_scheduler_pass_never_raises(self):
        with patch("data.fetch_players.fetch_new", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            scheduler.player_stats_pass(self.con)
        self.assertIn("[players] FAILED", out.getvalue())


class FeatureTests(DatabaseTest):
    def test_out_player_drops_from_team_strength(self):
        zdf = pd.DataFrame([
            {"player_id": 1, "name": "Star Guy", "team": "BOS", "r_min": 36.0},
            {"player_id": 2, "name": "Bench Guy", "team": "BOS", "r_min": 12.0},
        ])
        strength = {1: 2.0, 2: -1.0}
        full, _ = team_strength(zdf, strength, "BOS")
        snap = {"teams": {"BOS": [{"player": "Star Guy", "status": "Out", "min": 36.0}]}}
        hurt, rows = team_strength(zdf, strength, "BOS", snap)
        self.assertAlmostEqual(full, (36 * 2 - 12) / 48)
        self.assertAlmostEqual(hurt, -1.0)
        self.assertEqual(list(rows["p_play"]), [0.0, 1.0])

    def _season(self, home_pts, away_pts, n=4):
        for i in range(n):
            rows = fetch_players.parse_summary(summary(home_pts, away_pts))
            fetch_players.store_rows(self.con, f"past{i}", f"2026-10-2{i}", rows)

    def test_modules_score_the_better_team(self):
        self._season((40, 30), (20, 10))   # BOS has been much better
        g = self.game("live")
        self.con.execute("UPDATE games SET game_date='2026-11-05' WHERE game_id='live'")
        lp = lineup_pace_matchup.analyze("live", self.con)
        self.assertGreater(lp["score"], 0)                   # home (BOS) better net rating
        pa = player_ability.analyze("live", self.con, injury_snapshot={"teams": {}})
        self.assertGreater(pa["confidence"], 0)
        self.assertIn("BOS", pa["reasoning_md"])
        self.assertEqual(g["home_team"], "BOS")
