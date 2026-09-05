"""Canonical BIST timeframe alignment for the cached live snapshot.

TvDatafeed stores BIST timestamps as naive UTC values.  The production cache
keeps direct TradingView history for depth, then updates recent bars from 15m.
These helpers reproduce TradingView candle labels so incremental rows replace
(they do not overlap) direct rows.
"""

from __future__ import annotations

import pandas as pd

from market_data_store import OHLCV_COLUMNS, normalize_period


# TradingView labels BIST daily/weekly/monthly bars at 09:00 Europe/Istanbul,
# which is 06:00 UTC year-round because Türkiye is UTC+3.
TV_DAILY_LABEL_UTC_HOUR = 6


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    clean = df.copy()
    clean.columns = [str(column).lower() for column in clean.columns]
    clean = clean.loc[:, OHLCV_COLUMNS]
    clean.index = pd.to_datetime(clean.index, errors="coerce")
    clean = clean[~clean.index.isna()]
    clean = clean[~clean.index.duplicated(keep="last")].sort_index()
    for column in OHLCV_COLUMNS:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    return clean.dropna(subset=["open", "high", "low", "close"])


def _aggregate(frame: pd.DataFrame, buckets: pd.DatetimeIndex) -> pd.DataFrame:
    clean = _clean(frame)
    if clean.empty:
        return clean
    grouped = clean.groupby(pd.DatetimeIndex(buckets), sort=True)
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
    """Aggregate 15m bars to TradingView-compatible BIST clock buckets."""
    clean = _clean(df_15m)
    period = normalize_period(target_period)
    if clean.empty or period == "15m":
        return clean

    index = pd.DatetimeIndex(clean.index)
    if period == "30m":
        buckets = index.floor("30min")
    elif period == "45m":
        buckets = index.floor("45min")
    elif period == "1H":
        buckets = index.floor("1h")
    elif period == "2H":
        buckets = index.floor("2h")
    elif period == "4H":
        # TradingView BIST 4H bars are labelled 06:00, 10:00, 14:00 UTC.
        offset = pd.Timedelta(hours=2)
        buckets = (index - offset).floor("4h") + offset
    else:
        raise ValueError(f"Desteklenmeyen gun ici periyot: {target_period}")
    return _aggregate(clean, buckets)


def resample_daily(df_15m: pd.DataFrame) -> pd.DataFrame:
    clean = _clean(df_15m)
    if clean.empty:
        return clean
    buckets = clean.index.normalize() + pd.Timedelta(hours=TV_DAILY_LABEL_UTC_HOUR)
    return _aggregate(clean, buckets)


def resample_calendar(df_daily: pd.DataFrame, target_period: str) -> pd.DataFrame:
    clean = _clean(df_daily)
    if clean.empty:
        return clean
    naive = clean.index.tz_localize(None) if clean.index.tz is not None else clean.index
    period = normalize_period(target_period)
    if period == "1W":
        starts = naive.to_period("W-SUN").start_time
    elif period == "1M":
        starts = naive.to_period("M").start_time
    else:
        raise ValueError(f"Desteklenmeyen takvim periyodu: {target_period}")
    buckets = pd.DatetimeIndex(starts) + pd.Timedelta(hours=TV_DAILY_LABEL_UTC_HOUR)
    return _aggregate(clean, buckets)
