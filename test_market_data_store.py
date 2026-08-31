import os
import tempfile
import unittest

import pandas as pd

from market_data_store import (
    MarketDataStore,
    resample_bist_intraday,
    resample_calendar,
    resample_daily,
)


class MarketDataStoreTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        os.unlink(self.database_path)

    def tearDown(self):
        if os.path.exists(self.database_path):
            os.unlink(self.database_path)

    @staticmethod
    def sample_intraday():
        index = pd.date_range("2026-08-31 10:00:00", periods=8, freq="15min")
        return pd.DataFrame(
            {
                "open": range(10, 18),
                "high": range(11, 19),
                "low": range(9, 17),
                "close": [10.5 + value for value in range(8)],
                "volume": [100] * 8,
            },
            index=index,
        )

    def test_upsert_replaces_same_candle_and_prunes(self):
        frame = self.sample_intraday()
        with MarketDataStore(self.database_path) as store:
            store.upsert_dataframe("TEST", "BIST", "15m", frame, max_bars=5)
            replacement = frame.tail(1).copy()
            replacement.loc[:, "close"] = 99.0
            store.upsert_dataframe("TEST", "BIST", "15m", replacement, max_bars=5)
            loaded = store.load_dataframe("TEST", "BIST", "15m")

        self.assertEqual(len(loaded), 5)
        self.assertEqual(float(loaded.iloc[-1]["close"]), 99.0)

    def test_intraday_resampling_is_anchored_to_1000(self):
        result = resample_bist_intraday(self.sample_intraday(), "1H")
        self.assertEqual(list(result.index.strftime("%H:%M")), ["10:00", "11:00"])
        self.assertEqual(float(result.iloc[0]["open"]), 10.0)
        self.assertEqual(float(result.iloc[0]["close"]), 13.5)
        self.assertEqual(float(result.iloc[0]["volume"]), 400.0)

    def test_daily_weekly_and_monthly_resampling(self):
        daily = resample_daily(self.sample_intraday())
        self.assertEqual(len(daily), 1)

        history_index = pd.date_range("2026-08-24", periods=9, freq="D")
        history = pd.DataFrame(
            {
                "open": range(9),
                "high": range(1, 10),
                "low": range(9),
                "close": range(1, 10),
                "volume": [10] * 9,
            },
            index=history_index,
        )
        weekly = resample_calendar(history, "1W")
        monthly = resample_calendar(history, "1M")
        self.assertEqual(len(weekly), 2)
        self.assertEqual(len(monthly), 2)

    def test_read_only_store(self):
        with MarketDataStore(self.database_path) as store:
            store.upsert_dataframe("TEST", "BIST", "15m", self.sample_intraday())

        with MarketDataStore(self.database_path, read_only=True) as store:
            loaded = store.load_dataframe("TEST", "BIST", "15m", limit=3)
            self.assertEqual(len(loaded), 3)
            self.assertTrue(store.integrity_ok())


if __name__ == "__main__":
    unittest.main()
