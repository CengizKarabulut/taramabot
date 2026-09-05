"""Compatibility scanner that preserves legacy signals and enriches candidates with risk plans."""

from __future__ import annotations

import pandas as pd

from exit_engine import attach_strategy_plans
from scanner_legacy import *  # noqa: F401,F403
from scanner_legacy import MarketScanner as LegacyMarketScanner


def _deduplicate_daily_sessions(df, bars=None):
    """Collapse duplicate 1D rows that belong to the same calendar session.

    The cached snapshot can contain both a direct TradingView daily row and an
    intraday-derived daily row for the same BIST date with different timestamps.
    Treating them as two bars distorts indicators and makes the daily limit-up
    filter compare the current close with another row from the same day.
    """
    if df is None or df.empty:
        return df

    clean = df.copy()
    parsed_index = pd.to_datetime(clean.index, errors="coerce")
    valid = ~parsed_index.isna()
    clean = clean.loc[valid].copy()
    parsed_index = parsed_index[valid]
    clean.index = parsed_index
    clean = clean.sort_index()

    session_date = pd.Series(clean.index.normalize(), index=clean.index)
    clean = clean.loc[~session_date.duplicated(keep="last")]

    if bars and int(bars) > 0:
        clean = clean.tail(int(bars))
    return clean


class MarketScanner(LegacyMarketScanner):
    def _get_hist(self, symbol, exchange, period, interval, bars):
        # Ask for extra rows on 1D so deduplication still leaves the requested
        # amount of unique sessions whenever the snapshot contains duplicates.
        period_key = str(period).strip().lower()
        fetch_bars = int(bars) * 2 if period_key == "1d" and bars else bars
        df = super()._get_hist(symbol, exchange, period, interval, fetch_bars)
        if period_key == "1d":
            df = _deduplicate_daily_sessions(df, bars=bars)
        return df

    async def scan_symbol(self, symbol, exchange, interval, period_str, strategies=None):
        result = await super().scan_symbol(symbol, exchange, interval, period_str, strategies)
        if not result:
            return result
        try:
            df = self._get_hist(symbol, exchange, period_str, interval, BARS_TO_FETCH)
            if df is not None and not df.empty:
                result["exit_plans"] = attach_strategy_plans(df, result.get("signals", {}))
        except Exception as exc:
            logger.debug("Exit plan üretilemedi (%s/%s): %s", symbol, period_str, exc)
            result["exit_plans"] = {}
        return result
