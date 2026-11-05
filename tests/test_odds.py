from data.fetch_odds import game_odds_dict
from engine.edge import recommend
from tests.helpers import DatabaseTest


class OddsTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        self.game()

    def test_spread_and_total_quotes_are_observed_line_price_pairs(self):
        for book, line, price, total in [("a", -1.5, 1.5, 220.5), ("b", -2.5, 2.5, 222.5)]:
            self.quote(book, "spreads", "BOS", line, price)
            self.quote(book, "spreads", "LAL", -line, price)
            self.quote(book, "totals", "Over", total, price)
            self.quote(book, "totals", "Under", total, price)
        o = game_odds_dict(self.con, "game")
        for market, selection, line, price in [
            ("spreads", "BOS", o["spread_line"], o["spread_home_odds"]),
            ("spreads", "LAL", -o["spread_line"], o["spread_away_odds"]),
            ("totals", "Over", o["total_line"], o["over_odds"]),
            ("totals", "Under", o["total_line"], o["under_odds"]),
        ]:
            self.assertIsNotNone(self.con.execute(
                "SELECT 1 FROM odds WHERE market=? AND selection=? AND line=? AND price=?",
                (market, selection, line, price),
            ).fetchone(), (market, selection, line, price))

    def test_most_common_line_is_used_without_mixing_prices_from_other_lines(self):
        for book, line, price in [("a", -3.5, 1.9), ("b", -3.5, 1.95), ("c", -1.5, 1.4)]:
            self.quote(book, "spreads", "BOS", line, price)
            self.quote(book, "spreads", "LAL", -line, price)
        o = game_odds_dict(self.con, "game")
        self.assertEqual(o["spread_line"], -3.5)
        self.assertIn(o["spread_home_odds"], [1.9, 1.95])

    def test_moneyline_selects_an_observed_middle_price(self):
        for book, price in [("a", 1.8), ("b", 1.9)]:
            self.quote(book, "h2h", "BOS", None, price)
            self.quote(book, "h2h", "LAL", None, 2.1)
        self.assertIn(game_odds_dict(self.con, "game")["ml_home"], [1.8, 1.9])

    def test_incomplete_or_mismatched_sides_are_not_invented(self):
        self.quote("a", "h2h", "BOS", None, 1.8)
        self.quote("a", "spreads", "BOS", -2.5, 1.9)
        self.quote("b", "spreads", "LAL", 3.5, 1.9)
        self.quote("a", "totals", "Over", 220.5, 1.9)
        self.quote("b", "totals", "Under", 221.5, 1.9)
        o = game_odds_dict(self.con, "game")
        self.assertNotIn("ml_home", o)
        self.assertNotIn("spread_line", o)
        self.assertNotIn("total_line", o)

    def test_no_recommendation_without_a_valid_price_for_its_side(self):
        c = {"home_win_prob": 0.8, "agreement": 1, "pred_margin": 12, "pred_total": 230}
        for invalid_price in (None, 0, 1):
            o = {"spread_line": -3.5, "spread_home_odds": invalid_price,
                 "total_line": 220, "over_odds": invalid_price}
            self.assertEqual(recommend(c, o), [])
