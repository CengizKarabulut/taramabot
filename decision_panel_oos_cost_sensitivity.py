"""Production-fixed OOS cost sensitivity for Karar Paneli v6.4.5.

No parameters are selected here.  Each setup reuses the exact chronological
research split and the production entry rule/profile that already survived the
prior selection process.  The untouched OOS trades are then stressed with
explicit per-side commission and slippage assumptions.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from decision_panel_pullback_v645_research import load_universe as load_pullback_universe
from decision_panel_v645_research import chronological_cuts, load_universe as load_setup_universe
from strategy_exit_profiles import get_exit_profile
from strategy_optimizer import robust_metrics
from trade_backtester import simulate_trade


SETUP_CONFIG = {
    "PULLBACK": {
        "event_name": "pullback_rv2_near20",
        "min_trades": 30,
    },
    "TREND DEVAMI": {
        "event_name": "trend_v644_baseline",
        "min_trades": 30,
    },
    "BREAKOUT": {
        "event_name": "breakout_dist_1.50",
        "min_trades": 30,
    },
}

SCENARIOS = (
    ("gross", 0.0, 0.0),
    ("light_5_5bps", 5.0, 5.0),
    ("moderate_10_10bps", 10.0, 10.0),
    ("stress_20_10bps", 20.0, 10.0),
)


def _segment(value: pd.Timestamp, cut1: pd.Timestamp, cut2: pd.Timestamp, cut3: pd.Timestamp) -> str:
    if value <= cut1:
        return "train"
    if value <= cut2:
        return "validation1"
    if value <= cut3:
        return "validation2"
    return "oos"


def _load(database: str, setup: str):
    if setup == "PULLBACK":
        frames, event_map, meta = load_pullback_universe(database, 75)
    else:
        frames, event_map, meta = load_setup_universe(database, setup, min_score=75)
    cuts = chronological_cuts(frames, event_map)
    event_name = SETUP_CONFIG[setup]["event_name"]
    if event_name not in event_map:
        raise RuntimeError(f"Beklenen event haritası bulunamadı: {event_name}")
    return frames, event_map[event_name], meta, cuts


def replay_oos(database: str, setup: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frames, events, meta, cuts = _load(database, setup)
    cut1, cut2, cut3 = cuts
    profile = get_exit_profile("decision_panel", setup=setup)
    trades: list[dict[str, Any]] = []

    for symbol, indexes in events.items():
        frame = frames[symbol]
        index_lookup = {pd.Timestamp(value): pos for pos, value in enumerate(frame.index)}
        last_exit = -1
        for signal_index in indexes:
            signal_time = pd.Timestamp(frame.index[signal_index])
            if _segment(signal_time, cut1, cut2, cut3) != "oos":
                continue
            if signal_index <= last_exit:
                continue

            trade = simulate_trade(
                frame,
                signal_index,
                "decision_panel",
                setup=setup,
                profile=profile,
            )
            if trade is None:
                continue

            entry_time = pd.Timestamp(trade["entry_time"])
            exit_time = pd.Timestamp(trade["exit_time"])
            # Exact leakage guard used by the research optimizer: the complete
            # trade must remain inside the untouched OOS segment.
            if _segment(entry_time, cut1, cut2, cut3) != "oos":
                continue
            if _segment(exit_time, cut1, cut2, cut3) != "oos":
                continue

            item = dict(trade)
            item.update({
                "symbol": symbol,
                "signal_time": signal_time.isoformat(),
                "setup": setup,
            })
            trades.append(item)
            exit_pos = index_lookup.get(exit_time)
            if exit_pos is not None:
                last_exit = int(exit_pos)

    return trades, {
        "version": "decision-panel-v6.4.5-production-fixed-costs",
        "setup": setup,
        "entry_event": SETUP_CONFIG[setup]["event_name"],
        "profile": asdict(profile),
        "split": {
            "train_end": cut1.isoformat(),
            "validation1_end": cut2.isoformat(),
            "validation2_end": cut3.isoformat(),
            "oos_start": cut3.isoformat(),
            "rule": "same 50/20/15/15 chronological event-date split used in research",
            "leakage_guard": "signal, entry and exit must remain inside OOS",
        },
        "universe": meta,
    }


def _net_r(trade: dict[str, Any], commission_bps: float, slippage_bps: float) -> float:
    entry = float(trade["entry"])
    risk = float(trade["plan"]["risk_per_share"])
    gross_r = float(trade["realized_r"])
    if not np.isfinite(entry) or not np.isfinite(risk) or risk <= 0:
        return gross_r

    # realized_r already represents the weighted realized exit of partials.
    gross_weighted_exit = entry + gross_r * risk
    commission = max(float(commission_bps), 0.0) / 10000.0
    slippage = max(float(slippage_bps), 0.0) / 10000.0
    buy_fill = entry * (1.0 + slippage)
    sell_fill = gross_weighted_exit * (1.0 - slippage)
    net_pnl = sell_fill * (1.0 - commission) - buy_fill * (1.0 + commission)
    return net_pnl / risk


def cost_metrics(trades: list[dict[str, Any]], commission_bps: float, slippage_bps: float) -> dict[str, Any]:
    adjusted: list[dict[str, Any]] = []
    for trade in trades:
        item = dict(trade)
        item["realized_r"] = _net_r(trade, commission_bps, slippage_bps)
        item["winner"] = item["realized_r"] > 0
        adjusted.append(item)
    return robust_metrics(adjusted)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--setup", choices=tuple(SETUP_CONFIG), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    trades, payload = replay_oos(args.db, args.setup)
    payload["scenarios"] = {}
    for name, commission_bps, slippage_bps in SCENARIOS:
        payload["scenarios"][name] = {
            "commission_bps_per_side": commission_bps,
            "slippage_bps_per_side": slippage_bps,
            "metrics": cost_metrics(trades, commission_bps, slippage_bps),
        }

    moderate = payload["scenarios"]["moderate_10_10bps"]["metrics"]
    required = int(SETUP_CONFIG[args.setup]["min_trades"])
    payload["robust_sample"] = int(moderate.get("trades", 0)) >= required
    payload["moderate_cost_pass"] = bool(
        payload["robust_sample"]
        and moderate.get("expectancy_r", 0.0) > 0.0
        and moderate.get("profit_factor", 0.0) > 1.0
    )
    payload["production_note"] = (
        "PASS" if payload["moderate_cost_pass"] else
        "INSUFFICIENT_SAMPLE" if not payload["robust_sample"] else
        "FAIL_COST_ROBUSTNESS"
    )

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
