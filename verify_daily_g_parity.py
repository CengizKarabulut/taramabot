"""No-state smoke test for daily G (full SMI/MACD) parity.

Designed for validating the cached SQLite snapshot independently from scanner
state / Telegram deduplication.  BIGTK and UFUK were previously false-positive
G candidates because the 1D cache contained two rows for the same session.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from tvDatafeed import Interval

from scanner_enhanced import MarketScanner


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbols", nargs="+", default=["BIGTK", "UFUK"])
    args = parser.parse_args()

    scanner = MarketScanner(data_store_path=args.db)
    output = []
    failed = False

    try:
        for symbol in args.symbols:
            result = await scanner.scan_symbol(
                symbol,
                "BIST",
                Interval.in_daily,
                "1D",
                strategies=["smi_macd"],
            )
            if not result:
                output.append({"symbol": symbol, "error": "no_result"})
                failed = True
                continue

            smi = result.get("signals", {}).get("smi_macd", {})
            raw_full = bool(smi.get("full_buy_signal"))
            filter_ok = bool(smi.get("passes_full_filter", True))
            final_g = raw_full and filter_ok
            output.append(
                {
                    "symbol": symbol,
                    "bar_time": result.get("bar_time"),
                    "close": float(result.get("close", 0) or 0),
                    "change_pct": float(result.get("change", 0) or 0),
                    "daily_change_pct": float(result.get("daily_change", 0) or 0),
                    "raw_full": raw_full,
                    "passes_full_filter": filter_ok,
                    "final_G": final_g,
                    "details": smi.get("details", {}),
                }
            )
            if final_g:
                failed = True
    finally:
        if scanner.data_store is not None:
            scanner.data_store.close()

    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
