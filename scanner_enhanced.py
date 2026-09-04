"""Compatibility scanner that preserves legacy signals and enriches candidates with risk plans."""

from __future__ import annotations

from exit_engine import attach_strategy_plans
from scanner_legacy import *  # noqa: F401,F403
from scanner_legacy import MarketScanner as LegacyMarketScanner


class MarketScanner(LegacyMarketScanner):
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
