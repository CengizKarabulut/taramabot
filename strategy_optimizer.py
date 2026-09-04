"""Robust strategy exit-profile ranking.

Optimization goal follows the project priority: reliable win-rate first, but
only among configurations that have positive expectancy, adequate sample size
and acceptable profit factor.  Wilson lower bound is used instead of raw win
rate so tiny samples cannot dominate the ranking.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from strategy_exit_profiles import ExitProfile, get_exit_profile
from trade_backtester import simulate_trade, summarize_trades


def wilson_lower_bound(wins: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = wins / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    centre = p + z2 / (2.0 * total)
    adjustment = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * total)) / total)
    return max(0.0, (centre - adjustment) / denominator) * 100.0


def max_drawdown_r(trades: list[dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    curve = np.cumsum([float(trade["realized_r"]) for trade in trades])
    peaks = np.maximum.accumulate(np.insert(curve, 0, 0.0))[1:]
    drawdowns = peaks - curve
    return float(drawdowns.max()) if len(drawdowns) else 0.0


def robust_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_trades(trades)
    total = int(summary.get("trades", 0))
    wins = int(summary.get("wins", 0))
    winners = [float(t["realized_r"]) for t in trades if float(t["realized_r"]) > 0]
    losers = [float(t["realized_r"]) for t in trades if float(t["realized_r"]) < 0]
    summary.update({
        "wilson_win_rate": wilson_lower_bound(wins, total),
        "avg_win_r": float(np.mean(winners)) if winners else 0.0,
        "avg_loss_r": float(np.mean(losers)) if losers else 0.0,
        "payoff_ratio": (float(np.mean(winners)) / abs(float(np.mean(losers)))) if winners and losers and np.mean(losers) != 0 else 0.0,
        "max_drawdown_r": max_drawdown_r(trades),
        "mfe_r_p50": float(np.median([t["mfe_r"] for t in trades])) if trades else 0.0,
        "mfe_r_p75": float(np.quantile([t["mfe_r"] for t in trades], .75)) if trades else 0.0,
        "mae_r_p50": float(np.median([t["mae_r"] for t in trades])) if trades else 0.0,
        "mae_r_p25": float(np.quantile([t["mae_r"] for t in trades], .25)) if trades else 0.0,
    })
    return summary


def eligible(metrics: dict[str, Any], *, min_trades: int = 30, min_pf: float = 1.20, min_expectancy_r: float = 0.0) -> bool:
    pf = float(metrics.get("profit_factor", 0.0))
    return (
        int(metrics.get("trades", 0)) >= min_trades
        and float(metrics.get("expectancy_r", 0.0)) > min_expectancy_r
        and (math.isinf(pf) or pf >= min_pf)
    )


def ranking_score(metrics: dict[str, Any], *, min_trades: int = 30, min_pf: float = 1.20) -> float:
    """Win-rate leads; expectancy/PF/sample/drawdown act as quality terms."""
    if not eligible(metrics, min_trades=min_trades, min_pf=min_pf):
        return -1e9
    wilson = float(metrics["wilson_win_rate"])
    expectancy = max(-2.0, min(3.0, float(metrics.get("expectancy_r", 0.0))))
    pf = float(metrics.get("profit_factor", 0.0))
    pf_component = 3.0 if math.isinf(pf) else max(0.0, min(3.0, pf - 1.0))
    sample = min(4.0, math.log10(max(10, int(metrics["trades"]))) * 1.5)
    drawdown_penalty = min(15.0, float(metrics.get("max_drawdown_r", 0.0)) * 0.35)
    return wilson + expectancy * 5.0 + pf_component * 2.0 + sample - drawdown_penalty


def parameter_grid(base: ExitProfile) -> Iterable[ExitProfile]:
    stop_modes = tuple(dict.fromkeys((base.stop_mode, "swing", "hybrid_tight", "hybrid_wide")))
    atr_mults = tuple(sorted(set((max(.75, base.atr_mult-.25), base.atr_mult, base.atr_mult+.25))))
    tp_sets = (
        (0.8, 1.5, 2.4),
        (1.0, 1.8, 2.8),
        (1.0, 2.0, 3.0),
        (1.0, 2.0, 3.5),
        (1.2, 2.2, 3.5),
    )
    trail_mults = tuple(sorted(set((max(1.4, base.trailing_atr_mult-.2), base.trailing_atr_mult, base.trailing_atr_mult+.2))))
    for stop_mode in stop_modes:
        for atr_mult in atr_mults:
            for tp1, tp2, tp3 in tp_sets:
                if not (tp1 < tp2 < tp3):
                    continue
                for trail in trail_mults:
                    yield replace(
                        base,
                        stop_mode=stop_mode,
                        atr_mult=atr_mult,
                        tp1_r=tp1,
                        tp2_r=tp2,
                        tp3_r=tp3,
                        trailing_atr_mult=trail,
                    )


def evaluate_profile(
    frame: pd.DataFrame,
    signal_indexes: list[int],
    strategy: str,
    profile: ExitProfile,
    *,
    setup: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trades = []
    last_exit_position = -1
    for signal_index in signal_indexes:
        # One strategy/symbol/period has one open position at a time.
        if signal_index <= last_exit_position:
            continue
        result = simulate_trade(frame, signal_index, strategy, setup=setup, profile=profile)
        if result is None:
            continue
        trades.append(result)
        try:
            exit_label = pd.Timestamp(result["exit_time"])
            positions = np.flatnonzero(pd.to_datetime(frame.index) == exit_label)
            if len(positions):
                last_exit_position = int(positions[-1])
        except Exception:
            last_exit_position = signal_index
    return trades, robust_metrics(trades)


def optimize_profiles(
    datasets: list[tuple[pd.DataFrame, list[int]]],
    strategy: str,
    *,
    setup: str | None = None,
    min_trades: int = 30,
    min_pf: float = 1.20,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    base = get_exit_profile(strategy, setup=setup)
    ranked = []
    for profile in parameter_grid(base):
        all_trades = []
        for frame, signal_indexes in datasets:
            trades, _ = evaluate_profile(frame, signal_indexes, strategy, profile, setup=setup)
            all_trades.extend(trades)
        metrics = robust_metrics(all_trades)
        ranked.append({
            "score": ranking_score(metrics, min_trades=min_trades, min_pf=min_pf),
            "eligible": eligible(metrics, min_trades=min_trades, min_pf=min_pf),
            "profile": asdict(profile),
            "metrics": metrics,
        })
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return ranked[:top_n]


def split_walk_forward(items: list[Any], train_ratio: float = .60, validation_ratio: float = .20) -> tuple[list[Any], list[Any], list[Any]]:
    """Chronological train/validation/out-of-sample split."""
    count = len(items)
    train_end = int(count * train_ratio)
    validation_end = train_end + int(count * validation_ratio)
    return items[:train_end], items[train_end:validation_end], items[validation_end:]


def select_with_validation(train_ranked: list[dict[str, Any]], validation_results: dict[str, dict[str, Any]], *, min_trades: int = 15, min_pf: float = 1.10) -> dict[str, Any] | None:
    """Choose among top train candidates only when validation remains positive."""
    candidates = []
    for row in train_ranked:
        key = json.dumps(row["profile"], sort_keys=True)
        validation = validation_results.get(key)
        if not validation:
            continue
        if eligible(validation, min_trades=min_trades, min_pf=min_pf):
            candidates.append((ranking_score(validation, min_trades=min_trades, min_pf=min_pf), row, validation))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, train, validation = candidates[0]
    return {"profile": train["profile"], "train": train["metrics"], "validation": validation}


def write_ranking(path: str | Path, rows: list[dict[str, Any]]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Strategy exit profile ranking helper")
    parser.add_argument("--trades-json", required=True, help="JSON list of completed trade results")
    parser.add_argument("--output", default="reports/strategy_metrics.json")
    args = parser.parse_args()
    trades = json.loads(Path(args.trades_json).read_text(encoding="utf-8-sig"))
    metrics = robust_metrics(trades)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
