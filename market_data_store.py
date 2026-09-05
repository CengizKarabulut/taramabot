"""GitHub Actions icin kalici ve artimli piyasa verisi deposu."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional
from zoneinfo import ZoneInfo

import pandas as pd


TZ_TURKEY = ZoneInfo("Europe/Istanbul")
OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
INTRADAY_MINUTES: Dict[str, int] = {
    "15m": 15,
    "30m": 30,
    "45m": 45,
    "1H": 60,
    "2H": 120,
    "4H": 240,
}


def normalize_period(period: str) -> str:
    value = str(period).strip()
    mapping = {
        "15M": "15m",
        "30M": "30m",
        "45M": "45m",
        "1h": "1H",
        "2h": "2H",
        "4h": "4H",
        "1d": "1D",
        "1w": "1W",
        "1m": "1M",
    }
    return mapping.get(value, value)


def _clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    clean = df.copy()
    clean.columns = [str(column).lower() for column in clean.columns]
    missing = [column for column in OHLCV_COLUMNS if column not in clean.columns]
    if missing:
        raise ValueError(f"OHLCV kolonlari eksik: {missing}")

    clean = clean.loc[:, OHLCV_COLUMNS]
    clean.index = pd.to_datetime(clean.index, errors="coerce")
    clean = clean[~clean.index.isna()]
    clean = clean[~clean.index.duplicated(keep="last")].sort_index()
    for column in OHLCV_COLUMNS:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    return clean.dropna(subset=["open", "high", "low", "close"])


def _aggregate(frame: pd.DataFrame, bucket_index: Iterable[pd.Timestamp]) -> pd.DataFrame:
    clean = _clean_dataframe(frame)
    if clean.empty:
        return clean

    grouped = clean.groupby(pd.DatetimeIndex(bucket_index), sort=True)
    result = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    result.index.name = clean.index.name
    return result.dropna(subset=["open", "high", "low", "close"])


def resample_bist_intraday(df_15m: pd.DataFrame, target_period: str) -> pd.DataFrame:
    """15 dakikalik mumlari BIST 10:00 seansina sabitleyerek birlestirir."""
    target_period = normalize_period(target_period)
    minutes = INTRADAY_MINUTES.get(target_period)
    if minutes is None:
        raise ValueError(f"Desteklenmeyen gun ici periyot: {target_period}")

    clean = _clean_dataframe(df_15m)
    if clean.empty or target_period == "15m":
        return clean

    buckets = []
    for timestamp in clean.index:
        session_start = timestamp.normalize() + pd.Timedelta(hours=10)
        elapsed_minutes = max(0, int((timestamp - session_start).total_seconds() // 60))
        bucket_offset = (elapsed_minutes // minutes) * minutes
        buckets.append(session_start + pd.Timedelta(minutes=bucket_offset))
    return _aggregate(clean, buckets)


def resample_daily(df_15m: pd.DataFrame) -> pd.DataFrame:
    clean = _clean_dataframe(df_15m)
    return _aggregate(clean, clean.index.normalize()) if not clean.empty else clean


def resample_calendar(df_daily: pd.DataFrame, target_period: str) -> pd.DataFrame:
    """Gunluk mumlardan haftalik veya aylik son mumlari olusturur."""
    clean = _clean_dataframe(df_daily)
    if clean.empty:
        return clean

    naive_index = clean.index.tz_localize(None) if clean.index.tz is not None else clean.index
    if normalize_period(target_period) == "1W":
        buckets = naive_index.to_period("W-SUN").start_time
    elif normalize_period(target_period) == "1M":
        buckets = naive_index.to_period("M").start_time
    else:
        raise ValueError(f"Desteklenmeyen takvim periyodu: {target_period}")
    return _aggregate(clean, buckets)


class MarketDataStore:
    """OHLCV verisini tek SQLite dosyasinda saklar."""

    def __init__(self, path: str, read_only: bool = False):
        self.path = os.path.abspath(path)
        self.read_only = read_only
        if read_only:
            if not os.path.exists(self.path):
                raise FileNotFoundError(self.path)
            uri = Path(self.path).as_uri() + "?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
        else:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self.connection = sqlite3.connect(self.path)
            self.connection.execute("PRAGMA journal_mode=DELETE")
            self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                period TEXT NOT NULL,
                candle_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL,
                PRIMARY KEY (symbol, exchange, period, candle_time)
            );
            CREATE INDEX IF NOT EXISTS idx_candles_lookup
                ON candles(symbol, exchange, period, candle_time);
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "MarketDataStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def set_metadata(self, key: str, value) -> None:
        if self.read_only:
            raise RuntimeError("Salt okunur veri deposu guncellenemez")
        encoded = json.dumps(value, ensure_ascii=False)
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, encoded),
        )
        self.connection.commit()

    def get_metadata(self, key: str, default=None):
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return row[0]

    def retain_symbols(self, exchange: str, allowed_symbols: Iterable[str]) -> int:
        """Delete live-cache rows outside an explicitly trusted symbol universe."""
        if self.read_only:
            raise RuntimeError("Salt okunur veri deposu guncellenemez")
        allowed = sorted({str(symbol).strip().upper() for symbol in allowed_symbols if str(symbol).strip()})
        if not allowed:
            raise ValueError("Bos sembol evreniyle prune yapilamaz")
        placeholders = ",".join("?" for _ in allowed)
        sql = f"DELETE FROM candles WHERE exchange = ? AND UPPER(symbol) NOT IN ({placeholders})"
        with self.connection:
            cursor = self.connection.execute(sql, [str(exchange), *allowed])
        return int(cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0)

    def upsert_dataframe(
        self,
        symbol: str,
        exchange: str,
        period: str,
        df: pd.DataFrame,
        max_bars: Optional[int] = None,
    ) -> int:
        if self.read_only:
            raise RuntimeError("Salt okunur veri deposu guncellenemez")

        clean = _clean_dataframe(df)
        if clean.empty:
            return 0

        normalized_period = normalize_period(period)
        rows = []
        for timestamp, item in clean.iterrows():
            rows.append(
                (
                    str(symbol),
                    str(exchange),
                    normalized_period,
                    pd.Timestamp(timestamp).isoformat(),
                    float(item["open"]),
                    float(item["high"]),
                    float(item["low"]),
                    float(item["close"]),
                    None if pd.isna(item["volume"]) else float(item["volume"]),
                )
            )

        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO candles(
                    symbol, exchange, period, candle_time,
                    open, high, low, close, volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, exchange, period, candle_time) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume
                """,
                rows,
            )
            if max_bars and max_bars > 0:
                self.connection.execute(
                    """
                    DELETE FROM candles
                    WHERE symbol = ? AND exchange = ? AND period = ?
                      AND candle_time NOT IN (
                          SELECT candle_time FROM candles
                          WHERE symbol = ? AND exchange = ? AND period = ?
                          ORDER BY candle_time DESC
                          LIMIT ?
                      )
                    """,
                    (
                        symbol,
                        exchange,
                        normalized_period,
                        symbol,
                        exchange,
                        normalized_period,
                        int(max_bars),
                    ),
                )
        return len(rows)

    def load_dataframe(
        self,
        symbol: str,
        exchange: str,
        period: str,
        limit: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        normalized_period = normalize_period(period)
        params = [symbol, exchange, normalized_period]
        if limit and limit > 0:
            query = """
                SELECT candle_time, open, high, low, close, volume
                FROM (
                    SELECT candle_time, open, high, low, close, volume
                    FROM candles
                    WHERE symbol = ? AND exchange = ? AND period = ?
                    ORDER BY candle_time DESC
                    LIMIT ?
                )
                ORDER BY candle_time ASC
            """
            params.append(int(limit))
        else:
            query = """
                SELECT candle_time, open, high, low, close, volume
                FROM candles
                WHERE symbol = ? AND exchange = ? AND period = ?
                ORDER BY candle_time ASC
            """

        frame = pd.read_sql_query(query, self.connection, params=params)
        if frame.empty:
            return None
        frame["candle_time"] = pd.to_datetime(frame["candle_time"], errors="coerce")
        frame = frame.dropna(subset=["candle_time"]).set_index("candle_time")
        return frame.loc[:, OHLCV_COLUMNS]

    def list_symbols(self, exchange: str, period: str) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT symbol
            FROM candles
            WHERE exchange = ? AND period = ?
            ORDER BY symbol
            """,
            (exchange, normalize_period(period)),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def symbol_count(self, exchange: str, period: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(DISTINCT symbol) FROM candles WHERE exchange = ? AND period = ?",
            (exchange, normalize_period(period)),
        ).fetchone()
        return int(row[0] or 0)

    def integrity_ok(self) -> bool:
        row = self.connection.execute("PRAGMA integrity_check").fetchone()
        return bool(row and row[0] == "ok")

    def build_manifest(self) -> dict:
        rows = self.connection.execute(
            """
            SELECT exchange, period, COUNT(DISTINCT symbol), COUNT(*), MAX(candle_time)
            FROM candles
            GROUP BY exchange, period
            ORDER BY exchange, period
            """
        ).fetchall()
        return {
            "generated_at": datetime.now(TZ_TURKEY).isoformat(timespec="seconds"),
            "database": os.path.basename(self.path),
            "integrity_ok": self.integrity_ok(),
            "datasets": [
                {
                    "exchange": exchange,
                    "period": period,
                    "symbols": symbols,
                    "rows": count,
                    "latest_candle": latest,
                }
                for exchange, period, symbols, count, latest in rows
            ],
        }