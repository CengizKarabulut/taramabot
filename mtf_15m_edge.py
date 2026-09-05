"""Test pre-defined 30m/1H confirmation layers on fresh 15m A-I signals.

The 15m setup is confirmed only with higher-timeframe information that was
fully closed by the 15m signal close.  No look-ahead: higher-TF bar start times
are shifted by their duration before as-of alignment.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from market_data_store import MarketDataStore
from signal_parity import CODE_TO_STRATEGY, STRATEGY_TO_CODE, build_signal_frame, fresh_signal_indexes


HORIZONS = (4, 8, 16, 32)
ROUND_TRIP_COST_PCT = 0.20
VARIANTS = (
    "baseline",
    "30_any",
    "1h_any",
    "30_1h_any",
    "30_trend",
    "1h_trend",
    "30_1h_trend",
    "30_same",
    "1h_same",
    "30_same_1h_trend",
)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _context(frame: pd.DataFrame, daily: pd.DataFrame | None, duration_minutes: int):
    signals = build_signal_frame(frame, daily=daily, warmup=240)
    active_cols = list(CODE_TO_STRATEGY.keys())
    context = pd.DataFrame(index=frame.index)
    for code in active_cols:
        context[code] = signals[code].astype(bool)
    context["any"] = context[active_cols].any(axis=1)
    close = pd.to_numeric(frame["close"], errors="coerce")
    ema21 = _ema(close, 21)
    ema55 = _ema(close, 55)
    context["trend"] = (close > ema21) & (ema21 > ema55)
    context.index = pd.DatetimeIndex(context.index) + pd.Timedelta(minutes=duration_minutes)
    return context.sort_index()


def _asof_row(context: pd.DataFrame, when: pd.Timestamp):
    idx = context.index.searchsorted(when, side="right") - 1
    if idx < 0:
        return None
    return context.iloc[int(idx)]


def _event_summary(events: list[dict], horizon: int) -> dict:
    rows = [event for event in events if horizon in event["horizons"]]
    if not rows:
        return {"events": 0}
    returns = np.array([row["horizons"][horizon] for row in rows], dtype=float)
    net = returns - ROUND_TRIP_COST_PCT
    return {
        "events": int(len(rows)),
        "avg_return_pct": float(np.mean(returns)),
        "median_return_pct": float(np.median(returns)),
        "positive_rate_pct": float(np.mean(returns > 0) * 100.0),
        "avg_net_after_20bps_pct": float(np.mean(net)),
        "net_positive_rate_pct": float(np.mean(net > 0) * 100.0),
    }


def analyze(db15: str, db30: str, db1h: str, db1d: str, max_symbols: int = 0) -> dict:
    events: dict[str, dict[str, list[dict]]] = {
        code: {variant: [] for variant in VARIANTS} for code in CODE_TO_STRATEGY
    }

    with (
        MarketDataStore(db15, read_only=True) as s15,
        MarketDataStore(db30, read_only=True) as s30,
        MarketDataStore(db1h, read_only=True) as s1h,
        MarketDataStore(db1d, read_only=True) as sd,
    ):
        symbols = s15.list_symbols("BIST", "15m")
        if max_symbols > 0:
            symbols = symbols[:max_symbols]

        for number, symbol in enumerate(symbols, start=1):
            f15 = s15.load_dataframe(symbol, "BIST", "15m", limit=0)
            f30 = s30.load_dataframe(symbol, "BIST", "30m", limit=0)
            f1h = s1h.load_dataframe(symbol, "BIST", "1H", limit=0)
            daily = sd.load_dataframe(symbol, "BIST", "1D", limit=0)
            if any(frame is None or frame.empty for frame in (f15, f30, f1h)):
                continue
            if len(f15) < 300 or len(f30) < 260 or len(f1h) < 260:
                continue

            sig15 = build_signal_frame(f15, daily=daily, warmup=240)
            ctx30 = _context(f30, daily, 30)
            ctx1h = _context(f1h, daily, 60)

            for code, strategy in CODE_TO_STRATEGY.items():
                for pos in fresh_signal_indexes(sig15, strategy):
                    entry_pos = pos + 1
                    if entry_pos >= len(f15):
                        continue
                    entry = float(f15["open"].iloc[entry_pos])
                    if not np.isfinite(entry) or entry <= 0:
                        continue
                    signal_time = pd.Timestamp(f15.index[pos])
                    signal_close = signal_time + pd.Timedelta(minutes=15)
                    c30 = _asof_row(ctx30, signal_close)
                    c1h = _asof_row(ctx1h, signal_close)
                    if c30 is None or c1h is None:
                        continue

                    flags = {
                        "baseline": True,
                        "30_any": bool(c30["any"]),
                        "1h_any": bool(c1h["any"]),
                        "30_1h_any": bool(c30["any"] and c1h["any"]),
                        "30_trend": bool(c30["trend"]),
                        "1h_trend": bool(c1h["trend"]),
                        "30_1h_trend": bool(c30["trend"] and c1h["trend"]),
                        "30_same": bool(c30[code]),
                        "1h_same": bool(c1h[code]),
                        "30_same_1h_trend": bool(c30[code] and c1h["trend"]),
                    }

                    horizon_values = {}
                    for horizon in HORIZONS:
                        end_pos = entry_pos + horizon - 1
                        if end_pos >= len(f15):
                            continue
                        end_close = float(f15["close"].iloc[end_pos])
                        horizon_values[horizon] = (end_close / entry - 1.0) * 100.0
                    if not horizon_values:
                        continue

                    base_event = {
                        "symbol": symbol,
                        "signal_time": signal_time,
                        "horizons": horizon_values,
                    }
                    for variant, allowed in flags.items():
                        if allowed:
                            events[code][variant].append(base_event)

            if number % 50 == 0 or number == len(symbols):
                count = sum(len(events[code]["baseline"]) for code in events)
                print(f"[{number}/{len(symbols)}] baseline_events={count}")

    output = {
        "period": "15m",
        "confirmation": ["30m", "1H"],
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "horizons_bars": list(HORIZONS),
        "variants": list(VARIANTS),
        "strategies": {},
    }

    for code, strategy in CODE_TO_STRATEGY.items():
        strategy_result = {"strategy": strategy, "variants": {}}
        for variant in VARIANTS:
            rows = sorted(events[code][variant], key=lambda item: item["signal_time"])
            cut = int(len(rows) * 0.80)
            if len(rows) > 1:
                cut = min(max(cut, 1), len(rows) - 1)
            else:
                cut = 0
            oos = rows[cut:] if cut else rows
            oos_summary = {str(h): _event_summary(oos, h) for h in HORIZONS}
            positive_horizons = [
                h for h in HORIZONS
                if oos_summary[str(h)].get("events", 0) >= 50
                and oos_summary[str(h)].get("avg_net_after_20bps_pct", -999) > 0
            ]
            strategy_result["variants"][variant] = {
                "events": len(rows),
                "oos_events": len(oos),
                "oos": oos_summary,
                "positive_oos_horizons_after_cost": positive_horizons,
                "robust_candidate": len(positive_horizons) >= 2,
            }
        output["strategies"][code] = strategy_result

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db15", required=True)
    parser.add_argument("--db30", required=True)
    parser.add_argument("--db1h", required=True)
    parser.add_argument("--db1d", required=True)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--output", default="reports/15m-mtf-edge.json")
    args = parser.parse_args()
    payload = analyze(args.db15, args.db30, args.db1h, args.db1d, args.max_symbols)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
