"""Per-bar trace utility for scanner/Pine parity checks.

This tool does not change production signals. It answers a narrow audit question:
"Which exact candle made a strategy active, and which candle started the fresh
false->true episode used by historical_replay/trade lifecycle?"

For intraday BIST periods the production scanner applies the 9.5% limit-up gate
against the daily move. To keep historical trace causal, this utility builds a
minimal two-row daily limit source from the previous session close and the
current intraday close instead of using the completed end-of-day candle.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from filters import passes_strategy_filter
from historical_replay import STRATEGIES, raw_signal
from indicators import (
    check_ema_scan_signal,
    check_h8_smi_macd_positive_signal,
    check_i9_smi_macd_positive_full_signal,
    check_macd_positive_cross_signal,
    check_new_scan_signal,
    check_rsi_macd_scan_signal,
    check_rsi_signal,
    check_smi_macd_signal,
)
from market_data_store import MarketDataStore, normalize_period


INTRADAY = {"15m", "30m", "45m", "1H", "2H", "4H"}


def _causal_daily_limit_source(history: pd.DataFrame, period: str) -> pd.DataFrame:
    if normalize_period(period) not in INTRADAY:
        return history
    if history is None or history.empty:
        return history

    index = pd.DatetimeIndex(history.index)
    current_date = index[-1].date()
    previous = history[[stamp.date() < current_date for stamp in index]]
    if previous.empty:
        # No prior session is available. Two equal closes make the limit gate neutral.
        current_close = float(history["close"].iloc[-1])
        return pd.DataFrame({"close": [current_close, current_close]})

    previous_close = float(previous["close"].iloc[-1])
    current_close = float(history["close"].iloc[-1])
    return pd.DataFrame({"close": [previous_close, current_close]})


def _details(history: pd.DataFrame, strategy: str) -> dict[str, Any]:
    if strategy in {"smi_macd", "smi_macd_full"}:
        result = check_smi_macd_signal(history)
        return dict(result.get("details") or {})
    if strategy == "h8":
        result = check_h8_smi_macd_positive_signal(history)
        return dict(result.get("details") or {})
    if strategy == "i9":
        result = check_i9_smi_macd_positive_full_signal(history)
        details = dict(result.get("details") or {})
        h8 = dict(details.get("h8_details") or {})
        details.update({f"h8_{key}": value for key, value in h8.items()})
        return details
    if strategy == "rsi":
        return dict(check_rsi_signal(history).get("details") or {})
    if strategy == "new_scan":
        return dict(check_new_scan_signal(history).get("details") or {})
    if strategy == "rsi_macd":
        return dict(check_rsi_macd_scan_signal(history).get("details") or {})
    if strategy == "ema":
        return dict(check_ema_scan_signal(history).get("details") or {})
    if strategy == "macd_cross":
        return dict(check_macd_positive_cross_signal(history).get("details") or {})
    return {}


def trace_strategy(
    frame: pd.DataFrame,
    strategy: str,
    *,
    period: str,
    warmup: int = 35,
    last_bars: int = 0,
) -> list[dict[str, Any]]:
    if strategy not in STRATEGIES:
        raise ValueError(f"Bilinmeyen strateji: {strategy}")
    if frame is None or frame.empty:
        return []

    start = max(2, int(warmup))
    if last_bars > 0:
        start = max(start, len(frame) - int(last_bars))

    output: list[dict[str, Any]] = []
    previous_active = False
    for pos in range(start, len(frame)):
        history = frame.iloc[: pos + 1]
        raw, kind = raw_signal(history, strategy)
        filter_strategy = "smi_macd" if strategy in {"smi_macd", "smi_macd_full"} else strategy
        filter_kind = "full" if strategy == "smi_macd_full" else kind
        daily_source = _causal_daily_limit_source(history, period)
        passed = bool(
            raw
            and passes_strategy_filter(
                history,
                filter_strategy,
                signal_kind=filter_kind,
                daily_df=daily_source,
            )
        )
        fresh = passed and not previous_active
        if passed or fresh:
            details = _details(history, strategy)
            output.append(
                {
                    "bar_index": pos,
                    "bar_time": pd.Timestamp(frame.index[pos]).isoformat(),
                    "close": float(frame["close"].iloc[pos]),
                    "raw": bool(raw),
                    "filter_pass": bool(passed),
                    "fresh_episode_start": bool(fresh),
                    "signal_kind": kind or "",
                    "details": details,
                }
            )
        previous_active = passed
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--strategy", choices=STRATEGIES, required=True)
    parser.add_argument("--warmup", type=int, default=35)
    parser.add_argument("--last-bars", type=int, default=300)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    with MarketDataStore(args.db, read_only=True) as store:
        frame = store.load_dataframe(args.symbol.upper(), "BIST", args.period, limit=0)
    if frame is None or frame.empty:
        raise SystemExit(f"Veri bulunamadi: {args.symbol} {args.period}")

    rows = trace_strategy(
        frame,
        args.strategy,
        period=args.period,
        warmup=args.warmup,
        last_bars=args.last_bars,
    )
    payload = {
        "symbol": args.symbol.upper(),
        "period": normalize_period(args.period),
        "strategy": args.strategy,
        "rows": rows,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
