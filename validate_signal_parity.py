"""Compare vectorized A-I states against the production scanner functions.

The validator samples historical 15m bars from the SQLite snapshot.  For every
sampled bar it evaluates the original indicator + filter stack using only the
prefix available at that moment, then compares it with signal_parity.py.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from filters import passes_strategy_filter
from historical_replay import raw_signal
from market_data_store import MarketDataStore
from signal_parity import STRATEGIES, STRATEGY_TO_CODE, build_signal_frame


def _daily_gate_frame(previous_daily_close: float | None, current_close: float) -> pd.DataFrame | None:
    if previous_daily_close is None or not np.isfinite(previous_daily_close) or previous_daily_close <= 0:
        return None
    index = pd.to_datetime(["2000-01-01", "2000-01-02"])
    return pd.DataFrame(
        {
            "open": [previous_daily_close, current_close],
            "high": [previous_daily_close, current_close],
            "low": [previous_daily_close, current_close],
            "close": [previous_daily_close, current_close],
            "volume": [1.0, 1.0],
        },
        index=index,
    )


def legacy_state(frame: pd.DataFrame, strategy: str, previous_close: float | None) -> bool:
    signal, kind = raw_signal(frame, strategy)
    if not signal:
        return False
    filter_strategy = "smi_macd" if strategy in {"smi_macd", "smi_macd_full"} else strategy
    filter_kind = "full" if strategy == "smi_macd_full" else kind
    daily_gate = _daily_gate_frame(previous_close, float(frame["close"].iloc[-1]))
    return bool(
        passes_strategy_filter(
            frame,
            filter_strategy,
            signal_kind=filter_kind,
            daily_df=daily_gate,
        )
    )


def validate(
    database: str,
    *,
    period: str = "15m",
    max_symbols: int = 30,
    samples_per_symbol: int = 8,
    warmup: int = 240,
) -> dict:
    comparisons = 0
    mismatches: list[dict] = []
    symbols_checked = 0

    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", period)
        if max_symbols > 0:
            symbols = symbols[:max_symbols]
        for symbol in symbols:
            frame = store.load_dataframe(symbol, "BIST", period, limit=0)
            daily = store.load_dataframe(symbol, "BIST", "1D", limit=0)
            if frame is None or len(frame) <= warmup + 2:
                continue
            vector = build_signal_frame(frame, daily=daily, warmup=warmup)
            eligible = np.arange(warmup, len(frame) - 1, dtype=int)
            if len(eligible) == 0:
                continue
            if len(eligible) <= samples_per_symbol:
                positions = eligible
            else:
                positions = np.unique(np.linspace(eligible[0], eligible[-1], samples_per_symbol, dtype=int))

            for pos in positions:
                prefix = frame.iloc[: pos + 1]
                prev_daily = vector["previous_daily_close"].iloc[pos]
                previous_close = float(prev_daily) if pd.notna(prev_daily) else None
                for strategy in STRATEGIES:
                    expected = legacy_state(prefix, strategy, previous_close)
                    actual = bool(vector[strategy].iloc[pos])
                    comparisons += 1
                    if expected != actual:
                        mismatches.append(
                            {
                                "symbol": symbol,
                                "period": period,
                                "bar_time": pd.Timestamp(frame.index[pos]).isoformat(),
                                "code": STRATEGY_TO_CODE[strategy],
                                "strategy": strategy,
                                "production": expected,
                                "vector": actual,
                                "previous_daily_close": previous_close,
                                "close": float(prefix["close"].iloc[-1]),
                            }
                        )
            symbols_checked += 1

    return {
        "period": period,
        "symbols_checked": symbols_checked,
        "samples_per_symbol": samples_per_symbol,
        "comparisons": comparisons,
        "mismatch_count": len(mismatches),
        "parity_rate_pct": (100.0 * (comparisons - len(mismatches)) / comparisons) if comparisons else 0.0,
        "passed": comparisons > 0 and not mismatches,
        "mismatches": mismatches[:100],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--period", default="15m")
    parser.add_argument("--max-symbols", type=int, default=30)
    parser.add_argument("--samples-per-symbol", type=int, default=8)
    parser.add_argument("--warmup", type=int, default=240)
    parser.add_argument("--output", default="reports/parity/python_vector_15m.json")
    args = parser.parse_args()

    payload = validate(
        args.db,
        period=args.period,
        max_symbols=args.max_symbols,
        samples_per_symbol=args.samples_per_symbol,
        warmup=args.warmup,
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
