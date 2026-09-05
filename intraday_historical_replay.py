"""Vectorized no-lookahead intraday replay for the nine A-I scanner families.

Signal detection comes from signal_parity.py and is therefore calculated once
per symbol.  Execution remains deliberately conservative in trade_backtester:
next-bar open entry, stop-first same-bar ordering, staged profit taking and ATR
trailing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from market_data_store import MarketDataStore
from signal_parity import STRATEGIES, STRATEGY_TO_CODE, build_signal_frame, fresh_signal_indexes
from strategy_optimizer import robust_metrics
from trade_backtester import simulate_trade


COST_SCENARIOS = (
    ("gross", 0.0, 0.0),
    ("light_5_5bps", 5.0, 5.0),
    ("moderate_10_10bps", 10.0, 10.0),
    ("stress_20_10bps", 20.0, 10.0),
)


def _net_r(trade: dict[str, Any], commission_bps: float, slippage_bps: float) -> float:
    entry = float(trade["entry"])
    risk = float(trade["plan"]["risk_per_share"])
    gross_r = float(trade["realized_r"])
    if not np.isfinite(entry) or not np.isfinite(risk) or risk <= 0:
        return gross_r
    weighted_exit = entry + gross_r * risk
    commission = max(float(commission_bps), 0.0) / 10000.0
    slippage = max(float(slippage_bps), 0.0) / 10000.0
    buy_fill = entry * (1.0 + slippage)
    sell_fill = weighted_exit * (1.0 - slippage)
    net_pnl = sell_fill * (1.0 - commission) - buy_fill * (1.0 + commission)
    return net_pnl / risk


def cost_metrics(trades: list[dict[str, Any]], commission_bps: float, slippage_bps: float) -> dict[str, Any]:
    adjusted = []
    for trade in trades:
        item = dict(trade)
        item["realized_r"] = _net_r(trade, commission_bps, slippage_bps)
        item["winner"] = item["realized_r"] > 0
        adjusted.append(item)
    metrics = robust_metrics(adjusted)
    returns = [float(item.get("return_pct", 0.0)) for item in trades]
    metrics["avg_gross_return_pct"] = float(np.mean(returns)) if returns else 0.0
    metrics["median_gross_return_pct"] = float(np.median(returns)) if returns else 0.0
    metrics["avg_bars_held"] = float(np.mean([float(item.get("bars_held", 0)) for item in trades])) if trades else 0.0
    return metrics


def _time_splits(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"train": robust_metrics([]), "validation": robust_metrics([]), "oos": robust_metrics([]), "cuts": {}}
    ordered = sorted(trades, key=lambda item: pd.Timestamp(item["signal_time"]))
    times = pd.Series([pd.Timestamp(item["signal_time"]) for item in ordered])
    train_cut = times.quantile(0.60, interpolation="nearest")
    validation_cut = times.quantile(0.80, interpolation="nearest")
    groups = {"train": [], "validation": [], "oos": []}
    for trade in ordered:
        stamp = pd.Timestamp(trade["signal_time"])
        if stamp <= train_cut:
            groups["train"].append(trade)
        elif stamp <= validation_cut:
            groups["validation"].append(trade)
        else:
            groups["oos"].append(trade)
    return {
        "cuts": {"train_end": train_cut.isoformat(), "validation_end": validation_cut.isoformat()},
        "train": robust_metrics(groups["train"]),
        "validation": robust_metrics(groups["validation"]),
        "oos": robust_metrics(groups["oos"]),
    }


def replay_database(
    database: str,
    daily_database: str,
    *,
    strategy: str,
    period: str = "15m",
    max_symbols: int = 0,
    warmup: int = 240,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if strategy not in STRATEGIES:
        raise ValueError(f"Bilinmeyen strateji: {strategy}")

    trades: list[dict[str, Any]] = []
    signal_count = 0
    symbols_with_signals = 0
    symbols_with_trades = 0

    with MarketDataStore(database, read_only=True) as store, MarketDataStore(daily_database, read_only=True) as daily_store:
        symbols = store.list_symbols("BIST", period)
        if max_symbols > 0:
            symbols = symbols[:max_symbols]

        for number, symbol in enumerate(symbols, start=1):
            frame = store.load_dataframe(symbol, "BIST", period, limit=0)
            if frame is None or frame.empty or len(frame) <= warmup + 1:
                continue
            daily = daily_store.load_dataframe(symbol, "BIST", "1D", limit=0)
            signal_frame = build_signal_frame(frame, daily=daily, warmup=warmup)
            indexes = fresh_signal_indexes(signal_frame, strategy)
            signal_count += len(indexes)
            if indexes:
                symbols_with_signals += 1

            index_lookup = {pd.Timestamp(value): pos for pos, value in enumerate(frame.index)}
            last_exit_position = -1
            symbol_trades = 0
            for signal_pos in indexes:
                if signal_pos <= last_exit_position:
                    continue
                trade = simulate_trade(frame, signal_pos, strategy)
                if trade is None:
                    continue
                trade = dict(trade)
                trade.update(
                    {
                        "symbol": symbol,
                        "period": period,
                        "code": STRATEGY_TO_CODE[strategy],
                        "signal_time": pd.Timestamp(frame.index[signal_pos]).isoformat(),
                        "signal_index": int(signal_pos),
                    }
                )
                trades.append(trade)
                symbol_trades += 1
                exit_pos = index_lookup.get(pd.Timestamp(trade["exit_time"]))
                if exit_pos is not None:
                    last_exit_position = int(exit_pos)
            if symbol_trades:
                symbols_with_trades += 1
            if number % 50 == 0 or number == len(symbols):
                print(
                    f"[{number}/{len(symbols)}] {STRATEGY_TO_CODE[strategy]} {strategy}: "
                    f"signals={signal_count} trades={len(trades)}"
                )

    gross = cost_metrics(trades, 0.0, 0.0)
    splits = _time_splits(trades)
    scenarios = {}
    for name, commission, slippage in COST_SCENARIOS:
        scenarios[name] = {
            "commission_bps_per_side": commission,
            "slippage_bps_per_side": slippage,
            "metrics": cost_metrics(trades, commission, slippage),
        }

    metrics = {
        "code": STRATEGY_TO_CODE[strategy],
        "strategy": strategy,
        "period": period,
        "warmup": warmup,
        "signal_count": signal_count,
        "symbols_with_signals": symbols_with_signals,
        "symbols_with_trades": symbols_with_trades,
        "baseline_profile_scope": "current production profile; 15m calibration not yet applied",
        "gross": gross,
        "time_splits": splits,
        "cost_scenarios": scenarios,
    }
    return trades, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Intraday historical SQLite archive")
    parser.add_argument("--daily-db", required=True, help="Daily historical SQLite archive")
    parser.add_argument("--strategy", choices=STRATEGIES, required=True)
    parser.add_argument("--period", default="15m")
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--warmup", type=int, default=240)
    parser.add_argument("--output-dir", default="reports/intraday-backtest")
    args = parser.parse_args()

    trades, metrics = replay_database(
        args.db,
        args.daily_db,
        strategy=args.strategy,
        period=args.period,
        max_symbols=args.max_symbols,
        warmup=args.warmup,
    )
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stem = f"{STRATEGY_TO_CODE[args.strategy]}_{args.strategy}_{args.period}"
    (output / f"{stem}_trades.json").write_text(
        json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output / f"{stem}_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
