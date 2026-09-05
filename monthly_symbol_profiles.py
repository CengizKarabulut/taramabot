"""Build 1M A-I stock profiles from the persistent deep monthly archive."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from market_data_store import MarketDataStore
from signal_parity import CODE_TO_STRATEGY, build_signal_frame
from symbol_historical_profiles import ROUND_TRIP_COST_PCT, _build_one_profile


def build(database: str, warmup: int = 240, max_symbols: int = 0) -> dict:
    output = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "periods": {"1M": {}},
        "source": {"monthly": "historical-live-1M direct deep archive"},
    }
    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", "1M")
        if max_symbols > 0:
            symbols = symbols[:max_symbols]
        for number, symbol in enumerate(symbols, start=1):
            frame = store.load_dataframe(symbol, "BIST", "1M", limit=0)
            if frame is None or len(frame) <= warmup + 2:
                continue
            signals = build_signal_frame(frame, warmup=warmup, period="1M")
            profiles = {}
            for code, strategy in CODE_TO_STRATEGY.items():
                profile = _build_one_profile("1M", frame, signals, strategy)
                if profile is not None:
                    profiles[code] = profile
            if profiles:
                output["periods"]["1M"][symbol] = profiles
            if number % 100 == 0 or number == len(symbols):
                print(f"[{number}/{len(symbols)}] monthly_profiles={len(output['periods']['1M'])}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--warmup", type=int, default=240)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = build(args.db, warmup=max(50, args.warmup), max_symbols=max(0, args.max_symbols))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str) + "\n", encoding="utf-8")
    print(json.dumps({"1M_symbols": len(payload["periods"]["1M"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
