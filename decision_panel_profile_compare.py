"""Final fixed-rule comparison before promoting Karar Paneli v6.4.5.

This script does not search parameters.  It evaluates two pre-declared exit
profiles (current production and the research-selected proposal) on the exact
same chronological segments and selected entry rules.  OOS is therefore used
as a final confirmation, not as a parameter search surface.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from decision_panel_pullback_v645_research import load_universe as load_pullback
from decision_panel_v645_research import (
    EntryCandidate,
    chronological_cuts,
    evaluate_candidate,
    load_universe as load_trend,
)
from strategy_exit_profiles import get_exit_profile


def _compact(metrics: dict) -> dict:
    keys = (
        "trades", "wins", "losses", "win_rate", "wilson_win_rate",
        "profit_factor", "expectancy_r", "max_drawdown_r", "tp1_rate",
        "tp2_rate", "tp3_rate", "avg_mfe_r", "avg_mae_r",
    )
    return {key: metrics.get(key) for key in keys}


def compare_pullback(database: str) -> dict:
    frames, event_map, meta = load_pullback(database, 75)
    cuts = chronological_cuts(frames, event_map)
    selected_name = "pullback_rv2_near20"
    events = event_map[selected_name]

    current = get_exit_profile("decision_panel", setup="PULLBACK")
    proposed = replace(
        current,
        stop_mode="hybrid_wide",
        atr_mult=1.25,
        tp1_r=0.8,
        tp2_r=1.5,
        tp3_r=2.4,
        trailing_atr_mult=1.8,
    )

    result = {}
    for name, profile in (("current", current), ("proposed", proposed)):
        metrics = evaluate_candidate(
            frames, events, "PULLBACK", profile,
            cut1=cuts[0], cut2=cuts[1], cut3=cuts[2],
        )
        result[name] = {
            "profile": asdict(profile),
            "train": _compact(metrics["train"]),
            "validation1": _compact(metrics["validation1"]),
            "validation2": _compact(metrics["validation2"]),
            "oos": _compact(metrics["oos"]),
        }
    return {
        "setup": "PULLBACK",
        "entry_rule": "v6.4.4 pullback + RVOL<=2.0 + pct20>=95",
        "candidate_events": meta["candidate_events"].get(selected_name, 0),
        "split": [value.isoformat() for value in cuts],
        "comparison": result,
    }


def compare_trend(database: str) -> dict:
    candidates = [EntryCandidate("trend_v644_baseline", "TREND DEVAMI")]
    frames, event_map, meta = load_trend(database, "TREND DEVAMI", min_score=75)
    cuts = chronological_cuts(frames, event_map)
    events = event_map["trend_v644_baseline"]

    current = get_exit_profile("decision_panel", setup="TREND DEVAMI")
    proposed = replace(
        current,
        stop_mode="swing",
        atr_mult=1.25,
        tp1_r=0.8,
        tp2_r=1.5,
        tp3_r=2.4,
        trailing_atr_mult=2.2,
    )

    result = {}
    for name, profile in (("current", current), ("proposed", proposed)):
        metrics = evaluate_candidate(
            frames, events, "TREND DEVAMI", profile,
            cut1=cuts[0], cut2=cuts[1], cut3=cuts[2],
        )
        result[name] = {
            "profile": asdict(profile),
            "train": _compact(metrics["train"]),
            "validation1": _compact(metrics["validation1"]),
            "validation2": _compact(metrics["validation2"]),
            "oos": _compact(metrics["oos"]),
        }
    return {
        "setup": "TREND DEVAMI",
        "entry_rule": "unchanged v6.4.4 baseline",
        "candidate_events": meta["candidate_events"].get("trend_v644_baseline", 0),
        "split": [value.isoformat() for value in cuts],
        "comparison": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = {
        "method": "fixed current-vs-proposed; no parameter search",
        "pullback": compare_pullback(args.db),
        "trend": compare_trend(args.db),
    }
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
