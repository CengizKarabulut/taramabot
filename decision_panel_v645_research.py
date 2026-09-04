"""Research-only Karar Paneli v6.4.5 candidate selector.

Nothing in this module changes production signals.  It uses the long-history
1D snapshot to test two evidence-driven entry changes:

* BREAKOUT: make the SMA20/ATR extension limit setup-specific while preserving
  the existing score, RVOL, momentum and candle-quality requirements.
* TREND DEVAMI: keep the exact v6.4.4 trend state, then require an orderly
  continuation (limited RVOL/extension, positive 10-day progress and optional
  Bollinger expansion) to reduce climax/mature-trend entries.

Candidate entry rules are selected chronologically.  Exit parameters are then
optimized only on development data and validated before a final untouched OOS
measurement.  This is deliberately conservative; a candidate that cannot
survive the sample/expectancy/PF gates is returned as NOT SELECTED.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from decision_panel_breakout_diagnostic import gate_frame
from historical_decision_panel_v644 import _normalize, decision_series
from market_data_store import MarketDataStore
from strategy_exit_profiles import ExitProfile, get_exit_profile
from strategy_optimizer import eligible, parameter_grid, ranking_score, robust_metrics
from trade_backtester import simulate_trade


SETUPS = ("BREAKOUT", "TREND DEVAMI")


@dataclass(frozen=True)
class EntryCandidate:
    name: str
    setup: str
    breakout_max_dist_atr: float | None = None
    trend_max_rvol: float | None = None
    trend_max_dist_atr: float | None = None
    trend_min_ret10_pct: float | None = None
    trend_require_bb_rising: bool = False


def entry_candidates(setup: str) -> list[EntryCandidate]:
    if setup == "BREAKOUT":
        return [
            EntryCandidate("breakout_dist_1.50", setup, breakout_max_dist_atr=1.50),
            EntryCandidate("breakout_dist_1.75", setup, breakout_max_dist_atr=1.75),
            EntryCandidate("breakout_dist_2.00", setup, breakout_max_dist_atr=2.00),
        ]

    if setup == "TREND DEVAMI":
        result = [EntryCandidate("trend_v644_baseline", setup)]
        # Small, interpretable grid.  Every axis has an economic meaning:
        # RVOL caps climax participation, distance caps late extension,
        # ret10 demands actual trend progress, BB expansion confirms activity.
        for max_rvol in (1.8, 2.0, 2.2):
            for max_dist in (1.10, 1.25, 1.40):
                for min_ret10 in (3.0, 5.0, 7.0):
                    for bb_rising in (False, True):
                        suffix = "_bb" if bb_rising else ""
                        result.append(
                            EntryCandidate(
                                name=f"trend_rv{max_rvol:.1f}_d{max_dist:.2f}_r10{min_ret10:.0f}{suffix}",
                                setup=setup,
                                trend_max_rvol=max_rvol,
                                trend_max_dist_atr=max_dist,
                                trend_min_ret10_pct=min_ret10,
                                trend_require_bb_rising=bb_rising,
                            )
                        )
        return result
    raise ValueError(f"Desteklenmeyen setup: {setup}")


def _fresh(condition: pd.Series) -> pd.Series:
    condition = condition.fillna(False).astype(bool)
    return condition & (~condition.shift(1).fillna(False))


def _feature_frame(clean: pd.DataFrame) -> pd.DataFrame:
    c = clean["close"]
    v = clean["volume"]
    sma20 = c.rolling(20).mean()
    # Wilder ATR is already reflected by the diagnostic frame.  The ATR-scaled
    # SMA20 distance is therefore read from gate_frame for exact parity.
    avg_volume = v.rolling(10).mean().shift(1)
    rvol = v / avg_volume.replace(0, np.nan)
    ret10 = (c / c.shift(10) - 1.0) * 100.0
    bb_basis = c.rolling(20).mean()
    bb_dev = 2.0 * c.rolling(20).std(ddof=0)
    bb_width = (2.0 * bb_dev) / bb_basis.replace(0, np.nan) * 100.0
    return pd.DataFrame(
        {
            "dist_pct": (c / sma20.replace(0, np.nan) - 1.0) * 100.0,
            "rvol": rvol,
            "ret10_pct": ret10,
            "bb_width_rising": bb_width > bb_width.shift(1),
        },
        index=clean.index,
    )


def candidate_mask(
    clean: pd.DataFrame,
    candidate: EntryCandidate,
    *,
    min_score: int = 75,
) -> pd.Series:
    features = _feature_frame(clean)

    if candidate.setup == "BREAKOUT":
        gates = gate_frame(clean, min_score=min_score)
        # Preserve every exact v6.4.4 breakout gate except the common 1.50 ATR
        # distance ceiling.  Percent distance stays capped at 7% and RVOL stays
        # at the original breakout threshold (>1.50).
        base = (
            gates["history_ready"]
            & gates["structure_core"]
            & gates["participation_ok"]
            & gates["score_ok"]
            & gates["breakout20"]
            & gates["short_trend_core"]
            & gates["breakout_momentum_ok"]
            & gates["breakout_quality_ok"]
        )
        limit = float(candidate.breakout_max_dist_atr or 1.50)
        distance = (
            (features["dist_pct"] >= 0.0)
            & (features["dist_pct"] <= 7.0)
            & (gates["dist_atr"] >= 0.0)
            & (gates["dist_atr"] <= limit)
        )
        return _fresh(base & distance)

    state = decision_series(clean, min_score=min_score)
    trend_state = state["trend"].fillna(False)
    if candidate.name == "trend_v644_baseline":
        return _fresh(trend_state)

    gates = gate_frame(clean, min_score=min_score)
    orderly = trend_state.copy()
    if candidate.trend_max_rvol is not None:
        orderly &= features["rvol"] <= float(candidate.trend_max_rvol)
    if candidate.trend_max_dist_atr is not None:
        orderly &= gates["dist_atr"] <= float(candidate.trend_max_dist_atr)
    if candidate.trend_min_ret10_pct is not None:
        orderly &= features["ret10_pct"] >= float(candidate.trend_min_ret10_pct)
    if candidate.trend_require_bb_rising:
        orderly &= features["bb_width_rising"].fillna(False)
    return _fresh(orderly)


def load_universe(
    database: str,
    setup: str,
    *,
    min_score: int = 75,
) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, list[int]]], dict[str, Any]]:
    candidates = entry_candidates(setup)
    frames: dict[str, pd.DataFrame] = {}
    event_map: dict[str, dict[str, list[int]]] = {candidate.name: {} for candidate in candidates}
    errors = 0

    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", "1D")
        for number, symbol in enumerate(symbols, start=1):
            raw = store.load_dataframe(symbol, "BIST", "1D", limit=0)
            if raw is None or len(raw) < 280:
                continue
            try:
                clean = _normalize(raw)
                frames[symbol] = clean
                for candidate in candidates:
                    mask = candidate_mask(clean, candidate, min_score=min_score)
                    indexes = [
                        int(i)
                        for i in np.flatnonzero(mask.to_numpy(dtype=bool))
                        if int(i) < len(clean) - 1
                    ]
                    if indexes:
                        event_map[candidate.name][symbol] = indexes
                if number % 50 == 0 or number == len(symbols):
                    counts = ", ".join(
                        f"{c.name}={sum(len(v) for v in event_map[c.name].values())}"
                        for c in candidates[:3]
                    )
                    print(f"[{number}/{len(symbols)}] {setup} · {counts}")
            except Exception as exc:
                errors += 1
                print(f"[{number}/{len(symbols)}] {symbol}: HATA {exc}")

    totals = {name: sum(len(values) for values in symbols.values()) for name, symbols in event_map.items()}
    return frames, event_map, {"symbols_loaded": len(frames), "errors": errors, "candidate_events": totals}


def chronological_cuts(
    frames: dict[str, pd.DataFrame],
    event_map: dict[str, dict[str, list[int]]],
) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    # Use the union of candidate event times.  This keeps all candidates on the
    # exact same market periods rather than letting each threshold choose its
    # own convenient split dates.
    times: set[pd.Timestamp] = set()
    for by_symbol in event_map.values():
        for symbol, indexes in by_symbol.items():
            frame = frames[symbol]
            times.update(pd.Timestamp(frame.index[i]) for i in indexes)
    ordered = sorted(times)
    if len(ordered) < 40:
        raise RuntimeError(f"Aday havuzunda yalnız {len(ordered)} benzersiz sinyal tarihi var")

    def cut(frac: float) -> pd.Timestamp:
        return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * frac) - 1))]

    # 50% entry-development, 20% entry-validation, 15% exit-validation,
    # 15% final untouched OOS.
    return cut(0.50), cut(0.70), cut(0.85)


def _segment_for_time(
    value: pd.Timestamp,
    cut1: pd.Timestamp,
    cut2: pd.Timestamp,
    cut3: pd.Timestamp,
) -> str:
    if value <= cut1:
        return "train"
    if value <= cut2:
        return "validation1"
    if value <= cut3:
        return "validation2"
    return "oos"


def evaluate_candidate(
    frames: dict[str, pd.DataFrame],
    events: dict[str, list[int]],
    setup: str,
    profile: ExitProfile,
    *,
    cut1: pd.Timestamp,
    cut2: pd.Timestamp,
    cut3: pd.Timestamp,
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "train": [], "validation1": [], "validation2": [], "oos": []
    }

    for symbol, indexes in events.items():
        frame = frames[symbol]
        index_lookup = {pd.Timestamp(value): pos for pos, value in enumerate(frame.index)}
        last_exit_by_segment = {key: -1 for key in buckets}
        for signal_index in indexes:
            signal_time = pd.Timestamp(frame.index[signal_index])
            segment = _segment_for_time(signal_time, cut1, cut2, cut3)
            if signal_index <= last_exit_by_segment[segment]:
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
            # A completed trade must stay fully inside the same chronological
            # segment.  This prevents outcome information crossing boundaries.
            if _segment_for_time(entry_time, cut1, cut2, cut3) != segment:
                continue
            if _segment_for_time(exit_time, cut1, cut2, cut3) != segment:
                continue
            trade = dict(trade)
            trade.update({"symbol": symbol, "signal_time": signal_time.isoformat()})
            buckets[segment].append(trade)
            last_exit_by_segment[segment] = int(index_lookup.get(exit_time, signal_index))

    return {name: robust_metrics(trades) for name, trades in buckets.items()}


def _entry_minimums(setup: str) -> tuple[int, int]:
    return (20, 8) if setup == "BREAKOUT" else (60, 25)


def select_entry(
    frames: dict[str, pd.DataFrame],
    event_map: dict[str, dict[str, list[int]]],
    setup: str,
    candidates: Iterable[EntryCandidate],
    cuts: tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp],
) -> tuple[EntryCandidate | None, list[dict[str, Any]]]:
    train_min, val_min = _entry_minimums(setup)
    base_profile = get_exit_profile("decision_panel", setup=setup)
    rows: list[dict[str, Any]] = []
    cut1, cut2, cut3 = cuts

    for candidate in candidates:
        metrics = evaluate_candidate(
            frames,
            event_map[candidate.name],
            setup,
            base_profile,
            cut1=cut1,
            cut2=cut2,
            cut3=cut3,
        )
        train = metrics["train"]
        val1 = metrics["validation1"]
        train_ok = eligible(train, min_trades=train_min, min_pf=1.05)
        val_ok = eligible(val1, min_trades=val_min, min_pf=1.00)
        row = {
            "candidate": asdict(candidate),
            "train": train,
            "validation1": val1,
            # validation2/OOS are deliberately hidden from entry ranking.  They
            # are only exposed after a candidate has been selected.
            "train_eligible": train_ok,
            "validation1_eligible": val_ok,
            "validation1_score": ranking_score(val1, min_trades=val_min, min_pf=1.00),
        }
        rows.append(row)

    rows.sort(
        key=lambda row: (
            bool(row["train_eligible"] and row["validation1_eligible"]),
            row["validation1_score"],
        ),
        reverse=True,
    )
    selected_row = next(
        (row for row in rows if row["train_eligible"] and row["validation1_eligible"]),
        None,
    )
    if selected_row is None:
        return None, rows
    selected = EntryCandidate(**selected_row["candidate"])
    return selected, rows


def optimize_exit(
    frames: dict[str, pd.DataFrame],
    events: dict[str, list[int]],
    setup: str,
    cuts: tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp],
) -> tuple[ExitProfile | None, list[dict[str, Any]]]:
    cut1, cut2, cut3 = cuts
    base = get_exit_profile("decision_panel", setup=setup)
    dev_min = 30 if setup == "BREAKOUT" else 80
    val2_min = 8 if setup == "BREAKOUT" else 20
    ranked: list[dict[str, Any]] = []

    for profile in parameter_grid(base):
        metrics = evaluate_candidate(
            frames,
            events,
            setup,
            profile,
            cut1=cut1,
            cut2=cut2,
            cut3=cut3,
        )
        # Exit development uses train + validation1, but OOS remains untouched.
        # Merge by weighted sums is unsafe for PF/DD, so evaluate eligibility on
        # train and validation1 separately and rank primarily by validation1.
        train = metrics["train"]
        val1 = metrics["validation1"]
        val2 = metrics["validation2"]
        dev_ok = (
            train.get("trades", 0) >= dev_min
            and train.get("expectancy_r", 0) > 0
            and train.get("profit_factor", 0) >= 1.05
            and val1.get("expectancy_r", 0) > 0
            and val1.get("profit_factor", 0) >= 1.00
        )
        val2_ok = eligible(val2, min_trades=val2_min, min_pf=1.00)
        score = ranking_score(val2, min_trades=val2_min, min_pf=1.00)
        ranked.append(
            {
                "profile": asdict(profile),
                "train": train,
                "validation1": val1,
                "validation2": val2,
                "development_eligible": dev_ok,
                "validation2_eligible": val2_ok,
                "validation2_score": score,
            }
        )

    ranked.sort(
        key=lambda row: (
            bool(row["development_eligible"] and row["validation2_eligible"]),
            row["validation2_score"],
        ),
        reverse=True,
    )
    selected_row = next(
        (row for row in ranked if row["development_eligible"] and row["validation2_eligible"]),
        None,
    )
    if selected_row is None:
        return None, ranked
    data = dict(selected_row["profile"])
    data["tp_allocations"] = tuple(data["tp_allocations"])
    return ExitProfile(**data), ranked


def research(database: str, setup: str, *, min_score: int = 75) -> dict[str, Any]:
    candidates = entry_candidates(setup)
    frames, event_map, universe_meta = load_universe(database, setup, min_score=min_score)
    cuts = chronological_cuts(frames, event_map)
    selected_entry, entry_rows = select_entry(frames, event_map, setup, candidates, cuts)

    result: dict[str, Any] = {
        "version": "v6.4.5-research",
        "setup": setup,
        "min_score": min_score,
        "universe": universe_meta,
        "split": {
            "train_end": cuts[0].isoformat(),
            "validation1_end": cuts[1].isoformat(),
            "validation2_end": cuts[2].isoformat(),
            "rule": "50/20/15/15 chronological candidate-event dates",
            "leakage_guard": "entry_and_exit_must_stay_inside_same_segment",
        },
        "selected_entry": asdict(selected_entry) if selected_entry else None,
        "entry_ranking": entry_rows[:20],
        "selected_exit": None,
        "exit_ranking": [],
        "final_oos": None,
    }
    if selected_entry is None:
        return result

    selected_exit, exit_rows = optimize_exit(
        frames,
        event_map[selected_entry.name],
        setup,
        cuts,
    )
    result["selected_exit"] = asdict(selected_exit) if selected_exit else None
    result["exit_ranking"] = exit_rows[:20]
    if selected_exit is None:
        return result

    final_metrics = evaluate_candidate(
        frames,
        event_map[selected_entry.name],
        setup,
        selected_exit,
        cut1=cuts[0],
        cut2=cuts[1],
        cut3=cuts[2],
    )
    result["selected_entry_validation2"] = final_metrics["validation2"]
    result["final_oos"] = final_metrics["oos"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--setup", choices=SETUPS, required=True)
    parser.add_argument("--min-score", type=int, default=75)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result = research(args.db, args.setup, min_score=args.min_score)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "version": result["version"],
                "setup": result["setup"],
                "split": result["split"],
                "candidate_events": result["universe"]["candidate_events"],
                "selected_entry": result["selected_entry"],
                "selected_exit": result["selected_exit"],
                "final_oos": result["final_oos"],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
