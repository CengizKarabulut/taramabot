import unittest
import pandas as pd

from fundamental_score import score_label, score_peer_group, specs_for_sector


class FundamentalScoreTests(unittest.TestCase):
    def test_lower_valuation_gets_better_peer_score(self):
        df = pd.DataFrame({
            "symbol": ["AAA", "BBB", "CCC"],
            "sector": ["Sanayi"] * 3,
            "pe": [5.0, 10.0, 20.0],
            "roe": [20.0, 20.0, 20.0],
        })
        out = score_peer_group(df)
        self.assertGreater(out.loc[0, "metric_score__pe"], out.loc[2, "metric_score__pe"])

    def test_higher_profitability_gets_better_peer_score(self):
        df = pd.DataFrame({
            "symbol": ["AAA", "BBB", "CCC"],
            "sector": ["Sanayi"] * 3,
            "roe": [10.0, 20.0, 30.0],
        })
        out = score_peer_group(df)
        self.assertGreater(out.loc[2, "metric_score__roe"], out.loc[0, "metric_score__roe"])

    def test_bank_uses_bank_specific_metrics(self):
        names = {spec.name for spec in specs_for_sector("Banka")}
        self.assertIn("capital_adequacy", names)
        self.assertIn("npl_ratio", names)
        self.assertNotIn("inventory_days", names)

    def test_reit_uses_nav_metrics(self):
        names = {spec.name for spec in specs_for_sector("GYO")}
        self.assertIn("p_nav", names)
        self.assertIn("nav_growth_yoy", names)

    def test_missing_metrics_reduce_coverage_not_crash(self):
        df = pd.DataFrame({"symbol": ["AAA", "BBB"], "sector": ["Sanayi", "Sanayi"], "roe": [10.0, 20.0]})
        out = score_peer_group(df)
        self.assertTrue(out["fundamental_score"].notna().all())
        self.assertTrue((out["fundamental_coverage"] < 40).all())

    def test_score_labels(self):
        self.assertEqual(score_label(82), "COK GUCLU")
        self.assertEqual(score_label(52), "NOTR / ORTA")
        self.assertEqual(score_label(20), "COK ZAYIF")


if __name__ == "__main__":
    unittest.main()
