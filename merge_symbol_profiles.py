"""Merge historical profile parts and calculate each stock's best A-I/timeframe combinations."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PERIOD_ORDER = {period: index for index, period in enumerate(("15m", "30m", "45m", "1H", "2H", "4H", "1D", "1W", "1M"))}
MIN_RANK_EVENTS = 8


def _candidate(symbol: str, period: str, code: str, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "period": period,
        "code": code,
        "events": int(profile.get("events", 0) or 0),
        "quality_score": float(profile.get("quality_score", 0) or 0),
        "quality_label": profile.get("quality_label", "-"),
        "confidence": profile.get("confidence", "-"),
        "status": profile.get("status", "historical_profile"),
        "primary_horizon": profile.get("primary_horizon"),
    }


def merge(paths: list[str]) -> dict[str, Any]:
    periods: dict[str, dict[str, Any]] = {}
    generated = []
    sources = []
    round_trip_cost = None

    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("generated_at"):
            generated.append(payload["generated_at"])
        if payload.get("source"):
            sources.append(payload["source"])
        if round_trip_cost is None:
            round_trip_cost = payload.get("round_trip_cost_pct")
        for period, symbols in (payload.get("periods") or {}).items():
            destination = periods.setdefault(period, {})
            for symbol, codes in symbols.items():
                destination.setdefault(symbol, {}).update(codes)

    all_symbols = sorted({symbol for symbols in periods.values() for symbol in symbols})
    rankings: dict[str, list[dict[str, Any]]] = {}
    best_by_symbol: dict[str, Any] = {}
    best_early_warning: dict[str, Any] = {}

    for symbol in all_symbols:
        candidates = []
        early = []
        for period, symbols in periods.items():
            for code, profile in symbols.get(symbol, {}).items():
                row = _candidate(symbol, period, code, profile)
                if row["events"] < MIN_RANK_EVENTS:
                    continue
                if period == "15m":
                    early.append(row)
                else:
                    candidates.append(row)

        sort_key = lambda row: (row["quality_score"], row["events"], -PERIOD_ORDER.get(row["period"], 99))
        candidates.sort(key=sort_key, reverse=True)
        early.sort(key=sort_key, reverse=True)
        rankings[symbol] = candidates[:5]
        if candidates:
            best_by_symbol[symbol] = candidates[0]
        if early:
            best_early_warning[symbol] = early[0]

    return {
        "schema_version": 1,
        "generated_at": max(generated) if generated else datetime.now(timezone.utc).isoformat(),
        "merged_at": datetime.now(timezone.utc).isoformat(),
        "round_trip_cost_pct": round_trip_cost,
        "ranking_min_events": MIN_RANK_EVENTS,
        "periods": dict(sorted(periods.items(), key=lambda item: PERIOD_ORDER.get(item[0], 99))),
        "best_by_symbol": best_by_symbol,
        "best_early_warning_by_symbol": best_early_warning,
        "top_profiles_by_symbol": rankings,
        "sources": sources,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = merge(args.inputs)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({
        "symbols": len(payload["top_profiles_by_symbol"]),
        "best_symbols": len(payload["best_by_symbol"]),
        "periods": {period: len(symbols) for period, symbols in payload["periods"].items()},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
