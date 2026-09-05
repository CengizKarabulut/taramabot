"""Incrementally maintain one long-history BIST timeframe archive.

The first run bootstraps a deep history when no SQLite archive exists. Later
runs fetch only a small recent overlap and upsert those candles without
trimming older rows. New symbols receive a full bootstrap automatically.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tvDatafeed import TvDatafeed

from config import BIST_STOCKS, TV_PASSWORD, TV_USERNAME
from market_data_refresh import PERIOD_INTERVALS
from market_data_store import MarketDataStore, normalize_period


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
TZ_TURKEY = ZoneInfo("Europe/Istanbul")


def _connection() -> TvDatafeed:
    if TV_USERNAME and TV_PASSWORD:
        try:
            return TvDatafeed(username=TV_USERNAME, password=TV_PASSWORD)
        except Exception as exc:
            logger.warning("TradingView oturumu acilamadi, anonim devam: %s", exc)
    return TvDatafeed()


def _fetch(
    tv: TvDatafeed,
    symbol: str,
    period: str,
    bars: int,
    retries: int,
):
    interval = PERIOD_INTERVALS[period]
    for attempt in range(1, retries + 1):
        try:
            frame = tv.get_hist(
                symbol,
                "BIST",
                interval=interval,
                n_bars=max(1, int(bars)),
            )
            if frame is not None and not frame.empty:
                return frame
            raise RuntimeError("bos veri")
        except Exception as exc:
            if attempt >= retries:
                logger.warning(
                    "%s %s verisi alinamadi (%s bar): %s",
                    symbol,
                    period,
                    bars,
                    exc,
                )
                return None
            time.sleep(min(8, 2 ** attempt))
    return None


def update_archive(
    database: str,
    manifest: str,
    *,
    period: str,
    bootstrap_bars: int,
    recent_bars: int,
    retries: int,
) -> dict:
    period = normalize_period(period)
    if period not in PERIOD_INTERVALS:
        raise ValueError(f"Desteklenmeyen periyot: {period}")

    symbols = list(dict.fromkeys(BIST_STOCKS))
    if not symbols:
        raise RuntimeError("BIST sembol listesi bos")

    db_path = Path(database)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    tv = _connection()
    success = 0
    bootstrap_symbols = 0
    incremental_symbols = 0
    fetched_rows = 0

    with MarketDataStore(str(db_path)) as store:
        existing_symbols = set(store.list_symbols("BIST", period))
        mode = "incremental" if existing_symbols else "bootstrap"

        for position, symbol in enumerate(symbols, start=1):
            is_new_symbol = symbol not in existing_symbols
            bars = bootstrap_bars if is_new_symbol else recent_bars
            frame = _fetch(tv, symbol, period, bars, retries)
            if frame is None:
                continue

            # max_bars=None is deliberate: old history is never trimmed.
            upserted = store.upsert_dataframe(
                symbol,
                "BIST",
                period,
                frame,
                max_bars=None,
            )
            if upserted:
                success += 1
                fetched_rows += upserted
                if is_new_symbol:
                    bootstrap_symbols += 1
                else:
                    incremental_symbols += 1

            if position % 25 == 0 or position == len(symbols):
                logger.info(
                    "%s arsiv guncelleme: %s/%s; basarili=%s; yeni=%s; artimli=%s",
                    period,
                    position,
                    len(symbols),
                    success,
                    bootstrap_symbols,
                    incremental_symbols,
                )

        minimum_success = max(1, int(len(symbols) * 0.70))
        if success < minimum_success:
            raise RuntimeError(
                f"{period} arsiv guncelleme kapsami yetersiz: {success}/{len(symbols)}"
            )
        if not store.integrity_ok():
            raise RuntimeError("SQLite butunluk kontrolu basarisiz")

        store.set_metadata("purpose", "persistent_long_history_archive")
        store.set_metadata("period", period)
        store.set_metadata("last_refresh_mode", mode)
        store.set_metadata("bootstrap_bars", int(bootstrap_bars))
        store.set_metadata("recent_bars", int(recent_bars))
        store.set_metadata("last_refresh", datetime.now(TZ_TURKEY).isoformat())
        payload = store.build_manifest()

    payload.update(
        {
            "purpose": "persistent_long_history_archive",
            "period": period,
            "refresh_mode": mode,
            "requested_symbols": len(symbols),
            "successful_symbols": success,
            "bootstrap_symbols": bootstrap_symbols,
            "incremental_symbols": incremental_symbols,
            "fetched_rows_this_run": fetched_rows,
            "bootstrap_bars": int(bootstrap_bars),
            "recent_bars": int(recent_bars),
        }
    )

    manifest_path = Path(manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--period", choices=tuple(PERIOD_INTERVALS), required=True)
    parser.add_argument("--bootstrap-bars", type=int, required=True)
    parser.add_argument("--recent-bars", type=int, default=64)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    update_archive(
        args.db,
        args.manifest,
        period=args.period,
        bootstrap_bars=max(400, int(args.bootstrap_bars)),
        recent_bars=max(2, int(args.recent_bars)),
        retries=max(1, int(args.retries)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
