"""Staged exit research for NE ARARSAN VAR v1 with untouched final holdout.

The signal definition is imported from ne_ararsan_var_research.py and is never
changed here. Exit research is intentionally staged rather than evaluating the
full Cartesian grid:
A) structural stop neighborhood (27 profiles),
B) target/trailing/BE/holding neighborhood (72 profiles),
C) technical-exit on/off on the selected profile.

The first 80% of chronological signal events is development data. Development
is split again into train/validation. The final 20% is evaluated exactly once
after all profile choices are frozen.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from market_data_store import MarketDataStore
from ne_ararsan_var_research import (
    BASELINE,
    COST_SCENARIOS,
    HOLDOUT_FRACTION,
    WARMUP,
    build_indicator_frame,
)


TP_SETS = {
    "A": (0.8, 1.5, 2.4),
    "B": (1.0, 1.8, 2.8),
    "C": (1.0, 2.0, 3.0),
}
STOP_MODES = ("swing", "hybrid_tight", "hybrid_wide")
SWING_LOOKBACKS = (5, 7, 9)
ATR_BUFFERS = (0.15, 0.20, 0.25)
TRAIL_MULTS = (1.7, 2.0, 2.3)
MAX_HOLDS = (20, 40, 60, 80)
TRAIN_FRACTION_OF_DEVELOPMENT = 0.70
MIN_MONTHLY_DEVELOPMENT_SIGNALS = 100


def _profile(**overrides: Any) -> dict[str, Any]:
    profile = {
        "stop_mode": "swing",
        "swing_lookback": int(BASELINE["swing_lookback"]),
        "atr_buffer": float(BASELINE["atr_buffer"]),
        "min_risk_atr": float(BASELINE["min_risk_atr"]),
        "max_risk_atr": float(BASELINE["max_risk_atr"]),
        "fallback_atr_mult": 1.25,
        "tp1_r": float(BASELINE["tp1_r"]),
        "tp2_r": float(BASELINE["tp2_r"]),
        "tp3_r": float(BASELINE["tp3_r"]),
        "tp_allocations": tuple(float(v) for v in BASELINE["tp_allocations"]),
        "breakeven_after_tp1": bool(BASELINE["breakeven_after_tp1"]),
        "trailing_after_tp2": True,
        "trailing_atr_mult": float(BASELINE["trailing_atr_mult"]),
        "max_hold_bars": int(BASELINE["max_hold_bars"]),
        "technical_exit": False,
    }
    profile.update(overrides)
    return profile


def _initial_stop(data: pd.DataFrame, signal_pos: int, entry: float, profile: dict[str, Any]) -> tuple[float, float]:
    atr_now = float(data["atr14"].iloc[signal_pos])
    if not np.isfinite(atr_now) or atr_now <= 0:
        atr_now = max(entry * 0.02, 0.0001)

    lookback = int(profile["swing_lookback"])
    start = max(0, signal_pos - lookback + 1)
    swing_low = float(pd.to_numeric(data["low"].iloc[start : signal_pos + 1], errors="coerce").min())
    buffer = atr_now * float(profile["atr_buffer"])
    swing_stop = swing_low - buffer
    signal_stop = float(data["low"].iloc[signal_pos]) - buffer
    ema_stop = float(data["ema13"].iloc[signal_pos]) - buffer
    fallback = entry - atr_now * float(profile["fallback_atr_mult"])

    candidates = [v for v in (swing_stop, signal_stop, ema_stop) if np.isfinite(v) and v < entry]
    mode = str(profile["stop_mode"])
    if mode == "swing":
        raw_stop = swing_stop if np.isfinite(swing_stop) and swing_stop < entry else fallback
    elif mode == "hybrid_tight":
        raw_stop = max(candidates) if candidates else fallback
    elif mode == "hybrid_wide":
        raw_stop = min(candidates) if candidates else fallback
    else:
        raise ValueError(f"unknown stop mode: {mode}")

    risk = entry - raw_stop
    min_risk = atr_now * float(profile["min_risk_atr"])
    max_risk = atr_now * float(profile["max_risk_atr"])
    if not np.isfinite(risk) or risk <= 0:
        risk = min_risk
    risk = min(max(risk, min_risk), max_risk)
    return entry - risk, risk


def simulate_profile(data: pd.DataFrame, signal_pos: int, profile: dict[str, Any]) -> dict[str, Any] | None:
    entry_pos = signal_pos + 1
    if entry_pos >= len(data):
        return None
    entry = float(data["open"].iloc[entry_pos])
    if not np.isfinite(entry) or entry <= 0:
        return None

    stop, risk = _initial_stop(data, signal_pos, entry, profile)
    if risk <= 0:
        return None
    initial_stop = stop

    tp1 = entry + risk * float(profile["tp1_r"])
    tp2 = entry + risk * float(profile["tp2_r"])
    tp3 = entry + risk * float(profile["tp3_r"])
    allocations = tuple(float(v) for v in profile["tp_allocations"])

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
    last_pos = min(len(data) - 1, entry_pos + int(profile["max_hold_bars"]) - 1)

    for pos in range(entry_pos, last_pos + 1):
        row = data.iloc[pos]
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        bars_held += 1
        highest_high = max(highest_high, high)
        max_high = max(max_high, high)
        min_low = min(min_low, low)

        # Conservative: a stop already active at bar open wins over same-bar targets.
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
            if profile["breakeven_after_tp1"]:
                stop = max(stop, entry)

        if remaining > 1e-12 and not tp2_hit and high >= tp2:
            allocation = min(remaining, allocations[1])
            realized_cash += allocation * tp2
            remaining -= allocation
            tp2_hit = True
            trail_active = bool(profile["trailing_after_tp2"])

        if remaining > 1e-12 and not tp3_hit and high >= tp3:
            allocation = min(remaining, allocations[2])
            realized_cash += allocation * tp3
            remaining -= allocation
            tp3_hit = True

        if bool(profile.get("technical_exit")) and remaining > 1e-12:
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

        # Newly tightened trailing stop becomes active on the next bar.
        if trail_active and remaining > 1e-12:
            atr_now = float(row["atr14"])
            if np.isfinite(atr_now) and atr_now > 0:
                stop = max(stop, highest_high - atr_now * float(profile["trailing_atr_mult"]))

        if pos == last_pos and remaining > 1e-12:
            realized_cash += remaining * close
            remaining = 0.0
            exit_reason = "TIME"
            exit_pos = pos
            break

    pnl = realized_cash - entry
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
        "realized_r": pnl / risk,
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


def metrics(trades: list[dict[str, Any]], commission_bps: float = 10.0, slippage_bps: float = 10.0) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "expectancy_r": 0.0, "profit_factor": 0.0, "win_rate_pct": 0.0}
    r = np.array([_net_r(t, commission_bps, slippage_bps) for t in trades], dtype=float)
    gross_profit = float(r[r > 0].sum())
    gross_loss = float(-r[r < 0].sum())
    reasons = Counter(str(t["exit_reason"]) for t in trades)
    return {
        "trades": len(trades),
        "wins": int((r > 0).sum()),
        "win_rate_pct": float((r > 0).mean() * 100.0),
        "expectancy_r": float(r.mean()),
        "median_r": float(np.median(r)),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "tp1_rate_pct": float(np.mean([bool(t["tp1_hit"]) for t in trades]) * 100.0),
        "tp2_rate_pct": float(np.mean([bool(t["tp2_hit"]) for t in trades]) * 100.0),
        "tp3_rate_pct": float(np.mean([bool(t["tp3_hit"]) for t in trades]) * 100.0),
        "avg_mfe_r": float(np.mean([t["mfe_r"] for t in trades])),
        "avg_mae_r": float(np.mean([t["mae_r"] for t in trades])),
        "median_bars_held": float(np.median([t["bars_held"] for t in trades])),
        "exit_reasons": dict(sorted(reasons.items())),
    }


def _score(m: dict[str, Any], min_trades: int) -> float:
    trades = int(m.get("trades", 0))
    expectancy = float(m.get("expectancy_r", -999.0))
    pf = m.get("profit_factor")
    pf_value = float(pf) if pf is not None and np.isfinite(float(pf)) else 10.0
    if trades < min_trades:
        return -1e9
    # Prefer positive expectancy/PF, but keep a continuous score so weak TFs can
    # still identify the least-bad profile without being mislabeled validated.
    return expectancy + 0.04 * math.log(max(pf_value, 0.05))


def _collect_trades(
    frames: list[tuple[str, pd.DataFrame, list[int]]],
    profile: dict[str, Any],
    *,
    start_time: pd.Timestamp | None,
    end_time: pd.Timestamp | None,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for symbol, data, positions in frames:
        last_exit = -1
        for signal_pos in positions:
            signal_time = pd.Timestamp(data.index[signal_pos])
            if start_time is not None and signal_time < start_time:
                continue
            if end_time is not None and signal_time >= end_time:
                break
            if signal_pos <= last_exit:
                continue
            trade = simulate_profile(data, signal_pos, profile)
            if trade is None:
                continue
            exit_time = pd.Timestamp(trade["exit_time"])
            # Boundary purity: a trade must fully resolve inside its split.
            if end_time is not None and exit_time >= end_time:
                continue
            trade = dict(trade)
            trade["symbol"] = symbol
            trades.append(trade)
            last_exit = int(trade["exit_pos"])
    trades.sort(key=lambda t: t["entry_time"])
    return trades


def _evaluate_profiles(
    frames: list[tuple[str, pd.DataFrame, list[int]]],
    profiles: list[dict[str, Any]],
    *,
    start_time: pd.Timestamp | None,
    end_time: pd.Timestamp | None,
    min_trades: int,
) -> list[dict[str, Any]]:
    rows = []
    for idx, profile in enumerate(profiles, start=1):
        trades = _collect_trades(frames, profile, start_time=start_time, end_time=end_time)
        m = metrics(trades)
        rows.append({"profile": profile, "metrics": m, "score": _score(m, min_trades)})
        if idx % 10 == 0 or idx == len(profiles):
            print(f"profile {idx}/{len(profiles)}", flush=True)
    rows.sort(key=lambda row: row["score"], reverse=True)
    return rows


def _select_with_validation(
    frames: list[tuple[str, pd.DataFrame, list[int]]],
    profiles: list[dict[str, Any]],
    *,
    train_start: pd.Timestamp | None,
    validation_start: pd.Timestamp,
    holdout_start: pd.Timestamp,
    min_train_trades: int,
    min_validation_trades: int,
    top_train: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    train_rows = _evaluate_profiles(
        frames,
        profiles,
        start_time=train_start,
        end_time=validation_start,
        min_trades=min_train_trades,
    )
    candidates = train_rows[: min(top_train, len(train_rows))]
    validation_rows = []
    for row in candidates:
        trades = _collect_trades(
            frames,
            row["profile"],
            start_time=validation_start,
            end_time=holdout_start,
        )
        m = metrics(trades)
        validation_rows.append(
            {
                "profile": row["profile"],
                "train": row["metrics"],
                "train_score": row["score"],
                "validation": m,
                "validation_score": _score(m, min_validation_trades),
            }
        )
    validation_rows.sort(key=lambda row: row["validation_score"], reverse=True)
    selected = validation_rows[0]
    audit = {
        "train_top": train_rows[:10],
        "validation_candidates": validation_rows,
        "selected": selected,
    }
    return dict(selected["profile"]), audit


def analyze(database: str, period: str) -> dict[str, Any]:
    frames: list[tuple[str, pd.DataFrame, list[int]]] = []
    signal_times: list[pd.Timestamp] = []

    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", period)
        for number, symbol in enumerate(symbols, start=1):
            frame = store.load_dataframe(symbol, "BIST", period, limit=0)
            if frame is None or len(frame) <= WARMUP + 22:
                continue
            data = build_indicator_frame(frame)
            positions = [
                int(pos)
                for pos in np.flatnonzero(data["signal"].to_numpy(dtype=bool))
                if pos >= WARMUP and pos + 1 < len(data)
            ]
            if positions:
                frames.append((symbol, data, positions))
                signal_times.extend(pd.Timestamp(data.index[pos]) for pos in positions)
            if number % 100 == 0 or number == len(symbols):
                print(f"[{period}] loaded {number}/{len(symbols)}", flush=True)

    signal_times.sort()
    if len(signal_times) < 5:
        raise RuntimeError(f"{period}: insufficient signals ({len(signal_times)})")

    holdout_index = max(1, min(len(signal_times) - 1, int(len(signal_times) * (1.0 - HOLDOUT_FRACTION))))
    holdout_start = pd.Timestamp(signal_times[holdout_index])
    dev_times = [t for t in signal_times if t < holdout_start]
    if len(dev_times) < 30:
        raise RuntimeError(f"{period}: insufficient development signals ({len(dev_times)})")
    validation_index = max(1, min(len(dev_times) - 1, int(len(dev_times) * TRAIN_FRACTION_OF_DEVELOPMENT)))
    validation_start = pd.Timestamp(dev_times[validation_index])

    if period == "1M" and len(dev_times) < MIN_MONTHLY_DEVELOPMENT_SIGNALS:
        baseline = _profile(technical_exit=False)
        validation_trades = _collect_trades(
            frames, baseline, start_time=validation_start, end_time=holdout_start
        )
        oos_trades = _collect_trades(frames, baseline, start_time=holdout_start, end_time=None)
        return {
            "version": "ne-ararsan-var-v1-staged-exit-v1",
            "period": period,
            "status": "INSUFFICIENT_FOR_OPTIMIZATION",
            "reason": f"development signals {len(dev_times)} < {MIN_MONTHLY_DEVELOPMENT_SIGNALS}",
            "split": {
                "signals_total": len(signal_times),
                "signals_development": len(dev_times),
                "validation_start": validation_start.isoformat(),
                "holdout_start": holdout_start.isoformat(),
            },
            "baseline_profile": baseline,
            "validation_moderate_cost": metrics(validation_trades),
            "holdout_baseline_only_moderate_cost": metrics(oos_trades),
            "holdout_note": "Reported descriptively only; sample is too small for profile promotion.",
        }

    # Stage A: 27 structural stop neighbors, with all other baseline choices fixed.
    stage_a_profiles = [
        _profile(stop_mode=mode, swing_lookback=lookback, atr_buffer=buffer, technical_exit=False)
        for mode in STOP_MODES
        for lookback in SWING_LOOKBACKS
        for buffer in ATR_BUFFERS
    ]

    train_estimate = max(30, int(len(dev_times) * TRAIN_FRACTION_OF_DEVELOPMENT * 0.15))
    validation_estimate = max(20, int(len(dev_times) * (1.0 - TRAIN_FRACTION_OF_DEVELOPMENT) * 0.12))
    selected_a, audit_a = _select_with_validation(
        frames,
        stage_a_profiles,
        train_start=None,
        validation_start=validation_start,
        holdout_start=holdout_start,
        min_train_trades=train_estimate,
        min_validation_trades=validation_estimate,
        top_train=7,
    )

    # Stage B: 72 target/trailing/BE/hold combinations around the chosen stop.
    stage_b_profiles = []
    for tp_name, (tp1, tp2, tp3) in TP_SETS.items():
        for trail in TRAIL_MULTS:
            for be in (True, False):
                for hold in MAX_HOLDS:
                    p = dict(selected_a)
                    p.update(
                        {
                            "tp_set": tp_name,
                            "tp1_r": tp1,
                            "tp2_r": tp2,
                            "tp3_r": tp3,
                            "trailing_atr_mult": trail,
                            "breakeven_after_tp1": be,
                            "max_hold_bars": hold,
                            "technical_exit": False,
                        }
                    )
                    stage_b_profiles.append(p)

    selected_b, audit_b = _select_with_validation(
        frames,
        stage_b_profiles,
        train_start=None,
        validation_start=validation_start,
        holdout_start=holdout_start,
        min_train_trades=train_estimate,
        min_validation_trades=validation_estimate,
        top_train=10,
    )

    # Stage C: only technical hard-exit on/off; choose on validation after A/B are frozen.
    tech_rows = []
    for technical in (False, True):
        p = dict(selected_b)
        p["technical_exit"] = technical
        validation_trades = _collect_trades(
            frames, p, start_time=validation_start, end_time=holdout_start
        )
        m = metrics(validation_trades)
        tech_rows.append({"profile": p, "validation": m, "score": _score(m, validation_estimate)})
    tech_rows.sort(key=lambda row: row["score"], reverse=True)
    final_profile = dict(tech_rows[0]["profile"])

    # Freeze profile now. Only after this line is the final 20% opened exactly once.
    validation_trades = _collect_trades(
        frames, final_profile, start_time=validation_start, end_time=holdout_start
    )
    holdout_trades = _collect_trades(
        frames, final_profile, start_time=holdout_start, end_time=None
    )

    scenarios = {}
    for scenario, (commission_bps, slippage_bps) in COST_SCENARIOS.items():
        scenarios[scenario] = {
            "commission_bps_per_side": commission_bps,
            "slippage_bps_per_side": slippage_bps,
            "validation": metrics(validation_trades, commission_bps, slippage_bps),
            "holdout": metrics(holdout_trades, commission_bps, slippage_bps),
        }

    moderate_oos = scenarios["moderate_10_10bps"]["holdout"]
    promoted = bool(
        int(moderate_oos.get("trades", 0)) >= 30
        and float(moderate_oos.get("expectancy_r", 0.0)) > 0.0
        and float(moderate_oos.get("profit_factor") or 0.0) > 1.20
    )

    return {
        "version": "ne-ararsan-var-v1-staged-exit-v1",
        "period": period,
        "status": "PROMOTED" if promoted else "RESEARCH_ONLY",
        "selection_rule": "staged train ranking -> validation selection; final 20% opened once",
        "split": {
            "signals_total": len(signal_times),
            "signals_development": len(dev_times),
            "validation_start": validation_start.isoformat(),
            "holdout_start": holdout_start.isoformat(),
        },
        "stage_a_structure": audit_a,
        "stage_b_targets_trail_hold": audit_b,
        "stage_c_technical_exit": tech_rows,
        "final_profile": final_profile,
        "scenarios": scenarios,
        "promotion_gate": {
            "min_holdout_trades": 30,
            "moderate_cost_expectancy_r_gt": 0.0,
            "moderate_cost_profit_factor_gt": 1.20,
            "passed": promoted,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = analyze(args.db, args.period)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
