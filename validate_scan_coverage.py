"""Fail if any snapshot scan silently covers fewer symbols than the SQLite universe."""

from __future__ import annotations

import argparse
import json

from market_data_store import MarketDataStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()

    with open(args.summary, "r", encoding="utf-8") as handle:
        results = json.load(handle)
    if not isinstance(results, list):
        raise RuntimeError("Tarama ozeti liste degil")

    failures = []
    rows = []
    with MarketDataStore(args.db, read_only=True) as store:
        for result in results:
            period = str(result.get("period", ""))
            if not period:
                failures.append("period eksik")
                continue
            expected = len(store.list_symbols("BIST", period))
            scanned = int(result.get("total_scanned", 0) or 0)
            rows.append({"period": period, "expected": expected, "scanned": scanned})
            if expected <= 0:
                failures.append(f"{period}: snapshot evreni bos")
            elif scanned != expected:
                failures.append(f"{period}: scanned={scanned}, expected={expected}")

    payload = {
        "passed": not failures,
        "periods": rows,
        "failures": failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
