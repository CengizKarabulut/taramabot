import unittest
import pandas as pd

from production_4h_visual import confirmed_pivots, elliott_context, label_structure


def _frame(values):
    idx = pd.date_range("2026-01-01", periods=len(values), freq="4h", tz="Europe/Istanbul")
    close = pd.Series(values, index=idx, dtype=float)
    return pd.DataFrame({"open": close, "high": close + 0.2, "low": close - 0.2, "close": close, "volume": 1000.0}, index=idx)


class VisualStructureTests(unittest.TestCase):
    def test_confirmed_pivots_do_not_use_unconfirmed_tail(self):
        frame = _frame([1,2,3,2,1,2,4,3,2,5])
        pivots = confirmed_pivots(frame, left=1, right=1)
        self.assertTrue(all(p["i"] < len(frame)-1 for p in pivots))
        self.assertTrue(all(p["confirmed_at"] == frame.index[p["i"]+1] for p in pivots))

    def test_structure_labels_higher_high_and_higher_low(self):
        pivots = [
            {"i":1,"time":"a","kind":"H","price":10,"confirmed_at":"a"},
            {"i":2,"time":"b","kind":"L","price":5,"confirmed_at":"b"},
            {"i":3,"time":"c","kind":"H","price":12,"confirmed_at":"c"},
            {"i":4,"time":"d","kind":"L","price":7,"confirmed_at":"d"},
        ]
        self.assertEqual([p["label"] for p in label_structure(pivots)], ["H","L","HH","HL"])

    def test_elliott_is_context_not_forced_wave_count(self):
        result = elliott_context({"trend":"YUKSELEN YAPI","bos":"YUKARI BOS"})
        self.assertEqual(result["phase"], "ITKI ADAYI")
        self.assertIn("kesin", result["note"].lower())


if __name__ == "__main__":
    unittest.main()
