"""Validate that recent live-snapshot timeframes agree with canonical 15m aggregation."""

from __future__ import annotations

import argparse
import json
import math

import pandas as pd

from bist_timeframes import resample_bist_intraday, resample_daily
from market_data_store import MarketDataStore


INTRADAY_PERIODS = ("30m", "45m", "1H", "2H", "4H")
FIELDS = ("open", "high", "low", "close", "volume")


def _close(a, b, rel=1e-7, abs_tol=1e-7):
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return math.isclose(float(a), float(b), rel_tol=rel, abs_tol=abs_tol)


def compare_frames(expected: pd.DataFrame, actual: pd.DataFrame, symbol: str, period: str):
    mismatches = []
    if expected is None or expected.empty:
        return mismatches
    if actual is None or actual.empty:
        return [{"symbol": symbol, "period": period, "error": "actual_empty"}]

    actual = actual.loc[actual.index.intersection(expected.index)]
    for stamp, row in expected.iterrows():
        if stamp not in actual.index:
            mismatches.append({"symbol": symbol, "period": period, "time": stamp.isoformat(), "error": "missing_bar"})
            continue
        got = actual.loc[stamp]
        if isinstance(got, pd.DataFrame):
            got = got.iloc[-1]
        for field in FIELDS:
            if not _close(row[field], got[field], rel=1e-6, abs_tol=1e-5):
                mismatches.append(
                    {
                        "symbol": symbol,
                        "period": period,
                        "time": stamp.isoformat(),
                        "field": field,
                        "expected": float(row[field]) if pd.notna(row[field]) else None,
                        "actual": float(got[field]) if pd.notna(got[field]) else None,
                    }
                )
    return mismatches


def validate(db: str, max_symbols: int = 100) -> dict:
    comparisons = 0
    mismatches = []
    duplicate_daily = []

    with MarketDataStore(db, read_only=True) as store:
        symbols = store.list_symbols("BIST", "15m")
        if max_symbols > 0:
            symbols = symbols[:max_symbols]

        for symbol in symbols:
            frame15 = store.load_dataframe(symbol, "BIST", "15m", limit=100)
            if frame15 is None or frame15.empty:
                continue
            latest_date = frame15.index[-1].date()
            session = frame15[[stamp.date() == latest_date for stamp in frame15.index]]
            if session.empty:
                continue

            for period in INTRADAY_PERIODS:
                expected = resample_bist_intraday(session, period)
                actual = store.load_dataframe(symbol, "BIST", period, limit=30)
                if actual is not None:
                    actual = actual[[stamp.date() == latest_date for stamp in actual.index]]
                comparisons += len(expected) * len(FIELDS)
                mismatches.extend(compare_frames(expected, actual, symbol, period))

            expected_day = resample_daily(session)
            actual_day = store.load_dataframe(symbol, "BIST", "1D", limit=5)
            if actual_day is not None:
                dates = pd.Series([stamp.date() for stamp in actual_day.index])
                if dates.duplicated().any():
                    duplicate_daily.append(symbol)
                actual_day = actual_day[[stamp.date() == latest_date for stamp in actual_day.index]]
            comparisons += len(expected_day) * len(FIELDS)
            mismatches.extend(compare_frames(expected_day, actual_day, symbol, "1D"))

    return {
        "symbols_checked": len(symbols),
        "comparisons": comparisons,
        "mismatch_count": len(mismatches),
        "duplicate_daily_symbols": duplicate_daily[:100],
        "passed": not mismatches and not duplicate_daily,
        "mismatches": mismatches[:200],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--max-symbols", type=int, default=100)
    parser.add_argument("--output", default="data/timeframe-integrity.json")
    args = parser.parse_args()
    result = validate(args.db, max_symbols=args.max_symbols)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
