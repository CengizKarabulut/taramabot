import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from decision_panel_scan import load_snapshot_universe, send_telegram_if_changed
from market_data_store import MarketDataStore


class DecisionPanelSnapshotTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        os.unlink(self.database_path)
        handle, self.state_path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        with open(self.state_path, "w", encoding="utf-8") as stream:
            json.dump({"other_scanner": {"kept": True}}, stream)

    def tearDown(self):
        for path in (self.database_path, self.state_path):
            if os.path.exists(path):
                os.unlink(path)

    @staticmethod
    def daily_history(periods=260):
        index = pd.date_range("2025-09-01", periods=periods, freq="B")
        base = pd.Series(range(periods), index=index, dtype=float)
        return pd.DataFrame(
            {
                "open": 100.0 + base,
                "high": 101.0 + base,
                "low": 99.0 + base,
                "close": 100.5 + base,
                "volume": 1_000_000.0 + base,
            },
            index=index,
        )

    def test_loads_daily_histories_from_snapshot(self):
        with MarketDataStore(self.database_path) as store:
            store.upsert_dataframe("BETA", "BIST", "1D", self.daily_history())
            store.upsert_dataframe("ALFA", "BIST", "1D", self.daily_history())
            store.upsert_dataframe("IGNORE", "BIST", "15m", self.daily_history(20))

        symbols, histories, failures = load_snapshot_universe(self.database_path)

        self.assertEqual(symbols, ["ALFA", "BETA"])
        self.assertEqual(sorted(histories), ["ALFA", "BETA"])
        self.assertEqual(failures, [])
        self.assertEqual(len(histories["ALFA"]), 260)

    @patch("decision_panel_scan.send_telegram_report")
    def test_sends_only_when_candidate_signature_changes(self, telegram_mock):
        now = datetime(2026, 9, 1, 11, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
        live = pd.DataFrame({"symbol": ["BETA", "ALFA"]})
        closed = pd.DataFrame({"symbol": ["GAMA"]})

        first = send_telegram_if_changed(
            report="first",
            now=now,
            live_entries=live,
            closed_entries=closed,
            state_file=self.state_path,
            only_on_change=True,
        )
        second = send_telegram_if_changed(
            report="same",
            now=now,
            live_entries=live.iloc[::-1],
            closed_entries=closed,
            state_file=self.state_path,
            only_on_change=True,
        )
        changed = send_telegram_if_changed(
            report="changed",
            now=now,
            live_entries=pd.DataFrame({"symbol": ["ALFA"]}),
            closed_entries=closed,
            state_file=self.state_path,
            only_on_change=True,
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(changed)
        self.assertEqual(telegram_mock.call_count, 2)
        with open(self.state_path, encoding="utf-8") as stream:
            state = json.load(stream)
        self.assertTrue(state["other_scanner"]["kept"])
        self.assertEqual(state["decision_panel"]["last_signature"]["live_entries"], ["ALFA"])


if __name__ == "__main__":
    unittest.main()

