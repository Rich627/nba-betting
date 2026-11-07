import unittest

from engine.edge import market_home_prob, recommend, shrink_prob, weighted_consensus


def consensus(home_win_prob, margin, total=220.0, agreement=1.0):
    return {"home_win_prob": home_win_prob, "pred_margin": margin, "pred_total": total,
            "agreement": agreement, "n_models": 7}


def moneyline(recs):
    return [r for r in recs if r["market"] == "moneyline"]


class MarketProbTests(unittest.TestCase):
    def test_vig_is_removed(self):
        self.assertEqual(market_home_prob({"ml_home": 1.91, "ml_away": 1.91}), 0.5)
        self.assertAlmostEqual(market_home_prob({"ml_home": 1.36, "ml_away": 3.30}), 0.708, places=3)

    def test_missing_or_invalid_price_means_no_reference(self):
        for odds in ({}, {"ml_home": 1.9}, {"ml_home": 1.9, "ml_away": 1.0}, {"ml_home": "x", "ml_away": 1.9}):
            self.assertIsNone(market_home_prob(odds), odds)

    def test_shrink_is_halfway_by_default(self):
        self.assertEqual(shrink_prob(0.58, 0.40), 0.49)
        self.assertEqual(shrink_prob(0.58, 0.40, shrink=0), 0.58)
        self.assertEqual(shrink_prob(0.58, 0.40, shrink=1), 0.40)


class MoneylineTests(unittest.TestCase):
    def test_side_the_models_call_a_loser_is_not_bet(self):
        # NOP 2026-11-06: six of seven models took MIN, but a raw 44% x 2.46 looked like EV +8%.
        odds = {"ml_home": 2.46, "ml_away": 1.58}
        self.assertEqual(moneyline(recommend(consensus(0.44, -3.0), odds)), [])

    def test_shrunk_edge_above_the_cap_is_treated_as_model_error(self):
        # OKC@MEM 2026-11-06: models 58% home vs market 41% -> EV +40% raw, +20% shrunk.
        odds = {"ml_home": 2.42, "ml_away": 1.58}
        self.assertEqual(moneyline(recommend(consensus(0.58, 5.0), odds)), [])
        # The same disagreement is accepted when the caller raises the cap.
        self.assertEqual(len(moneyline(recommend(consensus(0.58, 5.0), odds, max_edge=0.5))), 1)

    def test_moderate_disagreement_with_a_fair_market_qualifies(self):
        odds = {"ml_home": 1.95, "ml_away": 1.95}
        recs = moneyline(recommend(consensus(0.62, 4.0), odds))
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["pick"], "home")
        self.assertEqual(r["odds"], 1.95)
        self.assertEqual(r["edge"], 0.092)  # 0.56 x 1.95 - 1, not 0.62 x 1.95 - 1
        self.assertEqual(r["confidence"], "med")
        self.assertIn("市場 50%", r["why"])

    def test_high_confidence_needs_strong_edge_and_agreement(self):
        odds = {"ml_home": 1.95, "ml_away": 1.95}
        r = moneyline(recommend(consensus(0.66, 6.0, agreement=0.86), odds))[0]
        self.assertEqual(r["edge"], 0.131)
        self.assertEqual(r["confidence"], "high")
        r = moneyline(recommend(consensus(0.66, 6.0, agreement=0.71), odds))[0]
        self.assertEqual(r["confidence"], "med")

    def test_away_side_uses_the_away_prices(self):
        odds = {"ml_home": 3.30, "ml_away": 1.36}
        # Model 78% away vs market 70.8% shrinks to 74.4% -> EV +1%, not a bet.
        self.assertEqual(moneyline(recommend(consensus(0.22, -9.0), odds)), [])
        r = moneyline(recommend(consensus(0.14, -12.0), odds))[0]
        self.assertEqual((r["pick"], r["odds"]), ("away", 1.36))
        self.assertEqual(r["edge"], 0.066)  # market away 0.708, model 0.86 -> 0.784 x 1.36 - 1

    def test_one_sided_market_gives_no_moneyline_bet_but_spread_still_works(self):
        odds = {"ml_home": 1.5, "spread_line": -2.5, "spread_home_odds": 1.91, "spread_away_odds": 1.91}
        recs = recommend(consensus(0.75, 8.0), odds)
        self.assertEqual(moneyline(recs), [])
        self.assertEqual([r["market"] for r in recs], ["spread"])

    def test_no_bet_below_agreement(self):
        odds = {"ml_home": 1.95, "ml_away": 1.95}
        self.assertEqual(recommend(consensus(0.62, 4.0, agreement=0.5), odds), [])


class OneBetPerSideTests(unittest.TestCase):
    def test_spread_on_the_moneyline_side_is_dropped(self):
        odds = {"ml_home": 1.95, "ml_away": 1.95,
                "spread_line": -1.5, "spread_home_odds": 1.91, "spread_away_odds": 1.91}
        recs = recommend(consensus(0.62, 6.0), odds)
        self.assertEqual([r["market"] for r in recs], ["moneyline"])

    def test_spread_on_the_other_side_is_kept(self):
        # Models like the home team to win, but not by the 7 the market asks for.
        odds = {"ml_home": 1.95, "ml_away": 1.95,
                "spread_line": -7.0, "spread_home_odds": 1.91, "spread_away_odds": 1.91}
        recs = recommend(consensus(0.62, 2.0), odds)
        self.assertEqual([(r["market"], r["pick"]) for r in recs], [("moneyline", "home"), ("spread", "away +7")])

    def test_spread_is_dropped_too_when_the_models_are_too_far_from_the_market(self):
        # OKC@MEM 2026-11-06: "MEM by 5" vs the market's "OKC by 4" is the same wrong
        # opinion that produced the EV +40% moneyline; both lost.
        odds = {"ml_home": 2.42, "ml_away": 1.58,
                "spread_line": 4.0, "spread_home_odds": 1.91, "spread_away_odds": 1.91}
        self.assertEqual(recommend(consensus(0.58, 5.0), odds), [])

    def test_spread_alone_survives_when_the_moneyline_edge_is_merely_small(self):
        odds = {"ml_home": 1.95, "ml_away": 1.95,
                "spread_line": -1.5, "spread_home_odds": 1.91, "spread_away_odds": 1.91}
        recs = recommend(consensus(0.55, 6.0), odds)  # shrunk 52.5% x 1.95 -> EV +2%
        self.assertEqual([(r["market"], r["pick"]) for r in recs], [("spread", "home -1.5")])


class ConsensusTests(unittest.TestCase):
    def test_weighted_consensus_is_unchanged(self):
        preds = {"A": {"home_win_prob": 0.6, "pred_home_score": 110, "pred_away_score": 100, "pred_total": 210},
                 "B": {"home_win_prob": 0.5, "pred_home_score": 105, "pred_away_score": 105, "pred_total": 210},
                 "C": {"error": "timeout"}}
        c = weighted_consensus(preds)
        self.assertEqual((c["home_win_prob"], c["pred_margin"], c["agreement"], c["n_models"]), (0.55, 5.0, 1.0, 2))


if __name__ == "__main__":
    unittest.main()
