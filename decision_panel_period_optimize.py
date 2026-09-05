"""Walk-forward exit research for System J (Decision Panel) on any timeframe.

Entry logic is the exact v6.4.5 causal signal layer.  No entry thresholds are
re-optimized here.  For each setup, exit profiles are selected on train,
validated chronologically, measured once on untouched OOS, then stressed with
explicit trading costs.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from decision_panel_v645_signal import decision_v645_series
from market_data_store import MarketDataStore, normalize_period
from strategy_exit_profiles import ExitProfile, get_exit_profile
from strategy_optimizer import eligible, parameter_grid, ranking_score, robust_metrics
from trade_backtester import simulate_trade


SETUP_COLUMNS = {
    "PULLBACK": "new_pullback",
    "BREAKOUT": "new_breakout",
    "TREND DEVAMI": "new_trend",
}

SCENARIOS = (
    ("gross", 0.0, 0.0),
    ("light_5_5bps", 5.0, 5.0),
    ("moderate_10_10bps", 10.0, 10.0),
    ("stress_20_10bps", 20.0, 10.0),
)


def _profile_from_dict(data: dict[str, Any]) -> ExitProfile:
    normalized = dict(data)
    if "tp_allocations" in normalized:
        normalized["tp_allocations"] = tuple(normalized["tp_allocations"])
    return ExitProfile(**normalized)


def _net_r(trade: dict[str, Any], commission_bps: float, slippage_bps: float) -> float:
    entry = float(trade["entry"])
    risk = float(trade["plan"]["risk_per_share"])
    gross_r = float(trade["realized_r"])
    if not np.isfinite(entry) or not np.isfinite(risk) or risk <= 0:
        return gross_r
    gross_exit = entry + gross_r * risk
    c = max(float(commission_bps), 0.0) / 10000.0
    s = max(float(slippage_bps), 0.0) / 10000.0
    buy = entry * (1.0 + s)
    sell = gross_exit * (1.0 - s)
    return (sell * (1.0 - c) - buy * (1.0 + c)) / risk


def _cost_metrics(trades: list[dict[str, Any]], commission_bps: float, slippage_bps: float) -> dict[str, Any]:
    adjusted = []
    for trade in trades:
        item = dict(trade)
        item["realized_r"] = _net_r(trade, commission_bps, slippage_bps)
        item["winner"] = item["realized_r"] > 0
        adjusted.append(item)
    return robust_metrics(adjusted)


def load_events(database: str, period: str, setup: str, min_score: int = 75):
    period = normalize_period(period)
    column = SETUP_COLUMNS[setup]
    frames: dict[str, pd.DataFrame] = {}
    events: dict[str, list[int]] = {}
    event_times: list[pd.Timestamp] = []
    errors = 0

    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", period)
        for number, symbol in enumerate(symbols, start=1):
            raw = store.load_dataframe(symbol, "BIST", period, limit=0)
            if raw is None or len(raw) < 280:
                continue
            try:
                state = decision_v645_series(raw, min_score=min_score)
                indexes = [
                    int(i)
                    for i in np.flatnonzero(state[column].fillna(False).to_numpy(dtype=bool))
                    if int(i) < len(raw) - 1
                ]
                if indexes:
                    frames[symbol] = raw
                    events[symbol] = indexes
                    event_times.extend(pd.Timestamp(raw.index[i]) for i in indexes)
                if number % 50 == 0 or number == len(symbols):
                    print(f"[{number}/{len(symbols)}] {period} {setup}: {len(event_times)} event")
            except Exception as exc:
                errors += 1
                print(f"[{number}/{len(symbols)}] {symbol}: HATA {exc}")

    return frames, events, event_times, {
        "period": period,
        "setup": setup,
        "symbols_with_events": len(events),
        "signal_events": len(event_times),
        "errors": errors,
    }


def _cuts(event_times: list[pd.Timestamp]) -> tuple[pd.Timestamp, pd.Timestamp]:
    if len(event_times) < 20:
        raise RuntimeError(f"Yalniz {len(event_times)} sinyal var")
    ordered = pd.Series(pd.to_datetime(event_times)).sort_values().reset_index(drop=True)
    train_cut = pd.Timestamp(ordered.iloc[max(0, int(len(ordered) * 0.60) - 1)])
    val_cut = pd.Timestamp(ordered.iloc[max(0, int(len(ordered) * 0.80) - 1)])
    return train_cut, val_cut


def _segment(ts: pd.Timestamp, train_cut: pd.Timestamp, val_cut: pd.Timestamp) -> str:
    if ts <= train_cut:
        return "train"
    if ts <= val_cut:
        return "validation"
    return "oos"


def replay(
    frames: dict[str, pd.DataFrame],
    events: dict[str, list[int]],
    setup: str,
    profile: ExitProfile,
    train_cut: pd.Timestamp,
    val_cut: pd.Timestamp,
) -> dict[str, list[dict[str, Any]]]:
    buckets = {"train": [], "validation": [], "oos": []}
    for symbol, indexes in events.items():
        frame = frames[symbol]
        lookup = {pd.Timestamp(value): pos for pos, value in enumerate(frame.index)}
        last_exit = {"train": -1, "validation": -1, "oos": -1}
        for signal_index in indexes:
            signal_time = pd.Timestamp(frame.index[signal_index])
            bucket = _segment(signal_time, train_cut, val_cut)
            if signal_index <= last_exit[bucket]:
                continue
            trade = simulate_trade(frame, signal_index, "decision_panel", setup=setup, profile=profile)
            if trade is None:
                continue
            entry_time = pd.Timestamp(trade["entry_time"])
            exit_time = pd.Timestamp(trade["exit_time"])
            if _segment(entry_time, train_cut, val_cut) != bucket:
                continue
            if _segment(exit_time, train_cut, val_cut) != bucket:
                continue
            item = dict(trade)
            item.update({"symbol": symbol, "signal_time": signal_time.isoformat()})
            buckets[bucket].append(item)
            last_exit[bucket] = int(lookup.get(exit_time, signal_index))
    return buckets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--setup", choices=tuple(SETUP_COLUMNS), required=True)
    parser.add_argument("--min-score", type=int, default=75)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    frames, events, times, meta = load_events(args.db, args.period, args.setup, min_score=args.min_score)
    if len(times) < 20:
        payload = {"metadata": meta, "status": "INSUFFICIENT_EVENTS", "moderate_cost_pass": False}
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    train_cut, val_cut = _cuts(times)
    base = get_exit_profile("decision_panel", setup=args.setup)
    ranked = []
    for profile in parameter_grid(base):
        buckets = replay(frames, events, args.setup, profile, train_cut, val_cut)
        train_metrics = robust_metrics(buckets["train"])
        val_metrics = robust_metrics(buckets["validation"])
        train_ok = eligible(train_metrics, min_trades=30, min_pf=1.10)
        val_ok = eligible(val_metrics, min_trades=12, min_pf=1.05)
        ranked.append({
            "profile": asdict(profile),
            "train": train_metrics,
            "validation": val_metrics,
            "eligible": bool(train_ok and val_ok),
            "score": ranking_score(val_metrics, min_trades=12, min_pf=1.05) if val_ok else -1e9,
        })
    ranked.sort(key=lambda row: row["score"], reverse=True)
    selected = next((row for row in ranked if row["eligible"]), None)

    if selected is None:
        payload = {
            "metadata": {**meta, "train_cut": train_cut.isoformat(), "validation_cut": val_cut.isoformat()},
            "status": "NO_VALIDATED_PROFILE",
            "moderate_cost_pass": False,
            "top_validation": ranked[:10],
        }
    else:
        profile = _profile_from_dict(selected["profile"])
        buckets = replay(frames, events, args.setup, profile, train_cut, val_cut)
        scenarios = {}
        for name, commission_bps, slippage_bps in SCENARIOS:
            scenarios[name] = {
                "commission_bps_per_side": commission_bps,
                "slippage_bps_per_side": slippage_bps,
                "metrics": _cost_metrics(buckets["oos"], commission_bps, slippage_bps),
            }
        moderate = scenarios["moderate_10_10bps"]["metrics"]
        robust_sample = int(moderate.get("trades", 0)) >= 20
        pass_gate = bool(
            robust_sample
            and float(moderate.get("expectancy_r", 0.0)) > 0
            and float(moderate.get("profit_factor", 0.0)) >= 1.20
        )
        payload = {
            "metadata": {
                **meta,
                "train_cut": train_cut.isoformat(),
                "validation_cut": val_cut.isoformat(),
                "split_rule": "60/20/20 chronological setup events",
                "leakage_guard": "signal_entry_exit_must_stay_inside_segment",
            },
            "status": "PASS" if pass_gate else "OOS_COST_FAIL_OR_SMALL_SAMPLE",
            "selected": selected,
            "out_of_sample": robust_metrics(buckets["oos"]),
            "scenarios": scenarios,
            "robust_sample": robust_sample,
            "moderate_cost_pass": pass_gate,
            "top_validation": ranked[:10],
        }

    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
