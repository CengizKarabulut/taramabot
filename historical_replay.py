"""No-lookahead historical replay for the nine scanner strategies.

Initial scope is daily bars from the SQLite market snapshot.  The engine walks
one bar at a time, evaluates only information available through that bar, and
feeds every accepted fresh signal into the conservative trade simulator.
Intraday replay can be added after the daily engine is validated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from filters import passes_strategy_filter
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
from market_data_store import MarketDataStore
from trade_backtester import simulate_trade, summarize_trades
from strategy_optimizer import robust_metrics


STRATEGIES = (
    "macd_cross", "h8", "i9", "ema", "rsi_macd", "new_scan",
    "smi_macd_full", "smi_macd", "rsi",
)


def raw_signal(frame: pd.DataFrame, strategy: str) -> tuple[bool, str | None]:
    if strategy in {"smi_macd", "smi_macd_full"}:
        result = check_smi_macd_signal(frame)
        if strategy == "smi_macd_full":
            return bool(result.get("full_buy_signal")), "full"
        # Match scanner categorisation: a full signal is reported in S-M-V-2,
        # not duplicated into S-M-2 on the same bar.
        return bool(result.get("smi_macd_buy") and not result.get("full_buy_signal")), "early"
    if strategy == "rsi":
        return bool(check_rsi_signal(frame).get("signal")), None
    if strategy == "new_scan":
        return bool(check_new_scan_signal(frame).get("signal")), None
    if strategy == "rsi_macd":
        return bool(check_rsi_macd_scan_signal(frame).get("signal")), None
    if strategy == "ema":
        return bool(check_ema_scan_signal(frame).get("signal")), None
    if strategy == "macd_cross":
        return bool(check_macd_positive_cross_signal(frame).get("signal")), None
    if strategy == "h8":
        return bool(check_h8_smi_macd_positive_signal(frame).get("signal")), None
    if strategy == "i9":
        return bool(check_i9_smi_macd_positive_full_signal(frame).get("full_signal")), None
    raise ValueError(f"Bilinmeyen strateji: {strategy}")


def filtered_signal(frame: pd.DataFrame, strategy: str) -> bool:
    signal, kind = raw_signal(frame, strategy)
    if not signal:
        return False
    filter_strategy = "smi_macd" if strategy in {"smi_macd", "smi_macd_full"} else strategy
    filter_kind = "full" if strategy == "smi_macd_full" else kind
    return passes_strategy_filter(frame, filter_strategy, signal_kind=filter_kind, daily_df=frame)


def signal_indexes(
    frame: pd.DataFrame,
    strategy: str,
    *,
    warmup: int = 240,
    fresh_only: bool = True,
) -> list[int]:
    indexes: list[int] = []
    previous_active = False
    start = max(35, warmup)
    for pos in range(start, len(frame) - 1):
        history = frame.iloc[: pos + 1]
        try:
            active = filtered_signal(history, strategy)
        except Exception:
            active = False
        if active and (not fresh_only or not previous_active):
            indexes.append(pos)
        previous_active = active
    return indexes


def replay_symbol(
    symbol: str,
    frame: pd.DataFrame,
    strategy: str,
    *,
    period: str = "1D",
    warmup: int = 240,
) -> list[dict[str, Any]]:
    indexes = signal_indexes(frame, strategy, warmup=warmup, fresh_only=True)
    trades: list[dict[str, Any]] = []
    last_exit_position = -1
    index_lookup = {pd.Timestamp(value): pos for pos, value in enumerate(frame.index)}
    for signal_pos in indexes:
        if signal_pos <= last_exit_position:
            continue
        result = simulate_trade(frame, signal_pos, strategy)
        if result is None:
            continue
        result.update({
            "symbol": symbol,
            "period": period,
            "signal_time": pd.Timestamp(frame.index[signal_pos]).isoformat(),
            "signal_index": signal_pos,
        })
        trades.append(result)
        exit_pos = index_lookup.get(pd.Timestamp(result["exit_time"]))
        if exit_pos is not None:
            last_exit_position = exit_pos
    return trades


def replay_database(
    database: str | Path,
    *,
    strategy: str,
    period: str = "1D",
    max_symbols: int = 0,
    warmup: int = 240,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", period)
        if max_symbols > 0:
            symbols = symbols[:max_symbols]
        for number, symbol in enumerate(symbols, start=1):
            frame = store.load_dataframe(symbol, "BIST", period, limit=0)
            if frame is None or frame.empty:
                continue
            try:
                symbol_trades = replay_symbol(symbol, frame, strategy, period=period, warmup=warmup)
                trades.extend(symbol_trades)
                print(f"[{number}/{len(symbols)}] {symbol}: {len(symbol_trades)} işlem")
            except Exception as exc:
                print(f"[{number}/{len(symbols)}] {symbol}: HATA {exc}")
    metrics = robust_metrics(trades)
    metrics.update({"strategy": strategy, "period": period, "symbols": len({trade['symbol'] for trade in trades})})
    return trades, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Taramabot daily historical signal replay")
    parser.add_argument("--db", required=True)
    parser.add_argument("--strategy", choices=STRATEGIES, required=True)
    parser.add_argument("--period", default="1D")
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--warmup", type=int, default=240)
    parser.add_argument("--output-dir", default="reports/backtest")
    args = parser.parse_args()

    trades, metrics = replay_database(
        args.db,
        strategy=args.strategy,
        period=args.period,
        max_symbols=args.max_symbols,
        warmup=args.warmup,
    )
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stem = f"{args.strategy}_{args.period}"
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
