"""Walk-forward 15m exit calibration for one scanner family.

Signals are frozen by signal_parity.py.  Exit candidates are compared only on
chronological train/validation windows; the final 20% OOS window is untouched
until a candidate has been selected.  Moderate transaction costs are part of
the validation gate.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from intraday_historical_replay import cost_metrics
from market_data_store import MarketDataStore
from signal_parity import STRATEGIES, STRATEGY_TO_CODE, build_signal_frame, fresh_signal_indexes
from strategy_exit_profiles import ExitProfile, get_exit_profile
from strategy_optimizer import ranking_score, robust_metrics
from trade_backtester import simulate_trade


MODERATE_COMMISSION_BPS = 10.0
MODERATE_SLIPPAGE_BPS = 10.0


def candidate_profiles(strategy: str) -> list[ExitProfile]:
    base = get_exit_profile(strategy)
    candidates: list[ExitProfile] = [base]

    # One-factor-at-a-time candidates keep the search intentionally small and
    # interpretable on the first intraday calibration pass.
    for mode in ("swing", "hybrid_tight", "hybrid_wide"):
        candidates.append(replace(base, stop_mode=mode))
    for tp1, tp2, tp3 in ((0.8, 1.5, 2.4), (1.0, 1.8, 2.8), (1.0, 2.0, 3.0)):
        candidates.append(replace(base, tp1_r=tp1, tp2_r=tp2, tp3_r=tp3))
    for hold in (32, 48, 64):
        candidates.append(replace(base, max_hold_bars=hold))
    for trail in (1.5, 2.0, 2.4):
        candidates.append(replace(base, trailing_atr_mult=trail))

    unique: dict[str, ExitProfile] = {}
    for profile in candidates:
        key = json.dumps(asdict(profile), sort_keys=True, default=str)
        unique[key] = profile
    return list(unique.values())


def _global_cuts(datasets: list[tuple[str, pd.DataFrame, list[int]]]) -> tuple[pd.Timestamp, pd.Timestamp]:
    times: list[pd.Timestamp] = []
    for _symbol, frame, indexes in datasets:
        if not indexes:
            continue
        times.extend(pd.Timestamp(frame.index[pos]) for pos in indexes)
    if not times:
        raise RuntimeError("Kalibrasyon icin sinyal bulunamadi")
    ordered = pd.Series(sorted(times))
    return (
        pd.Timestamp(ordered.quantile(0.60, interpolation="nearest")),
        pd.Timestamp(ordered.quantile(0.80, interpolation="nearest")),
    )


def _simulate_window(
    datasets: list[tuple[str, pd.DataFrame, list[int]]],
    strategy: str,
    profile: ExitProfile,
    *,
    start_exclusive: pd.Timestamp | None,
    end_inclusive: pd.Timestamp | None,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for symbol, full_frame, indexes in datasets:
        if full_frame.empty:
            continue
        if end_inclusive is not None:
            end_positions = np.flatnonzero(pd.to_datetime(full_frame.index) <= end_inclusive)
            if len(end_positions) < 2:
                continue
            frame = full_frame.iloc[: int(end_positions[-1]) + 1]
            max_pos = len(frame) - 2
        else:
            frame = full_frame
            max_pos = len(frame) - 2

        valid = []
        for pos in indexes:
            if pos > max_pos:
                continue
            stamp = pd.Timestamp(full_frame.index[pos])
            if start_exclusive is not None and stamp <= start_exclusive:
                continue
            if end_inclusive is not None and stamp > end_inclusive:
                continue
            valid.append(pos)

        last_exit_position = -1
        index_lookup = {pd.Timestamp(value): pos for pos, value in enumerate(frame.index)}
        for signal_pos in valid:
            if signal_pos <= last_exit_position:
                continue
            trade = simulate_trade(frame, signal_pos, strategy, profile=profile)
            if trade is None:
                continue
            item = dict(trade)
            item.update(
                {
                    "symbol": symbol,
                    "period": "15m",
                    "code": STRATEGY_TO_CODE[strategy],
                    "signal_time": pd.Timestamp(full_frame.index[signal_pos]).isoformat(),
                }
            )
            trades.append(item)
            exit_pos = index_lookup.get(pd.Timestamp(item["exit_time"]))
            if exit_pos is not None:
                last_exit_position = int(exit_pos)
    return trades


def _eligible(train: dict[str, Any], validation: dict[str, Any], validation_cost: dict[str, Any]) -> bool:
    return bool(
        int(train.get("trades", 0)) >= 30
        and float(train.get("expectancy_r", 0.0)) > 0
        and float(train.get("profit_factor", 0.0)) >= 1.10
        and int(validation.get("trades", 0)) >= 20
        and float(validation.get("expectancy_r", 0.0)) > 0
        and float(validation.get("profit_factor", 0.0)) >= 1.05
        and float(validation_cost.get("expectancy_r", 0.0)) > 0
        and float(validation_cost.get("profit_factor", 0.0)) >= 1.00
    )


def calibrate(database: str, daily_database: str, strategy: str, max_symbols: int = 0, warmup: int = 240) -> dict[str, Any]:
    datasets: list[tuple[str, pd.DataFrame, list[int]]] = []
    with MarketDataStore(database, read_only=True) as store, MarketDataStore(daily_database, read_only=True) as daily_store:
        symbols = store.list_symbols("BIST", "15m")
        if max_symbols > 0:
            symbols = symbols[:max_symbols]
        for number, symbol in enumerate(symbols, start=1):
            frame = store.load_dataframe(symbol, "BIST", "15m", limit=0)
            if frame is None or len(frame) <= warmup + 1:
                continue
            daily = daily_store.load_dataframe(symbol, "BIST", "1D", limit=0)
            signal_frame = build_signal_frame(frame, daily=daily, warmup=warmup)
            indexes = fresh_signal_indexes(signal_frame, strategy)
            if indexes:
                datasets.append((symbol, frame, indexes))
            if number % 100 == 0 or number == len(symbols):
                print(f"Signal cache {number}/{len(symbols)}; aktif sembol={len(datasets)}")

    train_cut, validation_cut = _global_cuts(datasets)
    base = get_exit_profile(strategy)
    rows = []
    for number, profile in enumerate(candidate_profiles(strategy), start=1):
        train_trades = _simulate_window(
            datasets, strategy, profile, start_exclusive=None, end_inclusive=train_cut
        )
        validation_trades = _simulate_window(
            datasets, strategy, profile, start_exclusive=train_cut, end_inclusive=validation_cut
        )
        train_metrics = robust_metrics(train_trades)
        validation_metrics = robust_metrics(validation_trades)
        validation_cost = cost_metrics(
            validation_trades, MODERATE_COMMISSION_BPS, MODERATE_SLIPPAGE_BPS
        )
        eligible = _eligible(train_metrics, validation_metrics, validation_cost)
        score = ranking_score(validation_cost, min_trades=20, min_pf=1.0) if eligible else -1e9
        row = {
            "candidate": number,
            "is_baseline": asdict(profile) == asdict(base),
            "profile": asdict(profile),
            "eligible": eligible,
            "selection_score": score,
            "train": train_metrics,
            "validation": validation_metrics,
            "validation_moderate_cost": validation_cost,
        }
        rows.append(row)
        print(
            f"candidate={number}/{len(candidate_profiles(strategy))} eligible={eligible} "
            f"valPF={validation_metrics.get('profit_factor', 0):.3f} "
            f"costPF={validation_cost.get('profit_factor', 0):.3f}"
        )

    eligible_rows = [row for row in rows if row["eligible"]]
    if eligible_rows:
        selected_row = max(eligible_rows, key=lambda row: row["selection_score"])
        selection_reason = "best validation moderate-cost score among eligible candidates"
    else:
        selected_row = next(row for row in rows if row["is_baseline"])
        selection_reason = "no candidate passed validation gates; baseline retained"

    selected_profile = ExitProfile(**selected_row["profile"])
    baseline_oos = _simulate_window(
        datasets, strategy, base, start_exclusive=validation_cut, end_inclusive=None
    )
    selected_oos = _simulate_window(
        datasets, strategy, selected_profile, start_exclusive=validation_cut, end_inclusive=None
    )

    baseline_oos_gross = robust_metrics(baseline_oos)
    selected_oos_gross = robust_metrics(selected_oos)
    baseline_oos_cost = cost_metrics(baseline_oos, MODERATE_COMMISSION_BPS, MODERATE_SLIPPAGE_BPS)
    selected_oos_cost = cost_metrics(selected_oos, MODERATE_COMMISSION_BPS, MODERATE_SLIPPAGE_BPS)

    promoted = bool(
        int(selected_oos_cost.get("trades", 0)) >= 30
        and float(selected_oos_cost.get("expectancy_r", 0.0)) > 0
        and float(selected_oos_cost.get("profit_factor", 0.0)) >= 1.10
        and float(selected_oos_gross.get("wilson_win_rate", 0.0)) > 0
    )

    return {
        "code": STRATEGY_TO_CODE[strategy],
        "strategy": strategy,
        "period": "15m",
        "method": "60/20/20 chronological walk-forward; OOS untouched until selection",
        "train_end": train_cut.isoformat(),
        "validation_end": validation_cut.isoformat(),
        "candidate_count": len(rows),
        "selection_reason": selection_reason,
        "selected_profile": selected_row["profile"],
        "baseline_profile": asdict(base),
        "selected_candidate": selected_row["candidate"],
        "selected_train": selected_row["train"],
        "selected_validation": selected_row["validation"],
        "selected_validation_moderate_cost": selected_row["validation_moderate_cost"],
        "oos": {
            "baseline_gross": baseline_oos_gross,
            "baseline_moderate_cost": baseline_oos_cost,
            "selected_gross": selected_oos_gross,
            "selected_moderate_cost": selected_oos_cost,
        },
        "promote_15m_profile": promoted,
        "candidates": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--daily-db", required=True)
    parser.add_argument("--strategy", choices=STRATEGIES, required=True)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--warmup", type=int, default=240)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = calibrate(
        args.db,
        args.daily_db,
        args.strategy,
        max_symbols=args.max_symbols,
        warmup=args.warmup,
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
