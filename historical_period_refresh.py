"""Build a dedicated long-history BIST snapshot for one timeframe.

This is research-only and never replaces the production 400-bar snapshot.  A
separate SQLite artifact is created per timeframe so 1H/4H/etc. walk-forward
validation can use a much deeper history without slowing the live scanner.
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


def _fetch(tv: TvDatafeed, symbol: str, period: str, bars: int, retries: int):
    interval = PERIOD_INTERVALS[period]
    for attempt in range(1, retries + 1):
        try:
            frame = tv.get_hist(symbol, "BIST", interval=interval, n_bars=bars)
            if frame is not None and not frame.empty:
                return frame
            raise RuntimeError("bos veri")
        except Exception as exc:
            if attempt >= retries:
                logger.warning("%s %s uzun tarih verisi alinamadi: %s", symbol, period, exc)
                return None
            time.sleep(min(8, 2 ** attempt))
    return None


def build_snapshot(database: str, manifest: str, *, period: str, bars: int, retries: int) -> dict:
    period = normalize_period(period)
    if period not in PERIOD_INTERVALS:
        raise ValueError(f"Desteklenmeyen periyot: {period}")

    symbols = list(dict.fromkeys(BIST_STOCKS))
    if not symbols:
        raise RuntimeError("BIST sembol listesi bos")

    tv = _connection()
    success = 0
    rows = 0
    min_bars = None
    max_bars = 0
    ready_240 = 0
    ready_280 = 0

    db_path = Path(database)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    with MarketDataStore(str(db_path)) as store:
        for position, symbol in enumerate(symbols, start=1):
            frame = _fetch(tv, symbol, period, bars, retries)
            if frame is None:
                continue
            inserted = store.upsert_dataframe(symbol, "BIST", period, frame, max_bars=bars)
            if inserted:
                success += 1
                rows += inserted
                min_bars = inserted if min_bars is None else min(min_bars, inserted)
                max_bars = max(max_bars, inserted)
                ready_240 += int(inserted >= 240)
                ready_280 += int(inserted >= 280)
            if position % 25 == 0 or position == len(symbols):
                logger.info(
                    "Uzun tarih %s: %s/%s sembol; basarili=%s; >=240=%s",
                    period,
                    position,
                    len(symbols),
                    success,
                    ready_240,
                )

        minimum_success = max(1, int(len(symbols) * 0.70))
        if success < minimum_success:
            raise RuntimeError(f"{period} uzun tarih kapsami yetersiz: {success}/{len(symbols)}")
        if not store.integrity_ok():
            raise RuntimeError("SQLite butunluk kontrolu basarisiz")

        store.set_metadata("purpose", "long_history_multitimeframe_backtest")
        store.set_metadata("period", period)
        store.set_metadata("requested_bars", int(bars))
        store.set_metadata("generated_at", datetime.now(TZ_TURKEY).isoformat())
        payload = store.build_manifest()

    payload.update(
        {
            "purpose": "long_history_multitimeframe_backtest",
            "period": period,
            "requested_bars": int(bars),
            "requested_symbols": len(symbols),
            "successful_symbols": success,
            "symbols_with_240_bars": ready_240,
            "symbols_with_280_bars": ready_280,
            "rows_inserted": rows,
            "min_symbol_bars": int(min_bars or 0),
            "max_symbol_bars": int(max_bars),
        }
    )

    manifest_path = Path(manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--period", choices=tuple(PERIOD_INTERVALS), required=True)
    parser.add_argument("--bars", type=int, default=2500)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()
    build_snapshot(
        args.db,
        args.manifest,
        period=args.period,
        bars=max(400, int(args.bars)),
        retries=max(1, int(args.retries)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
