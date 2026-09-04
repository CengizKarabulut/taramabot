"""OOS cost sensitivity for the nine long-history scanner exit candidates.

Research-only.  Replays exactly one already-selected exit profile per strategy
on the untouched OOS period, verifies that gross metrics reproduce the
walk-forward artifact, then applies explicit round-trip commission/slippage
stress scenarios.  No parameters are selected from OOS.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from historical_replay import signal_indexes
from market_data_store import MarketDataStore
from strategy_exit_profiles import ExitProfile
from strategy_optimizer import robust_metrics
from trade_backtester import simulate_trade


PROFILES: dict[str, ExitProfile] = {
    "smi_macd": ExitProfile(
        key="smi_macd", family="early_reversal", stop_mode="swing",
        atr_mult=1.10, atr_buffer=0.25, ema_period=21, swing_lookback=6,
        min_risk_atr=0.75, max_risk_atr=2.25,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.35, 0.30, 0.20), breakeven_after_tp1=True,
        trailing_after_tp2=True, trailing_atr_mult=1.8, max_hold_bars=80,
    ),
    "smi_macd_full": ExitProfile(
        key="smi_macd_full", family="trend_reversal", stop_mode="swing",
        atr_mult=1.25, atr_buffer=0.25, ema_period=21, swing_lookback=7,
        min_risk_atr=0.80, max_risk_atr=2.50,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), breakeven_after_tp1=True,
        trailing_after_tp2=True, trailing_atr_mult=2.2, max_hold_bars=80,
    ),
    "rsi_macd": ExitProfile(
        key="rsi_macd", family="confirmed_momentum", stop_mode="swing",
        atr_mult=1.10, atr_buffer=0.20, ema_period=13, swing_lookback=6,
        min_risk_atr=0.70, max_risk_atr=2.20,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), breakeven_after_tp1=True,
        trailing_after_tp2=True, trailing_atr_mult=1.9, max_hold_bars=80,
    ),
    "i9": ExitProfile(
        key="i9", family="confirmed_momentum", stop_mode="hybrid_wide",
        atr_mult=1.20, atr_buffer=0.20, ema_period=21, swing_lookback=7,
        min_risk_atr=0.75, max_risk_atr=2.40,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), breakeven_after_tp1=True,
        trailing_after_tp2=True, trailing_atr_mult=2.2, max_hold_bars=80,
    ),
    "rsi": ExitProfile(
        key="rsi", family="fast_momentum", stop_mode="hybrid_wide",
        atr_mult=0.95, atr_buffer=0.15, ema_period=21, swing_lookback=4,
        min_risk_atr=0.65, max_risk_atr=1.90,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.35, 0.30, 0.20), breakeven_after_tp1=True,
        trailing_after_tp2=True, trailing_atr_mult=1.5, max_hold_bars=55,
    ),
    "h8": ExitProfile(
        key="h8", family="momentum_continuation", stop_mode="swing",
        atr_mult=1.10, atr_buffer=0.20, ema_period=13, swing_lookback=6,
        min_risk_atr=0.70, max_risk_atr=2.20,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), breakeven_after_tp1=True,
        trailing_after_tp2=True, trailing_atr_mult=1.7, max_hold_bars=80,
    ),
    "macd_cross": ExitProfile(
        key="macd_cross", family="trend_momentum", stop_mode="hybrid_wide",
        atr_mult=1.15, atr_buffer=0.20, ema_period=21, swing_lookback=7,
        min_risk_atr=0.75, max_risk_atr=2.35,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), breakeven_after_tp1=True,
        trailing_after_tp2=True, trailing_atr_mult=1.7, max_hold_bars=80,
    ),
    "ema": ExitProfile(
        key="ema", family="trend", stop_mode="swing",
        atr_mult=1.25, atr_buffer=0.20, ema_period=21, swing_lookback=8,
        min_risk_atr=0.80, max_risk_atr=2.50,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.25, 0.30, 0.20), breakeven_after_tp1=True,
        trailing_after_tp2=True, trailing_atr_mult=1.9, max_hold_bars=80,
    ),
    "new_scan": ExitProfile(
        key="new_scan", family="trend", stop_mode="swing",
        atr_mult=1.25, atr_buffer=0.20, ema_period=21, swing_lookback=8,
        min_risk_atr=0.80, max_risk_atr=2.60,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), breakeven_after_tp1=True,
        trailing_after_tp2=True, trailing_atr_mult=2.2, max_hold_bars=80,
    ),
}

VALIDATION_CUT = {
    "smi_macd": "2024-12-27T06:00:00",
    "smi_macd_full": "2025-03-05T06:00:00",
    "rsi_macd": "2025-02-20T06:00:00",
    "i9": "2025-01-29T06:00:00",
    "rsi": "2025-01-28T06:00:00",
    "h8": "2025-02-14T06:00:00",
    "macd_cross": "2025-01-09T06:00:00",
    "ema": "2025-01-21T06:00:00",
    "new_scan": "2025-01-15T06:00:00",
}

EXPECTED_OOS_TRADES = {
    "smi_macd": 54,
    "smi_macd_full": 208,
    "rsi_macd": 1155,
    "i9": 1838,
    "rsi": 1756,
    "h8": 2073,
    "macd_cross": 2234,
    "ema": 3587,
    "new_scan": 3316,
}

SCENARIOS = (
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

    gross_weighted_exit = entry + gross_r * risk
    commission = max(float(commission_bps), 0.0) / 10000.0
    slippage = max(float(slippage_bps), 0.0) / 10000.0

    buy_fill = entry * (1.0 + slippage)
    sell_fill = gross_weighted_exit * (1.0 - slippage)
    net_pnl = sell_fill * (1.0 - commission) - buy_fill * (1.0 + commission)
    return net_pnl / risk


def _cost_metrics(trades: list[dict[str, Any]], commission_bps: float, slippage_bps: float) -> dict[str, Any]:
    adjusted: list[dict[str, Any]] = []
    for trade in trades:
        item = dict(trade)
        item["realized_r"] = _net_r(trade, commission_bps, slippage_bps)
        item["winner"] = item["realized_r"] > 0
        adjusted.append(item)
    return robust_metrics(adjusted)


def replay_oos(database: str, strategy: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if strategy not in PROFILES:
        raise ValueError(f"Bilinmeyen strateji: {strategy}")
    profile = PROFILES[strategy]
    cut = pd.Timestamp(VALIDATION_CUT[strategy])
    trades: list[dict[str, Any]] = []
    symbols_seen = 0

    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", "1D")
        for number, symbol in enumerate(symbols, start=1):
            frame = store.load_dataframe(symbol, "BIST", "1D", limit=0)
            if frame is None or frame.empty:
                continue
            indexes = signal_indexes(frame, strategy, warmup=240, fresh_only=True)
            last_exit_position = -1
            index_lookup = {pd.Timestamp(value): pos for pos, value in enumerate(frame.index)}
            symbol_count = 0
            for signal_pos in indexes:
                signal_time = pd.Timestamp(frame.index[signal_pos])
                if signal_time <= cut or signal_pos <= last_exit_position:
                    continue
                trade = simulate_trade(frame, signal_pos, strategy, profile=profile)
                if trade is None:
                    continue
                entry_time = pd.Timestamp(trade["entry_time"])
                if entry_time <= cut:
                    continue
                trade = dict(trade)
                trade.update({"symbol": symbol, "signal_time": signal_time.isoformat()})
                trades.append(trade)
                symbol_count += 1
                exit_pos = index_lookup.get(pd.Timestamp(trade["exit_time"]))
                if exit_pos is not None:
                    last_exit_position = exit_pos
            if symbol_count:
                symbols_seen += 1
            if number % 50 == 0 or number == len(symbols):
                print(f"[{number}/{len(symbols)}] {strategy}: {len(trades)} OOS işlem")

    gross = robust_metrics(trades)
    expected = EXPECTED_OOS_TRADES[strategy]
    parity = int(gross.get("trades", 0)) == int(expected)
    return trades, {
        "strategy": strategy,
        "profile": asdict(profile),
        "validation_cut": VALIDATION_CUT[strategy],
        "symbols_with_oos_trades": symbols_seen,
        "expected_oos_trades": expected,
        "replayed_oos_trades": gross.get("trades", 0),
        "gross_trade_count_parity": parity,
        "gross": gross,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--strategy", choices=tuple(PROFILES), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    trades, payload = replay_oos(args.db, args.strategy)
    payload["scenarios"] = {}
    for name, commission_bps, slippage_bps in SCENARIOS:
        payload["scenarios"][name] = {
            "commission_bps_per_side": commission_bps,
            "slippage_bps_per_side": slippage_bps,
            "metrics": _cost_metrics(trades, commission_bps, slippage_bps),
        }

    moderate = payload["scenarios"]["moderate_10_10bps"]["metrics"]
    payload["moderate_cost_pass"] = bool(
        payload["gross_trade_count_parity"]
        and moderate.get("trades", 0) >= 30
        and moderate.get("expectancy_r", 0.0) > 0.0
        and moderate.get("profit_factor", 0.0) > 1.0
    )

    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    if not payload["gross_trade_count_parity"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
