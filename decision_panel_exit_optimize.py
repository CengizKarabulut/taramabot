"""Setup-specific exit optimization for exact Karar Paneli v6.4.4 signals.

BREAKOUT, PULLBACK and TREND DEVAMI are optimized independently.  Chronological
60/20/20 signal-event splits are used and a completed trade is scored in a
split only when both entry and exit remain inside that split.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from historical_decision_panel_v644 import SETUPS, _normalize, decision_series
from market_data_store import MarketDataStore
from strategy_exit_profiles import ExitProfile, get_exit_profile
from strategy_optimizer import eligible, parameter_grid, ranking_score, robust_metrics
from trade_backtester import simulate_trade


def _profile_from_dict(data: dict[str, Any]) -> ExitProfile:
    normalized = dict(data)
    normalized["tp_allocations"] = tuple(normalized.get("tp_allocations", (0.30, 0.30, 0.20)))
    return ExitProfile(**normalized)


def collect_events(database: str, setup: str, min_score: int = 75) -> list[tuple[str, pd.DataFrame, list[int]]]:
    result: list[tuple[str, pd.DataFrame, list[int]]] = []
    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", "1D")
        for number, symbol in enumerate(symbols, start=1):
            frame = store.load_dataframe(symbol, "BIST", "1D", limit=0)
            if frame is None or frame.empty:
                continue
            try:
                clean = _normalize(frame)
                state = decision_series(clean, min_score=min_score)
                column = {
                    "BREAKOUT": "new_breakout",
                    "PULLBACK": "new_pullback",
                    "TREND DEVAMI": "new_trend",
                }[setup]
                indexes = [int(i) for i in np.flatnonzero(state[column].to_numpy(dtype=bool)) if int(i) < len(clean)-1]
                if indexes:
                    result.append((symbol, clean, indexes))
                    print(f"[{number}/{len(symbols)}] {symbol}: {len(indexes)} {setup}")
            except Exception as exc:
                print(f"[{number}/{len(symbols)}] {symbol}: HATA {exc}")
    return result


def contained_trade(trade: dict[str, Any], start: pd.Timestamp | None, end: pd.Timestamp | None) -> bool:
    entry = pd.Timestamp(trade["entry_time"])
    exit_ = pd.Timestamp(trade["exit_time"])
    if start is not None and entry <= start:
        return False
    if end is not None and (entry > end or exit_ > end):
        return False
    return True


def evaluate(
    datasets: list[tuple[pd.DataFrame, list[int], pd.Timestamp | None, pd.Timestamp | None]],
    setup: str,
    profile: ExitProfile,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_trades: list[dict[str, Any]] = []
    for frame, indexes, start, end in datasets:
        last_exit = -1
        index_lookup = {pd.Timestamp(value): pos for pos, value in enumerate(frame.index)}
        for index in indexes:
            if index <= last_exit:
                continue
            trade = simulate_trade(frame, index, "decision_panel", setup=setup, profile=profile)
            if trade is None:
                continue
            exit_pos = index_lookup.get(pd.Timestamp(trade["exit_time"]), index)
            last_exit = int(exit_pos)
            if contained_trade(trade, start, end):
                all_trades.append(trade)
    return all_trades, robust_metrics(all_trades)


def split_events(raw: list[tuple[str, pd.DataFrame, list[int]]]) -> tuple[dict[str, list], dict[str, Any]]:
    event_times = sorted(pd.Timestamp(frame.index[i]) for _, frame, idxs in raw for i in idxs)
    if len(event_times) < 10:
        return {"train": [], "validation": [], "test": []}, {"events": len(event_times), "error": "Yeterli sinyal yok"}
    train_cut = event_times[max(0, int(len(event_times)*0.60)-1)]
    val_cut = event_times[max(0, int(len(event_times)*0.80)-1)]
    datasets = {"train": [], "validation": [], "test": []}
    counts = {"train": 0, "validation": 0, "test": 0}
    for _symbol, frame, indexes in raw:
        train = [i for i in indexes if pd.Timestamp(frame.index[i]) <= train_cut]
        val = [i for i in indexes if train_cut < pd.Timestamp(frame.index[i]) <= val_cut]
        test = [i for i in indexes if pd.Timestamp(frame.index[i]) > val_cut]
        if train:
            datasets["train"].append((frame, train, None, train_cut)); counts["train"] += len(train)
        if val:
            datasets["validation"].append((frame, val, train_cut, val_cut)); counts["validation"] += len(val)
        if test:
            datasets["test"].append((frame, test, val_cut, None)); counts["test"] += len(test)
    return datasets, {
        "events": len(event_times), "train_cut": train_cut.isoformat(), "validation_cut": val_cut.isoformat(),
        "counts": counts, "leakage_guard": "entry_and_exit_must_stay_inside_split",
    }


def optimize(database: str, setup: str, min_score: int = 75) -> dict[str, Any]:
    raw = collect_events(database, setup, min_score=min_score)
    datasets, metadata = split_events(raw)
    metadata.update({"setup": setup, "min_score": min_score, "symbols_with_signals": len(raw)})
    if not datasets["train"]:
        return {"metadata": metadata, "selected": None, "out_of_sample": None, "top_train": [], "top_validation": []}

    base = get_exit_profile("decision_panel", setup=setup)
    train_rows = []
    for profile in parameter_grid(base):
        _, metrics = evaluate(datasets["train"], setup, profile)
        train_rows.append({
            "profile": asdict(profile), "metrics": metrics,
            "eligible": eligible(metrics, min_trades=20, min_pf=1.15),
            "score": ranking_score(metrics, min_trades=20, min_pf=1.15),
        })
    train_rows.sort(key=lambda row: row["score"], reverse=True)
    top_train = train_rows[:20]

    validation = []
    for row in top_train:
        profile = _profile_from_dict(row["profile"])
        _, metrics = evaluate(datasets["validation"], setup, profile)
        validation.append({
            "profile": row["profile"], "train": row["metrics"], "train_score": row["score"],
            "validation": metrics,
            "validation_eligible": eligible(metrics, min_trades=8, min_pf=1.05),
            "validation_score": ranking_score(metrics, min_trades=8, min_pf=1.05),
        })
    validation.sort(key=lambda row: row["validation_score"], reverse=True)
    selected = next((row for row in validation if row["validation_eligible"]), None)
    oos = None
    if selected:
        _, oos = evaluate(datasets["test"], setup, _profile_from_dict(selected["profile"]))
    return {
        "metadata": metadata, "selected": selected, "out_of_sample": oos,
        "top_train": top_train[:10], "top_validation": validation[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--setup", choices=SETUPS, required=True)
    parser.add_argument("--min-score", type=int, default=75)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = optimize(args.db, args.setup, args.min_score)
    destination = Path(args.output); destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps({"metadata": result["metadata"], "selected": result["selected"], "out_of_sample": result["out_of_sample"]}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
