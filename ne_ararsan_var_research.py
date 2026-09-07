"""Research-only backtest for NE ARARSAN VAR v1 across one BIST timeframe.

Signal is frozen before results are inspected:
- close > EMA5 > EMA8 > EMA13
- 30 < RSI14 < 60
- -100 < CCI20 < 100
- MACD(12,26,9) fresh bullish cross
- StochRSI(14,14,3,3) K > D
- RVOL20 > 1.50
- entry at next bar open

The final chronological 20% of signal events is withheld from this stage.
Only pre-holdout raw edge and baseline exit metrics are written.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from market_data_store import MarketDataStore


HORIZONS = (1, 3, 5, 10, 20)
WARMUP = 120
HOLDOUT_FRACTION = 0.20

BASELINE = {
    "swing_lookback": 7,
    "atr_buffer": 0.20,
    "min_risk_atr": 0.75,
    "max_risk_atr": 2.40,
    "tp1_r": 0.80,
    "tp2_r": 1.50,
    "tp3_r": 2.40,
    "tp_allocations": (0.30, 0.30, 0.20),
    "breakeven_after_tp1": True,
    "trailing_after_tp2": True,
    "trailing_atr_mult": 2.00,
    "max_hold_bars": 80,
}

COST_SCENARIOS = {
    "gross": (0.0, 0.0),
    "light_5_5bps": (5.0, 5.0),
    "moderate_10_10bps": (10.0, 10.0),
    "stress_20_10bps": (20.0, 10.0),
}


def _ema(series: pd.Series, span: int) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    close = pd.to_numeric(series, errors="coerce")
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _cci(frame: pd.DataFrame, length: int = 20) -> pd.Series:
    tp = (
        pd.to_numeric(frame["high"], errors="coerce")
        + pd.to_numeric(frame["low"], errors="coerce")
        + pd.to_numeric(frame["close"], errors="coerce")
    ) / 3.0
    mean = tp.rolling(length, min_periods=length).mean()
    mean_dev = tp.rolling(length, min_periods=length).apply(
        lambda values: float(np.mean(np.abs(values - np.mean(values)))),
        raw=True,
    )
    return (tp - mean) / (0.015 * mean_dev.replace(0.0, np.nan))


def _atr(frame: pd.DataFrame, length: int = 14) -> pd.Series:
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def build_indicator_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    volume = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)

    out["ema5"] = _ema(close, 5)
    out["ema8"] = _ema(close, 8)
    out["ema13"] = _ema(close, 13)
    out["rsi14"] = _rsi(close, 14)
    out["cci20"] = _cci(out, 20)

    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    out["macd"] = ema12 - ema26
    out["macd_signal"] = _ema(out["macd"], 9)

    rsi_low = out["rsi14"].rolling(14, min_periods=14).min()
    rsi_high = out["rsi14"].rolling(14, min_periods=14).max()
    stoch_rsi = 100.0 * (out["rsi14"] - rsi_low) / (rsi_high - rsi_low).replace(0.0, np.nan)
    out["stoch_k"] = stoch_rsi.rolling(3, min_periods=3).mean()
    out["stoch_d"] = out["stoch_k"].rolling(3, min_periods=3).mean()

    out["rvol20"] = volume / volume.rolling(20, min_periods=20).mean().replace(0.0, np.nan)
    out["atr14"] = _atr(out, 14)

    cross = (
        (out["macd"].shift(1) <= out["macd_signal"].shift(1))
        & (out["macd"] > out["macd_signal"])
    )
    out["signal"] = (
        (close > out["ema5"])
        & (out["ema5"] > out["ema8"])
        & (out["ema8"] > out["ema13"])
        & (out["rsi14"] > 30.0)
        & (out["rsi14"] < 60.0)
        & (out["cci20"] > -100.0)
        & (out["cci20"] < 100.0)
        & cross
        & (out["stoch_k"] > out["stoch_d"])
        & (out["rvol20"] > 1.50)
    ).fillna(False)
    return out


def _raw_event(symbol: str, data: pd.DataFrame, signal_pos: int) -> dict[str, Any] | None:
    entry_pos = signal_pos + 1
    if entry_pos >= len(data):
        return None
    entry = float(data["open"].iloc[entry_pos])
    if not np.isfinite(entry) or entry <= 0:
        return None
    event: dict[str, Any] = {
        "symbol": symbol,
        "signal_time": pd.Timestamp(data.index[signal_pos]),
        "entry_time": pd.Timestamp(data.index[entry_pos]),
        "entry": entry,
        "horizons": {},
    }
    for horizon in HORIZONS:
        end_pos = entry_pos + horizon - 1
        if end_pos >= len(data):
            continue
        window = data.iloc[entry_pos : end_pos + 1]
        end_close = float(window["close"].iloc[-1])
        high = float(window["high"].max())
        low = float(window["low"].min())
        event["horizons"][horizon] = {
            "end_time": pd.Timestamp(data.index[end_pos]),
            "return_pct": (end_close / entry - 1.0) * 100.0,
            "mfe_pct": (high / entry - 1.0) * 100.0,
            "mae_pct": (low / entry - 1.0) * 100.0,
        }
    return event if event["horizons"] else None


def _initial_stop(data: pd.DataFrame, signal_pos: int, entry: float) -> tuple[float, float]:
    p = BASELINE
    atr_now = float(data["atr14"].iloc[signal_pos])
    if not np.isfinite(atr_now) or atr_now <= 0:
        atr_now = max(entry * 0.02, 0.0001)
    start = max(0, signal_pos - int(p["swing_lookback"]) + 1)
    swing_low = float(pd.to_numeric(data["low"].iloc[start : signal_pos + 1], errors="coerce").min())
    raw_stop = swing_low - atr_now * float(p["atr_buffer"])
    risk = entry - raw_stop
    min_risk = atr_now * float(p["min_risk_atr"])
    max_risk = atr_now * float(p["max_risk_atr"])
    if not np.isfinite(risk) or risk <= 0:
        risk = min_risk
    risk = min(max(risk, min_risk), max_risk)
    return entry - risk, risk


def simulate_trade(
    data: pd.DataFrame,
    signal_pos: int,
    *,
    technical_exit: bool,
) -> dict[str, Any] | None:
    entry_pos = signal_pos + 1
    if entry_pos >= len(data):
        return None
    entry = float(data["open"].iloc[entry_pos])
    if not np.isfinite(entry) or entry <= 0:
        return None

    stop, risk = _initial_stop(data, signal_pos, entry)
    if risk <= 0:
        return None

    p = BASELINE
    tp1 = entry + risk * float(p["tp1_r"])
    tp2 = entry + risk * float(p["tp2_r"])
    tp3 = entry + risk * float(p["tp3_r"])
    allocations = tuple(float(v) for v in p["tp_allocations"])

    initial_stop = stop
    remaining = 1.0
    realized_cash = 0.0
    tp1_hit = tp2_hit = tp3_hit = False
    trail_active = False
    highest_high = entry
    max_high = entry
    min_low = entry
    bars_held = 0
    exit_reason = "OPEN"
    exit_pos = entry_pos

    last_pos = min(
        len(data) - 1,
        entry_pos + int(p["max_hold_bars"]) - 1,
    )

    for pos in range(entry_pos, last_pos + 1):
        row = data.iloc[pos]
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        bars_held += 1
        highest_high = max(highest_high, high)
        max_high = max(max_high, high)
        min_low = min(min_low, low)

        # Conservative ordering: a stop active at bar open wins over targets.
        if low <= stop:
            realized_cash += remaining * stop
            if stop >= entry:
                exit_reason = "TRAIL" if trail_active and stop > entry else "BREAKEVEN"
            else:
                exit_reason = "STOP"
            remaining = 0.0
            exit_pos = pos
            break

        if not tp1_hit and high >= tp1:
            allocation = min(remaining, allocations[0])
            realized_cash += allocation * tp1
            remaining -= allocation
            tp1_hit = True
            if p["breakeven_after_tp1"]:
                stop = max(stop, entry)

        if remaining > 1e-12 and not tp2_hit and high >= tp2:
            allocation = min(remaining, allocations[1])
            realized_cash += allocation * tp2
            remaining -= allocation
            tp2_hit = True
            trail_active = bool(p["trailing_after_tp2"])

        if remaining > 1e-12 and not tp3_hit and high >= tp3:
            allocation = min(remaining, allocations[2])
            realized_cash += allocation * tp3
            remaining -= allocation
            tp3_hit = True

        if technical_exit and remaining > 1e-12:
            macd = float(row["macd"])
            macd_signal = float(row["macd_signal"])
            ema13 = float(row["ema13"])
            if (
                np.isfinite(macd)
                and np.isfinite(macd_signal)
                and np.isfinite(ema13)
                and close < ema13
                and macd < macd_signal
            ):
                realized_cash += remaining * close
                remaining = 0.0
                exit_reason = "TECHNICAL"
                exit_pos = pos
                break

        # Newly tightened trailing stop is only active from the next bar.
        if trail_active and remaining > 1e-12:
            atr_now = float(row["atr14"])
            if np.isfinite(atr_now) and atr_now > 0:
                candidate = highest_high - atr_now * float(p["trailing_atr_mult"])
                stop = max(stop, candidate)

        if pos == last_pos and remaining > 1e-12:
            realized_cash += remaining * close
            remaining = 0.0
            exit_reason = "TIME"
            exit_pos = pos
            break

    if remaining > 1e-12:
        close = float(data["close"].iloc[last_pos])
        realized_cash += remaining * close
        remaining = 0.0
        exit_reason = "TIME"
        exit_pos = last_pos

    pnl = realized_cash - entry
    realized_r = pnl / risk
    return {
        "signal_time": pd.Timestamp(data.index[signal_pos]),
        "entry_time": pd.Timestamp(data.index[entry_pos]),
        "exit_time": pd.Timestamp(data.index[exit_pos]),
        "entry": entry,
        "risk": risk,
        "initial_stop": initial_stop,
        "final_stop": stop,
        "tp1_hit": tp1_hit,
        "tp2_hit": tp2_hit,
        "tp3_hit": tp3_hit,
        "bars_held": bars_held,
        "mfe_r": (max_high - entry) / risk,
        "mae_r": (min_low - entry) / risk,
        "realized_r": realized_r,
        "return_pct": pnl / entry * 100.0,
        "exit_reason": exit_reason,
        "exit_pos": exit_pos,
    }


def _net_r(trade: dict[str, Any], commission_bps: float, slippage_bps: float) -> float:
    entry = float(trade["entry"])
    risk = float(trade["risk"])
    gross_r = float(trade["realized_r"])
    weighted_exit = entry + gross_r * risk
    commission = max(float(commission_bps), 0.0) / 10000.0
    slippage = max(float(slippage_bps), 0.0) / 10000.0
    buy_fill = entry * (1.0 + slippage)
    sell_fill = weighted_exit * (1.0 - slippage)
    net_pnl = sell_fill * (1.0 - commission) - buy_fill * (1.0 + commission)
    return net_pnl / risk


def _trade_metrics(trades: list[dict[str, Any]], commission_bps: float = 0.0, slippage_bps: float = 0.0) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    r = np.array([_net_r(t, commission_bps, slippage_bps) for t in trades], dtype=float)
    gross_profit = float(r[r > 0].sum())
    gross_loss = float(-r[r < 0].sum())
    reasons = Counter(str(t["exit_reason"]) for t in trades)
    cumulative = np.cumsum(r)
    peaks = np.maximum.accumulate(np.concatenate(([0.0], cumulative)))[:-1]
    drawdowns = cumulative - peaks
    return {
        "trades": len(trades),
        "wins": int((r > 0).sum()),
        "win_rate_pct": float((r > 0).mean() * 100.0),
        "expectancy_r": float(r.mean()),
        "median_r": float(np.median(r)),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "avg_return_pct_gross": float(np.mean([t["return_pct"] for t in trades])),
        "tp1_rate_pct": float(np.mean([bool(t["tp1_hit"]) for t in trades]) * 100.0),
        "tp2_rate_pct": float(np.mean([bool(t["tp2_hit"]) for t in trades]) * 100.0),
        "tp3_rate_pct": float(np.mean([bool(t["tp3_hit"]) for t in trades]) * 100.0),
        "avg_mfe_r": float(np.mean([t["mfe_r"] for t in trades])),
        "avg_mae_r": float(np.mean([t["mae_r"] for t in trades])),
        "median_bars_held": float(np.median([t["bars_held"] for t in trades])),
        "max_trade_sequence_drawdown_r": float(drawdowns.min()) if len(drawdowns) else 0.0,
        "exit_reasons": dict(sorted(reasons.items())),
    }


def _raw_summary(events: list[dict[str, Any]], holdout_start: pd.Timestamp) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for horizon in HORIZONS:
        rows = [
            e["horizons"][horizon]
            for e in events
            if horizon in e["horizons"] and e["horizons"][horizon]["end_time"] < holdout_start
        ]
        if not rows:
            output[str(horizon)] = {"events": 0}
            continue
        returns = np.array([row["return_pct"] for row in rows], dtype=float)
        mfes = np.array([row["mfe_pct"] for row in rows], dtype=float)
        maes = np.array([row["mae_pct"] for row in rows], dtype=float)
        net = returns - 0.20
        output[str(horizon)] = {
            "events": len(rows),
            "avg_return_pct": float(returns.mean()),
            "median_return_pct": float(np.median(returns)),
            "positive_rate_pct": float((returns > 0).mean() * 100.0),
            "avg_net_after_20bps_pct": float(net.mean()),
            "net_positive_rate_pct": float((net > 0).mean() * 100.0),
            "avg_mfe_pct": float(mfes.mean()),
            "avg_mae_pct": float(maes.mean()),
            "mfe_mae_ratio": float(mfes.mean() / abs(maes.mean())) if maes.mean() != 0 else None,
        }
    return output


def analyze(database: str, period: str, max_symbols: int = 0) -> dict[str, Any]:
    raw_events: list[dict[str, Any]] = []
    frames_with_signals: list[tuple[str, pd.DataFrame, list[int]]] = []
    symbols_total = 0
    symbols_usable = 0
    earliest = None
    latest = None

    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", period)
        if max_symbols > 0:
            symbols = symbols[:max_symbols]
        symbols_total = len(symbols)

        for number, symbol in enumerate(symbols, start=1):
            frame = store.load_dataframe(symbol, "BIST", period, limit=0)
            if frame is None or len(frame) <= WARMUP + max(HORIZONS) + 2:
                continue
            data = build_indicator_frame(frame)
            symbols_usable += 1
            first_time = pd.Timestamp(data.index[0])
            last_time = pd.Timestamp(data.index[-1])
            earliest = first_time if earliest is None or first_time < earliest else earliest
            latest = last_time if latest is None or last_time > latest else latest

            signal_positions = [
                int(pos)
                for pos in np.flatnonzero(data["signal"].to_numpy(dtype=bool))
                if pos >= WARMUP
            ]
            if signal_positions:
                frames_with_signals.append((symbol, data, signal_positions))
                for signal_pos in signal_positions:
                    event = _raw_event(symbol, data, signal_pos)
                    if event is not None:
                        raw_events.append(event)

            if number % 50 == 0 or number == len(symbols):
                print(
                    f"[{period}] {number}/{len(symbols)} symbol; "
                    f"signal events={len(raw_events)}",
                    flush=True,
                )

    if len(raw_events) < 5:
        raise RuntimeError(f"{period}: insufficient signals ({len(raw_events)})")

    raw_events.sort(key=lambda e: e["signal_time"])
    cut_index = max(1, min(len(raw_events) - 1, int(len(raw_events) * (1.0 - HOLDOUT_FRACTION))))
    holdout_start = pd.Timestamp(raw_events[cut_index]["signal_time"])

    raw_dev = [e for e in raw_events if e["signal_time"] < holdout_start]
    raw_holdout_count = len(raw_events) - len(raw_dev)

    variants: dict[str, list[dict[str, Any]]] = {
        "with_technical_exit": [],
        "without_technical_exit": [],
    }

    for symbol, data, signal_positions in frames_with_signals:
        for variant_name, technical in (
            ("with_technical_exit", True),
            ("without_technical_exit", False),
        ):
            last_exit_pos = -1
            for signal_pos in signal_positions:
                signal_time = pd.Timestamp(data.index[signal_pos])
                if signal_time >= holdout_start:
                    break
                if signal_pos <= last_exit_pos:
                    continue
                trade = simulate_trade(data, signal_pos, technical_exit=technical)
                if trade is None:
                    continue
                # Do not let development metrics consume any holdout candles.
                if pd.Timestamp(trade["exit_time"]) >= holdout_start:
                    continue
                trade = dict(trade)
                trade["symbol"] = symbol
                variants[variant_name].append(trade)
                last_exit_pos = int(trade["exit_pos"])

    variant_payload: dict[str, Any] = {}
    for name, trades in variants.items():
        trades.sort(key=lambda t: t["entry_time"])
        scenario_metrics = {}
        for scenario, (commission_bps, slippage_bps) in COST_SCENARIOS.items():
            scenario_metrics[scenario] = {
                "commission_bps_per_side": commission_bps,
                "slippage_bps_per_side": slippage_bps,
                "metrics": _trade_metrics(trades, commission_bps, slippage_bps),
            }
        variant_payload[name] = {
            "trades": len(trades),
            "symbols_with_trades": len({t["symbol"] for t in trades}),
            "scenarios": scenario_metrics,
        }

    return {
        "version": "ne-ararsan-var-v1-preholdout",
        "period": period,
        "signal_rules": {
            "trend": "close > EMA5 > EMA8 > EMA13",
            "rsi14": "30 < RSI14 < 60",
            "cci20": "-100 < CCI20 < 100",
            "macd": "fresh bullish MACD(12,26,9) cross",
            "stoch_rsi": "K > D using 14,14,3,3",
            "rvol20": "> 1.50",
            "entry": "next bar open",
        },
        "baseline_exit": {
            **BASELINE,
            "tp_allocations": list(BASELINE["tp_allocations"]),
            "technical_exit": "close < EMA13 AND MACD < signal",
        },
        "research_guard": {
            "holdout_fraction": HOLDOUT_FRACTION,
            "holdout_start": holdout_start.isoformat(),
            "holdout_signal_events_not_scored": raw_holdout_count,
            "note": "Final chronological 20% is intentionally not scored in this stage.",
        },
        "data": {
            "symbols_total": symbols_total,
            "symbols_usable": symbols_usable,
            "symbols_with_signals": len({e["symbol"] for e in raw_events}),
            "signal_events_total": len(raw_events),
            "signal_events_development": len(raw_dev),
            "earliest_candle": earliest.isoformat() if earliest is not None else None,
            "latest_candle": latest.isoformat() if latest is not None else None,
        },
        "raw_forward_edge_development": _raw_summary(raw_dev, holdout_start),
        "baseline_exit_development": variant_payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = analyze(args.db, args.period, max_symbols=max(0, args.max_symbols))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
