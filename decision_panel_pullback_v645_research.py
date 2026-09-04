"""Research-only Pullback robustness study for Karar Paneli v6.4.5.

The exact v6.4.4 Pullback entry was strong in development but failed the next
chronological validation block.  This module therefore tests only a small set
of interpretable anti-climax / trend-quality filters; it does not optimize on
final OOS data and does not modify production signals.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from decision_panel_breakout_diagnostic import gate_frame
from decision_panel_v645_research import (
    chronological_cuts,
    evaluate_candidate,
    optimize_exit,
)
from historical_decision_panel_v644 import _normalize, decision_series
from market_data_store import MarketDataStore
from strategy_exit_profiles import get_exit_profile
from strategy_optimizer import eligible, ranking_score


@dataclass(frozen=True)
class PullbackCandidate:
    name: str
    max_rvol: float | None = None
    min_slope200_pct: float | None = None
    min_pct20: float | None = None


CANDIDATES = [
    PullbackCandidate("pullback_v644_baseline"),
    PullbackCandidate("pullback_rv2.0", max_rvol=2.0),
    PullbackCandidate("pullback_rv1.75", max_rvol=1.75),
    PullbackCandidate("pullback_slope200_pos", min_slope200_pct=0.0),
    PullbackCandidate("pullback_near20_95", min_pct20=95.0),
    PullbackCandidate("pullback_rv2_slope", max_rvol=2.0, min_slope200_pct=0.0),
    PullbackCandidate("pullback_rv2_near20", max_rvol=2.0, min_pct20=95.0),
    PullbackCandidate("pullback_slope_near20", min_slope200_pct=0.0, min_pct20=95.0),
    PullbackCandidate("pullback_orderly", max_rvol=2.0, min_slope200_pct=0.0, min_pct20=95.0),
]


def _fresh(condition: pd.Series) -> pd.Series:
    condition = condition.fillna(False).astype(bool)
    return condition & (~condition.shift(1).fillna(False))


def pullback_features(clean: pd.DataFrame) -> pd.DataFrame:
    c, h, v = clean["close"], clean["high"], clean["volume"]
    sma200 = c.rolling(200).mean()
    slope200 = (sma200 / sma200.shift(20) - 1.0) * 100.0
    rvol = v / v.rolling(10).mean().shift(1).replace(0, np.nan)
    high20 = h.rolling(20).max()
    pct20 = c / high20.replace(0, np.nan) * 100.0
    return pd.DataFrame(
        {"slope200_pct": slope200, "rvol": rvol, "pct20": pct20},
        index=clean.index,
    )


def masks_for_symbol(clean: pd.DataFrame, min_score: int) -> dict[str, pd.Series]:
    state = decision_series(clean, min_score=min_score)
    base = state["pullback"].fillna(False)
    feat = pullback_features(clean)
    result: dict[str, pd.Series] = {}
    for candidate in CANDIDATES:
        mask = base.copy()
        if candidate.max_rvol is not None:
            mask &= feat["rvol"] <= candidate.max_rvol
        if candidate.min_slope200_pct is not None:
            mask &= feat["slope200_pct"] >= candidate.min_slope200_pct
        if candidate.min_pct20 is not None:
            mask &= feat["pct20"] >= candidate.min_pct20
        result[candidate.name] = _fresh(mask)
    return result


def load_universe(database: str, min_score: int = 75):
    frames: dict[str, pd.DataFrame] = {}
    event_map: dict[str, dict[str, list[int]]] = {c.name: {} for c in CANDIDATES}
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
                masks = masks_for_symbol(clean, min_score)
                for name, mask in masks.items():
                    idx = [int(i) for i in np.flatnonzero(mask.to_numpy(dtype=bool)) if int(i) < len(clean)-1]
                    if idx:
                        event_map[name][symbol] = idx
                if number % 50 == 0 or number == len(symbols):
                    print(f"[{number}/{len(symbols)}] Pullback adayları işlendi")
            except Exception as exc:
                errors += 1
                print(f"[{number}/{len(symbols)}] {symbol}: HATA {exc}")
    totals = {name: sum(len(v) for v in by_symbol.values()) for name, by_symbol in event_map.items()}
    return frames, event_map, {"symbols_loaded": len(frames), "errors": errors, "candidate_events": totals}


def research(database: str, min_score: int = 75) -> dict[str, Any]:
    frames, event_map, universe = load_universe(database, min_score)
    cuts = chronological_cuts(frames, event_map)
    base_profile = get_exit_profile("decision_panel", setup="PULLBACK")
    entry_rows = []

    for candidate in CANDIDATES:
        metrics = evaluate_candidate(
            frames, event_map[candidate.name], "PULLBACK", base_profile,
            cut1=cuts[0], cut2=cuts[1], cut3=cuts[2],
        )
        train, val1 = metrics["train"], metrics["validation1"]
        train_ok = eligible(train, min_trades=100, min_pf=1.05)
        val_ok = eligible(val1, min_trades=35, min_pf=1.00)
        entry_rows.append(
            {
                "candidate": asdict(candidate),
                "train": train,
                "validation1": val1,
                "train_eligible": train_ok,
                "validation1_eligible": val_ok,
                "validation1_score": ranking_score(val1, min_trades=35, min_pf=1.00),
            }
        )

    entry_rows.sort(
        key=lambda row: (
            bool(row["train_eligible"] and row["validation1_eligible"]),
            row["validation1_score"],
        ),
        reverse=True,
    )
    selected_row = next(
        (r for r in entry_rows if r["train_eligible"] and r["validation1_eligible"]),
        None,
    )
    result: dict[str, Any] = {
        "version": "v6.4.5-pullback-research",
        "setup": "PULLBACK",
        "universe": universe,
        "split": {
            "train_end": cuts[0].isoformat(),
            "validation1_end": cuts[1].isoformat(),
            "validation2_end": cuts[2].isoformat(),
            "rule": "50/20/15/15 chronological candidate-event dates",
            "leakage_guard": "entry_and_exit_must_stay_inside_same_segment",
        },
        "selected_entry": selected_row["candidate"] if selected_row else None,
        "entry_ranking": entry_rows,
        "selected_exit": None,
        "exit_ranking": [],
        "final_oos": None,
    }
    if selected_row is None:
        return result

    selected_name = selected_row["candidate"]["name"]
    selected_exit, exit_rows = optimize_exit(
        frames, event_map[selected_name], "PULLBACK", cuts
    )
    result["selected_exit"] = asdict(selected_exit) if selected_exit else None
    result["exit_ranking"] = exit_rows[:20]
    if selected_exit is None:
        return result

    final = evaluate_candidate(
        frames, event_map[selected_name], "PULLBACK", selected_exit,
        cut1=cuts[0], cut2=cuts[1], cut3=cuts[2],
    )
    result["selected_entry_validation2"] = final["validation2"]
    result["final_oos"] = final["oos"]
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("--min-score", type=int, default=75)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    result = research(a.db, a.min_score)
    dest = Path(a.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps({
        "split": result["split"],
        "candidate_events": result["universe"]["candidate_events"],
        "selected_entry": result["selected_entry"],
        "selected_exit": result["selected_exit"],
        "final_oos": result["final_oos"],
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
