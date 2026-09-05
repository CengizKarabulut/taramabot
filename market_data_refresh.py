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
MIN_REASONABLE_BIST_UNIVERSE = 400


def _normalise_symbols(values) -> list[str]:
    return sorted({
        str(value).strip().upper()
        for value in (values or [])
        if str(value).strip()
    })


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

    @staticmethod
    def _snapshot_symbols(store: MarketDataStore) -> list[str]:
        symbols = store.list_symbols("BIST", "15m")
        if not symbols:
            symbols = store.list_symbols("BIST", "1D")
        return _normalise_symbols(symbols)

    def _resolve_refresh_symbols(self, store: MarketDataStore, *, allow_empty_snapshot: bool) -> list[str]:
        """Use the durable snapshot as the authoritative refresh universe.

        ``config.BIST_STOCKS`` is populated from borsapy at import time, but an
        intermittent borsapy failure can leave only the small static fallback
        list.  Never let that shrink a healthy 500+ symbol snapshot.  A
        sufficiently complete discovered list is unioned only to pick up new
        listings.
        """
        snapshot = self._snapshot_symbols(store)
        discovered = _normalise_symbols(BIST_STOCKS)

        if snapshot:
            discovery_is_complete = len(discovered) >= max(
                MIN_REASONABLE_BIST_UNIVERSE,
                int(len(snapshot) * 0.80),
            )
            if discovery_is_complete:
                resolved = sorted(set(snapshot).union(discovered))
                if len(resolved) > len(snapshot):
                    logger.info(
                        "BIST evrenine dis kaynaktan %s yeni sembol adayi eklendi (%s -> %s).",
                        len(resolved) - len(snapshot),
                        len(snapshot),
                        len(resolved),
                    )
                return resolved

            if discovered:
                logger.warning(
                    "Borsapy/config BIST listesi eksik gorunuyor (%s); saglam snapshot evreni (%s) korunuyor.",
                    len(discovered),
                    len(snapshot),
                )
            return snapshot

        if not allow_empty_snapshot:
            raise RuntimeError("Saglam snapshot BIST evreni bulunamadi")
        if len(discovered) < MIN_REASONABLE_BIST_UNIVERSE:
            raise RuntimeError(
                "Ilk kurulum BIST sembol evreni eksik: "
                f"{len(discovered)} < {MIN_REASONABLE_BIST_UNIVERSE}. "
                "Kalici history rebuild veya tam borsapy listesi gerekli."
            )
        return discovered

    def full_refresh(self, store: MarketDataStore) -> dict:
        """Tum periyotlarda dogrudan TradingView verisiyle kurtarma snapshot'i olustur."""
        period_stats = {}
        symbols = self._resolve_refresh_symbols(store, allow_empty_snapshot=True)
        total_symbols = len(symbols)

        for period, interval in PERIOD_INTERVALS.items():
            success = 0
            logger.info("%s tam veri yenilemesi basladi (%s sembol).", period, total_symbols)
            for position, symbol in enumerate(symbols, start=1):
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
        """Yalnizca yakin 15m mumlarini indirip ust periyotlarin guncel mumlarini turet."""
        symbols = self._resolve_refresh_symbols(store, allow_empty_snapshot=False)
        total_symbols = len(symbols)
        current_daily_symbols = store.symbol_count("BIST", "1D")
        if current_daily_symbols < self._minimum_success_count(total_symbols):
            raise RuntimeError(
                "Tam veri snapshot'i bulunamadi veya kapsami yetersiz: "
                f"1D={current_daily_symbols}, evren={total_symbols}."
            )

        success = 0
        logger.info("Artimli 15m veri yenilemesi basladi (%s sembol).", total_symbols)
        for position, symbol in enumerate(symbols, start=1):
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
        store.set_metadata("last_refresh_universe_size", str(total_symbols))
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
