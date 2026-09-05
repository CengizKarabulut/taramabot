"""Replay one selected timeframe profile on untouched OOS with explicit costs.

The selected profile comes from historical_exit_optimize.py.  This module does
not optimize anything: it reconstructs the same chronological split, replays
only the untouched test slice and verifies gross-metric parity before applying
commission/slippage scenarios.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from historical_exit_optimize import _aggregate_profile, _profile_from_dict, prepare_walk_forward
from strategy_optimizer import robust_metrics


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
    adjusted = []
    for trade in trades:
        item = dict(trade)
        item["realized_r"] = _net_r(trade, commission_bps, slippage_bps)
        item["winner"] = item["realized_r"] > 0
        adjusted.append(item)
    return robust_metrics(adjusted)


def _close_enough(left: Any, right: Any, tolerance: float = 1e-7) -> bool:
    try:
        a = float(left)
        b = float(right)
    except (TypeError, ValueError):
        return left == right
    if np.isinf(a) and np.isinf(b):
        return True
    return abs(a - b) <= tolerance * max(1.0, abs(a), abs(b))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--optimizer-json", required=True)
    parser.add_argument("--warmup", type=int, default=240)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    optimizer = json.loads(Path(args.optimizer_json).read_text(encoding="utf-8-sig"))
    selected = optimizer.get("selected")
    expected_gross = optimizer.get("out_of_sample")
    if not selected or not selected.get("profile") or expected_gross is None:
        payload = {
            "strategy": args.strategy,
            "period": args.period,
            "status": "NO_VALIDATED_PROFILE",
            "moderate_cost_pass": False,
            "optimizer": optimizer,
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    profile = _profile_from_dict(selected["profile"])
    datasets, metadata = prepare_walk_forward(
        args.db,
        args.strategy,
        period=args.period,
        warmup=args.warmup,
        max_symbols=0,
        purge_bars=0,
    )
    trades, gross_metrics = _aggregate_profile(datasets["test"], args.strategy, profile)

    parity_fields = ("trades", "wins", "losses", "profit_factor", "expectancy_r")
    parity = all(_close_enough(gross_metrics.get(key), expected_gross.get(key)) for key in parity_fields)

    scenarios = {}
    for name, commission_bps, slippage_bps in SCENARIOS:
        scenarios[name] = {
            "commission_bps_per_side": commission_bps,
            "slippage_bps_per_side": slippage_bps,
            "metrics": _cost_metrics(trades, commission_bps, slippage_bps),
        }

    moderate = scenarios["moderate_10_10bps"]["metrics"]
    robust_sample = int(moderate.get("trades", 0)) >= 30
    pass_gate = bool(
        parity
        and robust_sample
        and float(moderate.get("expectancy_r", 0.0)) > 0.0
        and float(moderate.get("profit_factor", 0.0)) >= 1.20
    )

    payload = {
        "version": "timeframe-oos-cost-v1",
        "strategy": args.strategy,
        "period": args.period,
        "profile": selected["profile"],
        "split": metadata,
        "gross_parity": parity,
        "expected_gross_oos": expected_gross,
        "replayed_gross_oos": gross_metrics,
        "scenarios": scenarios,
        "robust_sample": robust_sample,
        "moderate_cost_pass": pass_gate,
        "production_note": "PASS" if pass_gate else "FAIL_OR_INSUFFICIENT",
    }

    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if parity else 2


if __name__ == "__main__":
    raise SystemExit(main())
