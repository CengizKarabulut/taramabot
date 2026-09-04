from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from decision_panel_v645_signal import decision_v645_series
from exit_engine import build_exit_plan
from strategy_exit_profiles import get_exit_profile


class DecisionPanelV645Tests(unittest.TestCase):
    def make_frame(self, *, last_volume: float = 1_500_000.0, last_close: float = 100.0) -> pd.DataFrame:
        index = pd.date_range("2026-01-01", periods=40, freq="D")
        close = pd.Series(100.0, index=index)
        close.iloc[-1] = last_close
        high = pd.Series(101.0, index=index)
        high.iloc[-1] = max(last_close + 0.5, 101.0)
        volume = pd.Series(1_000_000.0, index=index)
        volume.iloc[-1] = last_volume
        return pd.DataFrame(
            {
                "open": close - 0.25,
                "high": high,
                "low": close - 1.0,
                "close": close,
                "volume": volume,
            },
            index=index,
        )

    @staticmethod
    def fake_base(frame: pd.DataFrame, **_kwargs) -> pd.DataFrame:
        result = pd.DataFrame(index=frame.index)
        result["score"] = 80
        result["adx"] = 30.0
        result["breakout"] = False
        result["pullback"] = False
        result["trend"] = False
        result.loc[result.index[-1], "pullback"] = True
        return result

    def test_validated_pullback_guard_accepts_orderly_near_high_signal(self):
        frame = self.make_frame(last_volume=1_500_000.0, last_close=100.0)
        with patch("decision_panel_v645_signal.decision_series", side_effect=self.fake_base):
            state = decision_v645_series(frame, min_score=75)
        row = state.iloc[-1]
        self.assertLessEqual(float(row["rvol"]), 2.0)
        self.assertGreaterEqual(float(row["pct20"]), 95.0)
        self.assertTrue(bool(row["pullback_guard"]))
        self.assertTrue(bool(row["new_pullback"]))
        self.assertEqual(row["new_setup"], "PULLBACK")
        self.assertTrue(bool(row["entry"]))

    def test_validated_pullback_guard_rejects_volume_climax(self):
        frame = self.make_frame(last_volume=3_000_000.0, last_close=100.0)
        with patch("decision_panel_v645_signal.decision_series", side_effect=self.fake_base):
            state = decision_v645_series(frame, min_score=75)
        row = state.iloc[-1]
        self.assertGreater(float(row["rvol"]), 2.0)
        self.assertFalse(bool(row["pullback_guard"]))
        self.assertFalse(bool(row["new_pullback"]))
        self.assertEqual(row["new_setup"], "")
        self.assertFalse(bool(row["entry"]))

    def test_oos_promoted_pullback_profile_is_locked(self):
        profile = get_exit_profile("decision_panel", setup="PULLBACK")
        self.assertEqual(profile.stop_mode, "hybrid_wide")
        self.assertAlmostEqual(profile.atr_mult, 1.25)
        self.assertAlmostEqual(profile.atr_buffer, 0.25)
        self.assertEqual(profile.ema_period, 21)
        self.assertEqual(profile.swing_lookback, 8)
        self.assertAlmostEqual(profile.min_risk_atr, 0.8)
        self.assertAlmostEqual(profile.max_risk_atr, 2.6)
        self.assertAlmostEqual(profile.tp1_r, 0.8)
        self.assertAlmostEqual(profile.tp2_r, 1.5)
        self.assertAlmostEqual(profile.tp3_r, 2.4)
        self.assertAlmostEqual(profile.trailing_atr_mult, 1.8)

    def test_trend_and_breakout_profiles_remain_unpromoted(self):
        trend = get_exit_profile("decision_panel", setup="TREND DEVAMI")
        breakout = get_exit_profile("decision_panel", setup="BREAKOUT")
        self.assertEqual(trend.stop_mode, "hybrid_tight")
        self.assertAlmostEqual(trend.tp1_r, 1.0)
        self.assertAlmostEqual(trend.tp2_r, 2.0)
        self.assertAlmostEqual(trend.tp3_r, 3.0)
        self.assertEqual(breakout.stop_mode, "hybrid_tight")
        self.assertAlmostEqual(breakout.tp1_r, 1.0)
        self.assertAlmostEqual(breakout.tp2_r, 2.0)
        self.assertAlmostEqual(breakout.tp3_r, 3.2)

    def test_pullback_exit_plan_is_monotonic(self):
        frame = self.make_frame(last_volume=1_500_000.0)
        # Add enough non-flat movement for a normal ATR path.
        ramp = np.linspace(0.0, 8.0, len(frame))
        frame["close"] += ramp
        frame["open"] = frame["close"] - 0.25
        frame["high"] = frame["close"] + 1.0
        frame["low"] = frame["close"] - 1.0
        plan = build_exit_plan(frame, "decision_panel", setup="PULLBACK")
        self.assertLess(plan["stop"], plan["entry"])
        self.assertLess(plan["entry"], plan["tp1"])
        self.assertLess(plan["tp1"], plan["tp2"])
        self.assertLess(plan["tp2"], plan["tp3"])
        self.assertAlmostEqual(plan["tp1_r"], 0.8)
        self.assertAlmostEqual(plan["tp2_r"], 1.5)
        self.assertAlmostEqual(plan["tp3_r"], 2.4)


if __name__ == "__main__":
    unittest.main()
