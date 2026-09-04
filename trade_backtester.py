"""Conservative long-trade simulator for scanner exit plans.

Rules intentionally avoid flattering the backtest:
- entry occurs after the signal bar unless the caller explicitly supplies an entry,
- if stop and a target are both touched in one OHLC bar, stop is assumed first,
- TP1 can move the remaining stop to break-even,
- TP2 can activate ATR trailing for the remaining position,
- every result reports MFE/MAE in both percent and R units.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd

from exit_engine import atr, build_exit_plan
from strategy_exit_profiles import ExitProfile


@dataclass
class TradeResult:
    strategy: str
    setup: str
    entry_time: str
    exit_time: str
    entry: float
    initial_stop: float
    final_stop: float
    tp1: float
    tp2: float
    tp3: float
    tp1_hit: bool
    tp2_hit: bool
    tp3_hit: bool
    stop_hit: bool
    trailing_exit: bool
    time_exit: bool
    bars_held: int
    mfe_pct: float
    mae_pct: float
    mfe_r: float
    mae_r: float
    realized_r: float
    return_pct: float
    winner: bool
    exit_reason: str


def _cols(frame: pd.DataFrame) -> dict[str, str]:
    lookup = {str(column).lower(): str(column) for column in frame.columns}
    return {name: lookup[name] for name in ("open", "high", "low", "close", "volume")}


def _time(value: Any) -> str:
    try:
        return pd.Timestamp(value).isoformat()
    except Exception:
        return str(value)


def simulate_trade(
    frame: pd.DataFrame,
    signal_index: int,
    strategy: str,
    *,
    setup: str | None = None,
    profile: ExitProfile | None = None,
    enter_next_bar: bool = True,
) -> dict[str, Any] | None:
    """Simulate one signal with no look-ahead in the initial plan.

    The plan is calculated using history ending at signal_index. With the default
    next-bar convention, the actual entry is the next bar's open while the
    structural plan is recomputed around that known execution price using only
    pre-entry history.
    """
    if frame is None or frame.empty or signal_index < 0 or signal_index >= len(frame):
        return None
    cols = _cols(frame)
    execution_index = signal_index + 1 if enter_next_bar else signal_index
    if execution_index >= len(frame):
        return None

    history = frame.iloc[: signal_index + 1].copy()
    execution_row = frame.iloc[execution_index]
    entry = float(execution_row[cols["open"]]) if enter_next_bar else float(execution_row[cols["close"]])
    if not np.isfinite(entry) or entry <= 0:
        return None

    plan = build_exit_plan(history, strategy, setup=setup, entry_price=entry, profile=profile)
    initial_stop = float(plan["stop"])
    risk = float(plan["risk_per_share"])
    if risk <= 0:
        return None

    stop = initial_stop
    tp1, tp2, tp3 = (float(plan[name]) for name in ("tp1", "tp2", "tp3"))
    allocations = list(plan["tp_allocations"])
    remaining = 1.0
    realized_cash = 0.0
    tp1_hit = tp2_hit = tp3_hit = False
    stop_hit = trailing_exit = time_exit = False
    trail_active = False
    highest_high = entry
    min_low = entry
    max_high = entry
    bars_held = 0
    exit_price = entry
    exit_time = frame.index[execution_index]
    exit_reason = "OPEN"

    atr_series = atr(frame)
    last_index = min(len(frame) - 1, execution_index + int(plan["max_hold_bars"]) - 1)

    for position in range(execution_index, last_index + 1):
        row = frame.iloc[position]
        high = float(row[cols["high"]])
        low = float(row[cols["low"]])
        close = float(row[cols["close"]])
        bars_held += 1
        highest_high = max(highest_high, high)
        max_high = max(max_high, high)
        min_low = min(min_low, low)

        # Conservative intrabar ordering: any active stop wins over same-bar targets.
        if low <= stop:
            exit_price = stop
            realized_cash += remaining * exit_price
            trailing_exit = trail_active and stop > initial_stop
            stop_hit = not trailing_exit
            exit_reason = "TRAIL" if trailing_exit else ("BREAKEVEN" if stop >= entry else "STOP")
            remaining = 0.0
            exit_time = frame.index[position]
            break

        if not tp1_hit and high >= tp1:
            allocation = min(remaining, allocations[0])
            realized_cash += allocation * tp1
            remaining -= allocation
            tp1_hit = True
            if plan["breakeven_after_tp1"]:
                stop = max(stop, entry)

        if remaining > 0 and not tp2_hit and high >= tp2:
            allocation = min(remaining, allocations[1])
            realized_cash += allocation * tp2
            remaining -= allocation
            tp2_hit = True
            trail_active = bool(plan["trailing_after_tp2"])

        if remaining > 0 and not tp3_hit and high >= tp3:
            allocation = min(remaining, allocations[2])
            realized_cash += allocation * tp3
            remaining -= allocation
            tp3_hit = True

        if remaining <= 1e-12:
            remaining = 0.0
            exit_price = tp3 if tp3_hit else tp2 if tp2_hit else tp1
            exit_time = frame.index[position]
            exit_reason = "TP3" if tp3_hit else "TARGETS"
            break

        if trail_active:
            atr_now = float(atr_series.iloc[position]) if pd.notna(atr_series.iloc[position]) else float(plan["atr"])
            if np.isfinite(atr_now) and atr_now > 0:
                trail_candidate = highest_high - atr_now * float(plan["trailing_atr_mult"])
                stop = max(stop, trail_candidate)

        if position == last_index:
            realized_cash += remaining * close
            remaining = 0.0
            exit_price = close
            exit_time = frame.index[position]
            time_exit = True
            exit_reason = "TIME"
            break

    weighted_exit = realized_cash  # position size is normalized to 1.0
    pnl = weighted_exit - entry
    realized_r = pnl / risk
    return_pct = pnl / entry * 100.0
    mfe_pct = (max_high / entry - 1.0) * 100.0
    mae_pct = (min_low / entry - 1.0) * 100.0

    result = TradeResult(
        strategy=strategy,
        setup=setup or "",
        entry_time=_time(frame.index[execution_index]),
        exit_time=_time(exit_time),
        entry=entry,
        initial_stop=initial_stop,
        final_stop=stop,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        tp1_hit=tp1_hit,
        tp2_hit=tp2_hit,
        tp3_hit=tp3_hit,
        stop_hit=stop_hit,
        trailing_exit=trailing_exit,
        time_exit=time_exit,
        bars_held=bars_held,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        mfe_r=(max_high - entry) / risk,
        mae_r=(min_low - entry) / risk,
        realized_r=realized_r,
        return_pct=return_pct,
        winner=realized_r > 0,
        exit_reason=exit_reason,
    )
    payload = asdict(result)
    payload["plan"] = plan
    return payload


def summarize_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [trade for trade in trades if trade is not None]
    if not measured:
        return {
            "trades": 0, "wins": 0, "win_rate": 0.0, "expectancy_r": 0.0,
            "profit_factor": 0.0, "avg_r": 0.0, "tp1_rate": 0.0,
            "tp2_rate": 0.0, "tp3_rate": 0.0, "stop_rate": 0.0,
        }
    r_values = np.array([float(t["realized_r"]) for t in measured], dtype=float)
    gross_profit = float(r_values[r_values > 0].sum())
    gross_loss = float(-r_values[r_values < 0].sum())
    wins = int((r_values > 0).sum())
    count = len(measured)
    return {
        "trades": count,
        "wins": wins,
        "losses": count - wins,
        "win_rate": wins / count * 100.0,
        "expectancy_r": float(r_values.mean()),
        "avg_r": float(r_values.mean()),
        "median_r": float(np.median(r_values)),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        "tp1_rate": sum(bool(t["tp1_hit"]) for t in measured) / count * 100.0,
        "tp2_rate": sum(bool(t["tp2_hit"]) for t in measured) / count * 100.0,
        "tp3_rate": sum(bool(t["tp3_hit"]) for t in measured) / count * 100.0,
        "stop_rate": sum(bool(t["stop_hit"]) for t in measured) / count * 100.0,
        "trail_rate": sum(bool(t["trailing_exit"]) for t in measured) / count * 100.0,
        "avg_mfe_r": float(np.mean([t["mfe_r"] for t in measured])),
        "avg_mae_r": float(np.mean([t["mae_r"] for t in measured])),
    }
