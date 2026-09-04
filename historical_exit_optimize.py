"""Outcome-contained walk-forward exit optimization on historical signals.

Signals are generated once with production scanner/filter logic. Candidate exit
profiles are optimized on the training slice, checked on validation and then
reported once on the untouched out-of-sample slice.

Leakage protection is based on completed-trade containment: a trade belongs to
a chronological split only when its entry and exit are both inside that split.
This is stricter and more data-efficient than blindly removing 80 bars from the
end of every symbol's train/validation window.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from historical_replay import STRATEGIES, signal_indexes
from market_data_store import MarketDataStore
from strategy_exit_profiles import ExitProfile, get_exit_profile
from strategy_optimizer import (
    eligible,
    evaluate_profile,
    parameter_grid,
    ranking_score,
    robust_metrics,
)


SplitDataset = tuple[pd.DataFrame, list[int], pd.Timestamp | None, pd.Timestamp | None]


def _profile_from_dict(data: dict[str, Any]) -> ExitProfile:
    normalized = dict(data)
    if "tp_allocations" in normalized:
        normalized["tp_allocations"] = tuple(normalized["tp_allocations"])
    return ExitProfile(**normalized)


def _trade_in_split(
    trade: dict[str, Any],
    start_exclusive: pd.Timestamp | None,
    end_inclusive: pd.Timestamp | None,
) -> bool:
    entry_time = pd.Timestamp(trade["entry_time"])
    exit_time = pd.Timestamp(trade["exit_time"])
    if start_exclusive is not None and entry_time <= start_exclusive:
        return False
    if end_inclusive is not None and (entry_time > end_inclusive or exit_time > end_inclusive):
        return False
    return True


def _aggregate_profile(
    datasets: list[SplitDataset],
    strategy: str,
    profile: ExitProfile,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for frame, indexes, start_exclusive, end_inclusive in datasets:
        symbol_trades, _ = evaluate_profile(frame, indexes, strategy, profile)
        trades.extend(
            trade
            for trade in symbol_trades
            if _trade_in_split(trade, start_exclusive, end_inclusive)
        )
    return trades, robust_metrics(trades)


def _optimize_profiles_contained(
    datasets: list[SplitDataset],
    strategy: str,
    *,
    min_trades: int,
    min_pf: float,
    top_n: int,
) -> list[dict[str, Any]]:
    base = get_exit_profile(strategy)
    ranked: list[dict[str, Any]] = []
    for profile in parameter_grid(base):
        _, metrics = _aggregate_profile(datasets, strategy, profile)
        ranked.append(
            {
                "score": ranking_score(metrics, min_trades=min_trades, min_pf=min_pf),
                "eligible": eligible(metrics, min_trades=min_trades, min_pf=min_pf),
                "profile": asdict(profile),
                "metrics": metrics,
            }
        )
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return ranked[:top_n]


def prepare_walk_forward(
    database: str,
    strategy: str,
    *,
    period: str = "1D",
    warmup: int = 240,
    max_symbols: int = 0,
    purge_bars: int = 0,
) -> tuple[dict[str, list[SplitDataset]], dict[str, Any]]:
    """Create chronological signal slices.

    `purge_bars` is retained only for CLI compatibility with older workflow
    invocations. Outcome containment is now the leakage guard, so no fixed bar
    deletion is applied.
    """
    raw: list[tuple[str, pd.DataFrame, list[int]]] = []
    event_times: list[pd.Timestamp] = []

    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", period)
        if max_symbols > 0:
            symbols = symbols[:max_symbols]

        for number, symbol in enumerate(symbols, start=1):
            frame = store.load_dataframe(symbol, "BIST", period, limit=0)
            if frame is None or len(frame) <= warmup + 2:
                continue
            try:
                indexes = signal_indexes(frame, strategy, warmup=warmup, fresh_only=True)
            except Exception as exc:
                print(f"[{number}/{len(symbols)}] {symbol}: signal HATA {exc}")
                continue
            indexes = [i for i in indexes if i < len(frame) - 1]
            if not indexes:
                continue
            raw.append((symbol, frame, indexes))
            event_times.extend(pd.Timestamp(frame.index[i]) for i in indexes)
            print(f"[{number}/{len(symbols)}] {symbol}: {len(indexes)} sinyal")

    if len(event_times) < 10:
        return {"train": [], "validation": [], "test": []}, {
            "signal_events": len(event_times),
            "symbols_with_signals": len(raw),
            "error": "Yeterli tarihsel sinyal yok",
        }

    ordered = pd.Series(pd.to_datetime(event_times)).sort_values().reset_index(drop=True)
    train_cut = pd.Timestamp(ordered.iloc[max(0, int(len(ordered) * 0.60) - 1)])
    validation_cut = pd.Timestamp(ordered.iloc[max(0, int(len(ordered) * 0.80) - 1)])

    result: dict[str, list[SplitDataset]] = {
        "train": [], "validation": [], "test": []
    }
    counts = {"train": 0, "validation": 0, "test": 0}

    for _symbol, frame, indexes in raw:
        timestamps = pd.to_datetime(frame.index)
        train_idx = [
            i for i in indexes
            if pd.Timestamp(timestamps[i]) <= train_cut
        ]
        validation_idx = [
            i for i in indexes
            if train_cut < pd.Timestamp(timestamps[i]) <= validation_cut
        ]
        test_idx = [
            i for i in indexes
            if pd.Timestamp(timestamps[i]) > validation_cut
        ]

        if train_idx:
            result["train"].append((frame, train_idx, None, train_cut))
            counts["train"] += len(train_idx)
        if validation_idx:
            result["validation"].append((frame, validation_idx, train_cut, validation_cut))
            counts["validation"] += len(validation_idx)
        if test_idx:
            result["test"].append((frame, test_idx, validation_cut, None))
            counts["test"] += len(test_idx)

    metadata = {
        "strategy": strategy,
        "period": period,
        "symbols_with_signals": len(raw),
        "signal_events": len(event_times),
        "train_cut": train_cut.isoformat(),
        "validation_cut": validation_cut.isoformat(),
        "split_rule": "60/20/20 chronological signal events",
        "leakage_guard": "entry_and_exit_must_both_stay_inside_split",
        "legacy_purge_bars_ignored": int(purge_bars),
        "candidate_signal_counts": counts,
    }
    return result, metadata


def optimize_walk_forward(
    database: str,
    strategy: str,
    *,
    period: str = "1D",
    warmup: int = 240,
    max_symbols: int = 0,
    purge_bars: int = 0,
    top_train: int = 20,
) -> dict[str, Any]:
    datasets, metadata = prepare_walk_forward(
        database,
        strategy,
        period=period,
        warmup=warmup,
        max_symbols=max_symbols,
        purge_bars=purge_bars,
    )

    train_ranked = _optimize_profiles_contained(
        datasets["train"],
        strategy,
        min_trades=30,
        min_pf=1.20,
        top_n=top_train,
    )

    validation_rows: list[dict[str, Any]] = []
    for row in train_ranked:
        profile = _profile_from_dict(row["profile"])
        _, validation_metrics = _aggregate_profile(datasets["validation"], strategy, profile)
        validation_rows.append(
            {
                "profile": asdict(profile),
                "train": row["metrics"],
                "train_score": row["score"],
                "validation": validation_metrics,
                "validation_eligible": eligible(validation_metrics, min_trades=15, min_pf=1.10),
                "validation_score": ranking_score(validation_metrics, min_trades=15, min_pf=1.10),
            }
        )

    validation_rows.sort(key=lambda item: item["validation_score"], reverse=True)
    selected = next((row for row in validation_rows if row["validation_eligible"]), None)

    test_metrics: dict[str, Any] | None = None
    if selected is not None:
        profile = _profile_from_dict(selected["profile"])
        _, test_metrics = _aggregate_profile(datasets["test"], strategy, profile)

    return {
        "metadata": metadata,
        "selected": selected,
        "out_of_sample": test_metrics,
        "top_validation": validation_rows[:10],
        "top_train": train_ranked[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Outcome-contained walk-forward exit optimizer")
    parser.add_argument("--db", required=True)
    parser.add_argument("--strategy", choices=STRATEGIES, required=True)
    parser.add_argument("--period", default="1D")
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--warmup", type=int, default=240)
    parser.add_argument("--purge-bars", type=int, default=0, help="Deprecated; containment now protects split boundaries")
    parser.add_argument("--top-train", type=int, default=20)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result = optimize_walk_forward(
        args.db,
        args.strategy,
        period=args.period,
        warmup=args.warmup,
        max_symbols=args.max_symbols,
        purge_bars=args.purge_bars,
        top_train=args.top_train,
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    summary = {
        "metadata": result["metadata"],
        "selected": result["selected"],
        "out_of_sample": result["out_of_sample"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
