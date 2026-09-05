"""Measure raw A-I forward edge on one timeframe without stop/TP assumptions."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from market_data_store import MarketDataStore, normalize_period
from signal_parity import CODE_TO_STRATEGY, STRATEGY_TO_CODE, build_signal_frame, fresh_signal_indexes


HORIZONS = (1, 2, 4, 8, 16)
ROUND_TRIP_COST_PCT = 0.20
MIN_OOS_EVENTS = 30


def _summary(events: list[dict], horizon: int) -> dict:
    rows = [event for event in events if horizon in event["horizons"]]
    if not rows:
        return {"events": 0}
    returns = np.array([row["horizons"][horizon]["return_pct"] for row in rows], dtype=float)
    mfes = np.array([row["horizons"][horizon]["mfe_pct"] for row in rows], dtype=float)
    maes = np.array([row["horizons"][horizon]["mae_pct"] for row in rows], dtype=float)
    net = returns - ROUND_TRIP_COST_PCT
    return {
        "events": int(len(rows)),
        "avg_return_pct": float(np.mean(returns)),
        "median_return_pct": float(np.median(returns)),
        "positive_rate_pct": float(np.mean(returns > 0) * 100.0),
        "avg_net_after_20bps_pct": float(np.mean(net)),
        "net_positive_rate_pct": float(np.mean(net > 0) * 100.0),
        "avg_mfe_pct": float(np.mean(mfes)),
        "avg_mae_pct": float(np.mean(maes)),
        "mfe_mae_ratio": float(np.mean(mfes) / abs(np.mean(maes))) if np.mean(maes) != 0 else None,
    }


def analyze(period_db: str, daily_db: str, period: str, warmup: int = 240, max_symbols: int = 0) -> dict:
    period = normalize_period(period)
    events_by_strategy: dict[str, list[dict]] = defaultdict(list)

    with MarketDataStore(period_db, read_only=True) as store, MarketDataStore(daily_db, read_only=True) as daily_store:
        symbols = store.list_symbols("BIST", period)
        if max_symbols > 0:
            symbols = symbols[:max_symbols]

        for number, symbol in enumerate(symbols, start=1):
            frame = store.load_dataframe(symbol, "BIST", period, limit=0)
            if frame is None or len(frame) <= warmup + max(HORIZONS) + 2:
                continue
            daily = None
            if period.lower() in {"15m", "30m", "45m", "1h", "2h", "4h"}:
                daily = daily_store.load_dataframe(symbol, "BIST", "1D", limit=0)
            signals = build_signal_frame(frame, daily=daily, warmup=warmup, period=period)

            for strategy in CODE_TO_STRATEGY.values():
                for signal_pos in fresh_signal_indexes(signals, strategy):
                    entry_pos = signal_pos + 1
                    if entry_pos >= len(frame):
                        continue
                    entry = float(frame["open"].iloc[entry_pos])
                    if not np.isfinite(entry) or entry <= 0:
                        continue
                    event = {
                        "symbol": symbol,
                        "signal_time": pd.Timestamp(frame.index[signal_pos]),
                        "horizons": {},
                    }
                    for horizon in HORIZONS:
                        end_pos = entry_pos + horizon - 1
                        if end_pos >= len(frame):
                            continue
                        window = frame.iloc[entry_pos : end_pos + 1]
                        end_close = float(window["close"].iloc[-1])
                        high = float(window["high"].max())
                        low = float(window["low"].min())
                        event["horizons"][horizon] = {
                            "return_pct": (end_close / entry - 1.0) * 100.0,
                            "mfe_pct": (high / entry - 1.0) * 100.0,
                            "mae_pct": (low / entry - 1.0) * 100.0,
                        }
                    if event["horizons"]:
                        events_by_strategy[strategy].append(event)

            if number % 100 == 0 or number == len(symbols):
                print(f"{period} [{number}/{len(symbols)}] events={sum(map(len, events_by_strategy.values()))}")

    result = {
        "period": period,
        "entry_rule": "next bar open",
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "horizons_bars": list(HORIZONS),
        "min_oos_events": MIN_OOS_EVENTS,
        "strategies": {},
    }

    for strategy in CODE_TO_STRATEGY.values():
        events = sorted(events_by_strategy[strategy], key=lambda row: row["signal_time"])
        cut = int(len(events) * 0.80)
        if len(events) > 1:
            cut = min(max(cut, 1), len(events) - 1)
        else:
            cut = 0
        oos = events[cut:] if cut else events
        overall = {str(h): _summary(events, h) for h in HORIZONS}
        oos_summary = {str(h): _summary(oos, h) for h in HORIZONS}
        positive = [
            h for h in HORIZONS
            if oos_summary[str(h)].get("events", 0) >= MIN_OOS_EVENTS
            and oos_summary[str(h)].get("avg_net_after_20bps_pct", -999) > 0
        ]
        robust = len(positive) >= 2
        result["strategies"][STRATEGY_TO_CODE[strategy]] = {
            "strategy": strategy,
            "events": len(events),
            "oos_events": len(oos),
            "overall": overall,
            "oos": oos_summary,
            "positive_oos_horizons_after_cost": positive,
            "raw_edge_detected": robust,
            "sample_limited": len(oos) < MIN_OOS_EVENTS,
        }

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--daily-db", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--warmup", type=int, default=240)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = analyze(args.db, args.daily_db, args.period, args.warmup, args.max_symbols)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
