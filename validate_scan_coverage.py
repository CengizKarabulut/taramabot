"""Fail if a snapshot scan silently covers fewer eligible symbols than SQLite.

The scanner requires at least two bars for a symbol/timeframe.  Newly listed
stocks can legitimately have only one weekly/monthly bar, so coverage is
compared with the scanner-eligible universe rather than every stored symbol.
"""

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

            stored_symbols = store.list_symbols("BIST", period)
            eligible = 0
            for symbol in stored_symbols:
                frame = store.load_dataframe(symbol, "BIST", period, limit=2)
                if frame is not None and len(frame) >= 2:
                    eligible += 1

            scanned = int(result.get("total_scanned", 0) or 0)
            rows.append(
                {
                    "period": period,
                    "stored": len(stored_symbols),
                    "eligible": eligible,
                    "insufficient_history": len(stored_symbols) - eligible,
                    "scanned": scanned,
                }
            )
            if not stored_symbols:
                failures.append(f"{period}: snapshot evreni bos")
            elif eligible <= 0:
                failures.append(f"{period}: taranabilir sembol yok")
            elif scanned != eligible:
                failures.append(f"{period}: scanned={scanned}, eligible={eligible}")

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
