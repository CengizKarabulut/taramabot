import unittest

import pandas as pd

from fundamental_data_pipeline import build_trend_payload, normalize_peer_snapshot


class FundamentalDataPipelineTests(unittest.TestCase):
    def test_peer_columns_are_normalized(self):
        raw = pd.DataFrame({
            "Ticker": ["AAA.IS", "BBB"],
            "Sektör": ["Sanayi", "Sanayi"],
            "F_K": [8.0, 12.0],
            "PD_DD": [1.1, 2.0],
            "ROE": [20.0, 10.0],
        })
        out = normalize_peer_snapshot(raw)
        self.assertEqual(out.loc[0, "symbol"], "AAA")
        self.assertIn("sector", out.columns)
        self.assertIn("pe", out.columns)
        self.assertIn("pb", out.columns)
        self.assertIn("roe", out.columns)

    def test_trend_payload_rewards_growth_margin_cash_and_lower_leverage(self):
        periods = ["2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"]
        income = pd.DataFrame({
            p: vals for p, vals in zip(periods, zip(
                [100, 105, 110, 115, 130, 140, 150, 160],
                [20, 21, 23, 24, 30, 33, 36, 40],
                [10, 11, 12, 12, 18, 20, 22, 25],
                [8, 9, 10, 10, 15, 17, 19, 21],
            ))
        }, index=["Hasılat", "Esas Faaliyet Karı (Zararı)", "Dönem Karı (Zararı)", "Ana Ortaklık Payları"])
        balance = pd.DataFrame({
            p: vals for p, vals in zip(periods, zip(
                [20, 21, 22, 23, 30, 31, 32, 35],
                [200, 205, 210, 215, 230, 240, 250, 260],
                [100, 105, 110, 115, 130, 140, 150, 165],
                [50, 49, 48, 47, 45, 44, 43, 42],
                [5, 5, 5, 5, 4, 4, 4, 4],
                [10, 10, 10, 10, 9, 9, 9, 9],
            ))
        }, index=["Nakit ve Nakit Benzerleri", "Toplam Varlıklar", "Özkaynaklar", "Kısa Vadeli Borçlanmalar", "Uzun Vadeli Borçlanmaların Kısa Vadeli Kısımları", "Uzun Vadeli Borçlanmalar"])
        cash = pd.DataFrame({p: [v] for p, v in zip(periods, [9, 10, 11, 11, 17, 19, 21, 23])}, index=["İşletme Faaliyetlerinden Nakit Akışları"])
        payload = build_trend_payload("AAA", "Sanayi", balance, income, cash, "XI_29")
        self.assertGreaterEqual(payload["trend_score"], 75)
        self.assertGreater(payload["snapshot"]["revenue_yoy"], 0)
        self.assertGreaterEqual(payload["snapshot"]["cfo_net_income"], 0.8)
        self.assertLessEqual(payload["snapshot"]["net_debt_equity_yoy_change_pp"], 0)

    def test_missing_cashflow_does_not_become_zero(self):
        periods = ["2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1"]
        income = pd.DataFrame({p: [100 + i * 10, 10 + i] for i, p in enumerate(periods)}, index=["Hasılat", "Dönem Karı (Zararı)"])
        balance = pd.DataFrame({p: [100, 50] for p in periods}, index=["Toplam Varlıklar", "Özkaynaklar"])
        payload = build_trend_payload("AAA", "Sanayi", balance, income, None, "XI_29")
        self.assertIsNone(payload["snapshot"]["cfo_net_income"])
        self.assertLess(payload["trend_coverage"], 100)


if __name__ == "__main__":
    unittest.main()
