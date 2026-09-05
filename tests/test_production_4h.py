from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from production_4h import classify_candidate, closed_4h_frame, expected_bucket_close


ISTANBUL = ZoneInfo("Europe/Istanbul")


class Production4HTests(unittest.TestCase):
    def make_frame(self, stamps: list[str]) -> pd.DataFrame:
        index = pd.DatetimeIndex([pd.Timestamp(value, tz=ISTANBUL) for value in stamps])
        return pd.DataFrame(
            {
                "open": range(1, len(index) + 1),
                "high": range(2, len(index) + 2),
                "low": range(0, len(index)),
                "close": range(1, len(index) + 1),
                "volume": [1000] * len(index),
            },
            index=index,
        )

    def test_priority_requires_two_independent_promoted_models_for_a(self):
        self.assertEqual(classify_candidate(True, True)[:2], ("A", "YUKSEK"))
        self.assertEqual(classify_candidate(True, False)[0], "B+")
        self.assertEqual(classify_candidate(False, True)[0], "B")
        self.assertEqual(classify_candidate(False, False)[0], "-")

    def test_1000_bucket_is_not_closed_before_1400(self):
        frame = self.make_frame(["2026-09-04 10:00"])
        now = datetime(2026, 9, 4, 13, 59, tzinfo=ISTANBUL)
        self.assertTrue(closed_4h_frame(frame, now).empty)
        now = datetime(2026, 9, 4, 14, 0, tzinfo=ISTANBUL)
        self.assertEqual(len(closed_4h_frame(frame, now)), 1)

    def test_1400_bucket_closes_at_1800(self):
        frame = self.make_frame(["2026-09-04 10:00", "2026-09-04 14:00"])
        now = datetime(2026, 9, 4, 17, 59, tzinfo=ISTANBUL)
        self.assertEqual(len(closed_4h_frame(frame, now)), 1)
        now = datetime(2026, 9, 4, 18, 0, tzinfo=ISTANBUL)
        self.assertEqual(len(closed_4h_frame(frame, now)), 2)

    def test_closing_bucket_completes_at_1810(self):
        frame = self.make_frame(["2026-09-04 14:00", "2026-09-04 18:00"])
        self.assertEqual(
            expected_bucket_close(frame.index[-1]).strftime("%H:%M"),
            "18:10",
        )
        before = datetime(2026, 9, 4, 18, 9, tzinfo=ISTANBUL)
        after = datetime(2026, 9, 4, 18, 10, tzinfo=ISTANBUL)
        self.assertEqual(len(closed_4h_frame(frame, before)), 1)
        self.assertEqual(len(closed_4h_frame(frame, after)), 2)

    def test_historical_last_bucket_is_not_removed(self):
        frame = self.make_frame(["2026-09-03 18:00"])
        now = datetime(2026, 9, 4, 11, 0, tzinfo=ISTANBUL)
        self.assertEqual(len(closed_4h_frame(frame, now)), 1)


if __name__ == "__main__":
    unittest.main()
