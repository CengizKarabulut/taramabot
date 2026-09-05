"""SQLite piyasa verisi snapshot'ini tam veya artimli gunceller."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tvDatafeed import Interval, TvDatafeed

from bist_timeframes import (
    resample_bist_intraday,
    resample_calendar,
    resample_daily,
)
from config import BARS_TO_FETCH, BIST_STOCKS, TV_PASSWORD, TV_USERNAME
from market_data_store import MarketDataStore


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
TZ_TURKEY = ZoneInfo("Europe/Istanbul")

PERIOD_INTERVALS = {
    "15m": Interval.in_15_minute,
    "30m": Interval.in_30_minute,
    "45m": Interval.in_45_minute,
    "1H": Interval.in_1_hour,
    "2H": Interval.in_2_hour,
    "4H": Interval.in_4_hour,
    "1D": Interval.in_daily,
    "1W": Interval.in_weekly,
    "1M": Interval.in_monthly,
}
DERIVED_INTRADAY_PERIODS = ("30m", "45m", "1H", "2H", "4H")


class MarketDataRefresher:
    def __init__(self, database_path: str, retries: int = 3):
        self.database_path = database_path
        self.retries = max(1, retries)
        self.tv = self._create_tv_connection()

    @staticmethod
    def _create_tv_connection() -> TvDatafeed:
        if TV_USERNAME and TV_PASSWORD:
            try:
                logger.info("TradingView oturumuyla veri baglantisi kuruluyor.")
                return TvDatafeed(username=TV_USERNAME, password=TV_PASSWORD)
            except Exception as exc:
                logger.warning("TradingView oturumu acilamadi, anonim devam ediliyor: %s", exc)
        return TvDatafeed()

    def _fetch(self, symbol: str, interval: Interval, bars: int):
        for attempt in range(1, self.retries + 1):
            try:
                frame = self.tv.get_hist(
                    symbol,
                    "BIST",
                    interval=interval,
                    n_bars=bars,
                )
                if frame is not None and not frame.empty:
                    return frame
                raise RuntimeError("bos veri")
            except Exception as exc:
                if attempt >= self.retries:
                    logger.warning("%s verisi alinamadi: %s", symbol, exc)
                    return None
                wait_seconds = 2 ** attempt
                logger.debug(
                    "%s veri denemesi %s/%s basarisiz; %ss beklenecek.",
                    symbol,
                    attempt,
                    self.retries,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
        return None

    @staticmethod
    def _minimum_success_count(total_symbols: int) -> int:
        return max(1, int(total_symbols * 0.70))

    def full_refresh(self, store: MarketDataStore) -> dict:
        """Tum periyotlarda dogrudan TradingView verisiyle aksam snapshot'i olustur."""
        period_stats = {}
        total_symbols = len(BIST_STOCKS)
        if total_symbols == 0:
            raise RuntimeError("BIST sembol listesi bos")

        for period, interval in PERIOD_INTERVALS.items():
            success = 0
            logger.info("%s tam veri yenilemesi basladi (%s sembol).", period, total_symbols)
            for position, symbol in enumerate(BIST_STOCKS, start=1):
                frame = self._fetch(symbol, interval, BARS_TO_FETCH)
                if frame is not None:
                    store.upsert_dataframe(
                        symbol,
                        "BIST",
                        period,
                        frame,
                        max_bars=BARS_TO_FETCH,
                    )
                    success += 1
                if position % 25 == 0 or position == total_symbols:
                    logger.info("%s: %s/%s sembol tamamlandi.", period, position, total_symbols)

            if success < self._minimum_success_count(total_symbols):
                raise RuntimeError(
                    f"{period} veri kapsami yetersiz: {success}/{total_symbols}"
                )
            period_stats[period] = {"success": success, "total": total_symbols}

        store.set_metadata("last_full_refresh", datetime.now(TZ_TURKEY).isoformat())
        store.set_metadata("last_refresh_mode", "full")
        return period_stats

    def intraday_refresh(self, store: MarketDataStore, recent_bars: int = 64) -> dict:
        """Yalnizca yeni 15m mumlari indirip diger periyotlarin guncel mumlarini turet."""
        total_symbols = len(BIST_STOCKS)
        if total_symbols == 0:
            raise RuntimeError("BIST sembol listesi bos")
        if store.symbol_count("BIST", "1D") < self._minimum_success_count(total_symbols):
            raise RuntimeError(
                "Tam veri snapshot'i bulunamadi. Once full yenileme calistirilmali."
            )

        success = 0
        logger.info("Artimli 15m veri yenilemesi basladi (%s sembol).", total_symbols)
        for position, symbol in enumerate(BIST_STOCKS, start=1):
            frame_15m = self._fetch(symbol, Interval.in_15_minute, recent_bars)
            if frame_15m is None:
                continue

            latest_session_date = frame_15m.index[-1].date()
            current_session = frame_15m[
                [timestamp.date() == latest_session_date for timestamp in frame_15m.index]
            ]
            if current_session.empty:
                continue

            store.upsert_dataframe(
                symbol,
                "BIST",
                "15m",
                frame_15m,
                max_bars=BARS_TO_FETCH,
            )
            for period in DERIVED_INTRADAY_PERIODS:
                derived = resample_bist_intraday(current_session, period)
                store.upsert_dataframe(
                    symbol,
                    "BIST",
                    period,
                    derived,
                    max_bars=BARS_TO_FETCH,
                )

            current_daily = resample_daily(current_session)
            store.upsert_dataframe(
                symbol,
                "BIST",
                "1D",
                current_daily,
                max_bars=BARS_TO_FETCH,
            )
            daily_history = store.load_dataframe(
                symbol,
                "BIST",
                "1D",
                limit=BARS_TO_FETCH,
            )
            if daily_history is not None:
                weekly = resample_calendar(daily_history.tail(14), "1W")
                monthly = resample_calendar(daily_history.tail(62), "1M")
                store.upsert_dataframe(
                    symbol,
                    "BIST",
                    "1W",
                    weekly,
                    max_bars=BARS_TO_FETCH,
                )
                store.upsert_dataframe(
                    symbol,
                    "BIST",
                    "1M",
                    monthly,
                    max_bars=BARS_TO_FETCH,
                )

            success += 1
            if position % 25 == 0 or position == total_symbols:
                logger.info("Artimli veri: %s/%s sembol tamamlandi.", position, total_symbols)

        if success < self._minimum_success_count(total_symbols):
            raise RuntimeError(f"Artimli veri kapsami yetersiz: {success}/{total_symbols}")

        store.set_metadata("last_intraday_refresh", datetime.now(TZ_TURKEY).isoformat())
        store.set_metadata("last_refresh_mode", "intraday")
        return {"15m": {"success": success, "total": total_symbols}}

    def run(self, mode: str, manifest_path: str, recent_bars: int = 64) -> dict:
        with MarketDataStore(self.database_path) as store:
            if mode == "full":
                stats = self.full_refresh(store)
            elif mode == "intraday":
                stats = self.intraday_refresh(store, recent_bars=recent_bars)
            else:
                raise ValueError(f"Bilinmeyen yenileme modu: {mode}")

            if not store.integrity_ok():
                raise RuntimeError("SQLite butunluk kontrolu basarisiz")

            manifest = store.build_manifest()
            manifest["mode"] = mode
            manifest["refresh_stats"] = stats
            manifest["bars_to_fetch"] = BARS_TO_FETCH
            manifest["recent_bars"] = recent_bars

        manifest_file = Path(manifest_path)
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest_file.with_suffix(manifest_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(manifest_file)
        logger.info("Veri snapshot'i hazir: %s", self.database_path)
        return manifest


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("full", "intraday"), required=True)
    parser.add_argument("--db", default="data/market_data.sqlite")
    parser.add_argument("--manifest", default="data/manifest.json")
    parser.add_argument("--recent-bars", type=int, default=64)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    refresher = MarketDataRefresher(args.db, retries=args.retries)
    manifest = refresher.run(
        mode=args.mode,
        manifest_path=args.manifest,
        recent_bars=args.recent_bars,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
