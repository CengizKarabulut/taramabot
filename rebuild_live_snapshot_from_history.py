"""Rebuild the compact live market snapshot from persistent timeframe archives.

This is a repair/bootstrap utility, not a recurring downloader.  Each source is
a clean long-history SQLite for one timeframe; only the latest BARS_TO_FETCH
rows per symbol are copied into a new live snapshot.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import BARS_TO_FETCH
from market_data_store import MarketDataStore, normalize_period


TZ_TURKEY = ZoneInfo("Europe/Istanbul")
PERIODS = ("15m", "30m", "45m", "1H", "2H", "4H", "1D", "1W", "1M")


def parse_source(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise argparse.ArgumentTypeError("Kaynak PERIOD:path formatinda olmali")
    period, path = value.split(":", 1)
    period = normalize_period(period)
    if period not in PERIODS:
        raise argparse.ArgumentTypeError(f"Desteklenmeyen period: {period}")
    return period, path


def rebuild(destination: str, sources: list[tuple[str, str]], manifest: str) -> dict:
    source_map = dict(sources)
    missing = [period for period in PERIODS if period not in source_map]
    if missing:
        raise RuntimeError(f"Eksik kaynak timeframe: {missing}")

    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    stats = {}
    with MarketDataStore(str(output)) as target:
        for period in PERIODS:
            source_path = source_map[period]
            copied_symbols = 0
            copied_rows = 0
            with MarketDataStore(source_path, read_only=True) as source:
                symbols = source.list_symbols("BIST", period)
                for symbol in symbols:
                    frame = source.load_dataframe(
                        symbol,
                        "BIST",
                        period,
                        limit=BARS_TO_FETCH,
                    )
                    if frame is None or frame.empty:
                        continue
                    copied_rows += target.upsert_dataframe(
                        symbol,
                        "BIST",
                        period,
                        frame,
                        max_bars=BARS_TO_FETCH,
                    )
                    copied_symbols += 1
            stats[period] = {
                "symbols": copied_symbols,
                "rows_written": copied_rows,
            }
            print(f"{period}: {copied_symbols} sembol, {copied_rows} satir")

        target.set_metadata("snapshot_source", "persistent_history_archives")
        target.set_metadata("snapshot_rebuilt_at", datetime.now(TZ_TURKEY).isoformat())
        target.set_metadata("last_refresh_mode", "history_rebuild")
        if not target.integrity_ok():
            raise RuntimeError("Yeni live SQLite integrity_check basarisiz")
        payload = target.build_manifest()
        payload["mode"] = "history_rebuild"
        payload["bars_to_fetch"] = BARS_TO_FETCH
        payload["rebuild_stats"] = stats

    manifest_path = Path(manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/market_data.sqlite")
    parser.add_argument("--manifest", default="data/manifest.json")
    parser.add_argument("--source", action="append", type=parse_source, required=True)
    args = parser.parse_args()
    payload = rebuild(args.db, args.source, args.manifest)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
