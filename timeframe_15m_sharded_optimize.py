"""Exact sharded execution helper for the expensive 15m walk-forward research.

This module does not change the optimizer's research logic. It only partitions
`strategy_optimizer.parameter_grid()` across independent GitHub Actions jobs,
then reconstructs the same global train ranking, validation selection and
untouched OOS evaluation used by `historical_exit_optimize.py`.

Why this exists:
The full 15m EMA/new_scan jobs exceeded the six-hour hosted-runner timeout when
all exit profiles were evaluated serially. Sharding changes orchestration only;
all symbols, signals, profiles, split rules and eligibility gates stay intact.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from historical_exit_optimize import (
    _aggregate_profile,
    _profile_from_dict,
    prepare_walk_forward,
)
from historical_replay import STRATEGIES
from strategy_exit_profiles import get_exit_profile
from strategy_optimizer import eligible, parameter_grid, ranking_score


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _finite_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return -1e9
    return score


def run_shard(args: argparse.Namespace) -> int:
    if args.shard_count <= 0:
        raise ValueError("shard-count must be positive")
    if not 0 <= args.shard_index < args.shard_count:
        raise ValueError("shard-index must be in [0, shard-count)")

    datasets, metadata = prepare_walk_forward(
        args.db,
        args.strategy,
        period=args.period,
        warmup=args.warmup,
        max_symbols=0,
        purge_bars=0,
    )

    profiles = list(parameter_grid(get_exit_profile(args.strategy)))
    assigned = [
        (index, profile)
        for index, profile in enumerate(profiles)
        if index % args.shard_count == args.shard_index
    ]

    rows: list[dict[str, Any]] = []
    for ordinal, (profile_index, profile) in enumerate(assigned, start=1):
        print(
            f"[{args.strategy}] shard {args.shard_index + 1}/{args.shard_count} "
            f"profile {ordinal}/{len(assigned)} (grid #{profile_index})",
            flush=True,
        )
        _, metrics = _aggregate_profile(datasets["train"], args.strategy, profile)
        rows.append(
            {
                "profile_index": profile_index,
                "profile": asdict(profile),
                "metrics": metrics,
                "eligible": eligible(metrics, min_trades=30, min_pf=1.20),
                "score": ranking_score(metrics, min_trades=30, min_pf=1.20),
            }
        )

    # Original optimizer uses Python's stable descending sort. For cross-shard
    # reconstruction, profile_index restores the original parameter-grid order
    # whenever scores tie (notably ineligible rows at -1e9).
    rows.sort(key=lambda row: (-_finite_score(row["score"]), int(row["profile_index"])))

    payload = {
        "version": "timeframe-15m-sharded-v1",
        "mode": "train_shard",
        "strategy": args.strategy,
        "period": args.period,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "grid_size": len(profiles),
        "assigned_profiles": len(assigned),
        "metadata": metadata,
        "rows": rows,
    }
    _write_json(args.output, payload)
    print(
        json.dumps(
            {
                "strategy": args.strategy,
                "shard": args.shard_index,
                "grid_size": len(profiles),
                "assigned_profiles": len(assigned),
                "best_score": rows[0]["score"] if rows else None,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


def _load_shard_rows(
    shards_dir: str | Path,
    strategy: str,
    expected_shards: int,
    expected_grid_size: int,
) -> list[dict[str, Any]]:
    root = Path(shards_dir)
    files = sorted(root.rglob(f"{strategy}-shard-*.json"))
    if not files:
        raise RuntimeError(f"No shard JSON files found for {strategy} under {root}")

    rows_by_index: dict[int, dict[str, Any]] = {}
    seen_shards: set[int] = set()
    declared_grid_sizes: set[int] = set()

    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if payload.get("strategy") != strategy:
            continue
        shard_index = int(payload["shard_index"])
        shard_count = int(payload["shard_count"])
        if shard_count != expected_shards:
            raise RuntimeError(
                f"Shard-count mismatch in {path}: {shard_count} != {expected_shards}"
            )
        if shard_index in seen_shards:
            raise RuntimeError(f"Duplicate shard {shard_index} for {strategy}")
        seen_shards.add(shard_index)
        declared_grid_sizes.add(int(payload["grid_size"]))
        for row in payload.get("rows", []):
            profile_index = int(row["profile_index"])
            if profile_index in rows_by_index:
                raise RuntimeError(f"Duplicate profile index {profile_index} for {strategy}")
            rows_by_index[profile_index] = row

    required_shards = set(range(expected_shards))
    if seen_shards != required_shards:
        missing = sorted(required_shards - seen_shards)
        extra = sorted(seen_shards - required_shards)
        raise RuntimeError(f"Shard set mismatch for {strategy}; missing={missing}, extra={extra}")
    if declared_grid_sizes != {expected_grid_size}:
        raise RuntimeError(
            f"Grid-size mismatch for {strategy}: shards={sorted(declared_grid_sizes)}, "
            f"current={expected_grid_size}"
        )
    if set(rows_by_index) != set(range(expected_grid_size)):
        missing = sorted(set(range(expected_grid_size)) - set(rows_by_index))
        extra = sorted(set(rows_by_index) - set(range(expected_grid_size)))
        raise RuntimeError(
            f"Profile coverage mismatch for {strategy}; missing={missing[:10]}, extra={extra[:10]}"
        )

    return [rows_by_index[index] for index in range(expected_grid_size)]


def run_merge(args: argparse.Namespace) -> int:
    current_profiles = list(parameter_grid(get_exit_profile(args.strategy)))
    all_rows = _load_shard_rows(
        args.shards_dir,
        args.strategy,
        args.shard_count,
        len(current_profiles),
    )

    # Reconstruct the exact global train ranking from all grid rows.
    all_rows.sort(key=lambda row: (-_finite_score(row["score"]), int(row["profile_index"])))
    train_ranked = all_rows[: args.top_train]

    # Rebuild the same deterministic chronological datasets once for validation
    # and OOS. This is intentionally not approximated or sampled.
    datasets, metadata = prepare_walk_forward(
        args.db,
        args.strategy,
        period=args.period,
        warmup=args.warmup,
        max_symbols=0,
        purge_bars=0,
    )

    validation_rows: list[dict[str, Any]] = []
    for ordinal, row in enumerate(train_ranked, start=1):
        print(
            f"[{args.strategy}] validation {ordinal}/{len(train_ranked)} "
            f"(grid #{row['profile_index']})",
            flush=True,
        )
        profile = _profile_from_dict(row["profile"])
        _, validation_metrics = _aggregate_profile(
            datasets["validation"], args.strategy, profile
        )
        validation_rows.append(
            {
                "profile": row["profile"],
                "train": row["metrics"],
                "train_score": row["score"],
                "validation": validation_metrics,
                "validation_eligible": eligible(
                    validation_metrics, min_trades=15, min_pf=1.10
                ),
                "validation_score": ranking_score(
                    validation_metrics, min_trades=15, min_pf=1.10
                ),
            }
        )

    # Stable sort preserves train rank on exact validation-score ties, matching
    # historical_exit_optimize.optimize_walk_forward().
    validation_rows.sort(
        key=lambda item: _finite_score(item["validation_score"]), reverse=True
    )
    selected = next(
        (row for row in validation_rows if row["validation_eligible"]), None
    )

    test_metrics: dict[str, Any] | None = None
    if selected is not None:
        print(f"[{args.strategy}] evaluating untouched OOS", flush=True)
        profile = _profile_from_dict(selected["profile"])
        _, test_metrics = _aggregate_profile(datasets["test"], args.strategy, profile)

    metadata = dict(metadata)
    metadata["execution"] = {
        "mode": "exact_sharded_parameter_grid",
        "shard_count": args.shard_count,
        "profile_grid_size": len(current_profiles),
        "top_train_validated": args.top_train,
        "logic_equivalent_to": "historical_exit_optimize.optimize_walk_forward",
    }

    # Preserve the canonical optimizer output schema expected by
    # historical_period_cost_sensitivity.py.
    canonical_train = [
        {
            "score": row["score"],
            "eligible": row["eligible"],
            "profile": row["profile"],
            "metrics": row["metrics"],
        }
        for row in train_ranked[:10]
    ]
    result = {
        "metadata": metadata,
        "selected": selected,
        "out_of_sample": test_metrics,
        "top_validation": validation_rows[:10],
        "top_train": canonical_train,
    }
    _write_json(args.output, result)
    print(
        json.dumps(
            {
                "metadata": metadata,
                "selected": selected,
                "out_of_sample": test_metrics,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    shard = subparsers.add_parser("shard", help="Evaluate one train-grid shard")
    shard.add_argument("--db", required=True)
    shard.add_argument("--strategy", choices=STRATEGIES, required=True)
    shard.add_argument("--period", default="15m")
    shard.add_argument("--warmup", type=int, default=240)
    shard.add_argument("--shard-index", type=int, required=True)
    shard.add_argument("--shard-count", type=int, required=True)
    shard.add_argument("--output", required=True)
    shard.set_defaults(func=run_shard)

    merge = subparsers.add_parser(
        "merge", help="Rebuild global train rank, validate top candidates and run OOS"
    )
    merge.add_argument("--db", required=True)
    merge.add_argument("--strategy", choices=STRATEGIES, required=True)
    merge.add_argument("--period", default="15m")
    merge.add_argument("--warmup", type=int, default=240)
    merge.add_argument("--shards-dir", required=True)
    merge.add_argument("--shard-count", type=int, required=True)
    merge.add_argument("--top-train", type=int, default=20)
    merge.add_argument("--output", required=True)
    merge.set_defaults(func=run_merge)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
