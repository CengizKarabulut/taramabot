from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from exit_engine import build_exit_plan
from strategy_optimizer import wilson_lower_bound, robust_metrics
from trade_backtester import simulate_trade


class RiskEngineTests(unittest.TestCase):
    def make_frame(self, count: int = 80, start: float = 100.0) -> pd.DataFrame:
        index = pd.date_range("2025-01-01", periods=count, freq="D")
        close = pd.Series(start + np.linspace(0, 8, count), index=index)
        return pd.DataFrame(
            {
                "open": close - 0.20,
                "high": close + 1.00,
                "low": close - 1.00,
                "close": close,
                "volume": np.linspace(1_000_000, 1_500_000, count),
            },
            index=index,
        )

    def test_exit_plan_is_monotonic(self):
        frame = self.make_frame()
        plan = build_exit_plan(frame, "rsi_macd")
        self.assertLess(plan["stop"], plan["entry"])
        self.assertLess(plan["entry"], plan["tp1"])
        self.assertLess(plan["tp1"], plan["tp2"])
        self.assertLess(plan["tp2"], plan["tp3"])
        self.assertGreater(plan["risk_per_share"], 0)

    def test_same_bar_stop_wins_over_target(self):
        frame = self.make_frame(60)
        signal_index = 39
        history = frame.iloc[: signal_index + 1]
        next_open = float(frame.iloc[signal_index + 1]["open"])
        plan = build_exit_plan(history, "rsi_macd", entry_price=next_open)
        # Make execution bar touch both stop and TP3. Conservative simulator must stop first.
        frame.iloc[signal_index + 1, frame.columns.get_loc("low")] = plan["stop"] - 0.1
        frame.iloc[signal_index + 1, frame.columns.get_loc("high")] = plan["tp3"] + 0.1
        result = simulate_trade(frame, signal_index, "rsi_macd")
        self.assertIsNotNone(result)
        self.assertEqual(result["exit_reason"], "STOP")
        self.assertTrue(result["stop_hit"])
        self.assertFalse(result["tp1_hit"])

    def test_tp1_then_breakeven_can_still_be_net_positive_or_flat_not_fake_full_win(self):
        frame = self.make_frame(65)
        signal_index = 39
        history = frame.iloc[: signal_index + 1]
        entry = float(frame.iloc[signal_index + 1]["open"])
        plan = build_exit_plan(history, "rsi_macd", entry_price=entry)
        # First execution bar reaches only TP1, next bar comes back through break-even.
        frame.iloc[40, frame.columns.get_loc("low")] = min(entry + 0.01, float(frame.iloc[40]["low"]))
        frame.iloc[40, frame.columns.get_loc("high")] = plan["tp1"] + 0.01
        frame.iloc[40, frame.columns.get_loc("close")] = plan["tp1"]
        frame.iloc[41, frame.columns.get_loc("low")] = entry - 0.01
        frame.iloc[41, frame.columns.get_loc("high")] = min(plan["tp2"] - 0.01, entry + 0.10)
        result = simulate_trade(frame, signal_index, "rsi_macd")
        self.assertTrue(result["tp1_hit"])
        self.assertEqual(result["exit_reason"], "BREAKEVEN")
        self.assertGreater(result["realized_r"], 0.0)  # partial TP1 is correctly included
        self.assertLess(result["realized_r"], plan["tp1_r"])  # but it is not counted as a full 1R win

    def test_wilson_penalizes_tiny_samples(self):
        tiny = wilson_lower_bound(5, 5)
        mature = wilson_lower_bound(70, 100)
        self.assertLess(tiny, mature)

    def test_robust_metrics_require_realized_r(self):
        trades = [
            {"realized_r": 1.0, "tp1_hit": True, "tp2_hit": False, "tp3_hit": False, "stop_hit": False, "trailing_exit": False, "mfe_r": 1.2, "mae_r": -0.2},
            {"realized_r": -0.5, "tp1_hit": False, "tp2_hit": False, "tp3_hit": False, "stop_hit": True, "trailing_exit": False, "mfe_r": 0.2, "mae_r": -0.6},
        ]
        metrics = robust_metrics(trades)
        self.assertEqual(metrics["trades"], 2)
        self.assertAlmostEqual(metrics["win_rate"], 50.0)
        self.assertGreater(metrics["expectancy_r"], 0.0)
        self.assertGreater(metrics["profit_factor"], 1.0)


if __name__ == "__main__":
    unittest.main()
