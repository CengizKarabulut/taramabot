from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import borsapy as bp
import numpy as np
import pandas as pd
import requests


ISTANBUL = ZoneInfo("Europe/Istanbul")


@dataclass(frozen=True)
class Settings:
    min_score: int = 70
    minimum_history: int = 55
    min_rvol: float = 1.20
    min_turnover_mn: float = 25.0
    min_adx: float = 20.0
    min_52_high_pct: float = 75.0
    min_20_high_pct: float = 95.0
    max_dist_ema5_pct: float = 3.0
    max_dist_ema8_pct: float = 4.5
    max_dist_ema13_pct: float = 6.0
    min_atr_pct: float = 1.5
    max_atr_pct: float = 7.0
    min_clv: float = 0.60
    max_upper_wick_atr: float = 0.50
    entry_cross_fresh_bars: int = 3
    entry_cluster_mid_atr: float = 0.70
    entry_cluster_max_atr: float = 1.50
    stop_atr: float = 1.5


def rma(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def wma(series: pd.Series, length: int) -> pd.Series:
    weights = np.arange(1.0, length + 1.0)
    return series.rolling(length).apply(lambda values: float(np.dot(values, weights) / weights.sum()), raw=True)


def crossover(left: pd.Series, right: pd.Series) -> pd.Series:
    return (left > right) & (left.shift(1) <= right.shift(1))


def bars_since(condition: pd.Series) -> pd.Series:
    indexes = np.arange(len(condition), dtype=float)
    last_true = pd.Series(np.where(condition.fillna(False), indexes, np.nan), index=condition.index).ffill()
    return pd.Series(indexes, index=condition.index) - last_true


def safe_float(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else math.nan
    except (TypeError, ValueError):
        return math.nan


def truth(value: Any) -> bool:
    return bool(value) if not pd.isna(value) else False


def prepare_history(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {str(column).lower(): column for column in frame.columns}
    required = {}
    for wanted in ("open", "high", "low", "close", "volume"):
        if wanted not in rename:
            raise ValueError(f"Eksik sütun: {wanted}")
        required[rename[wanted]] = wanted.title()
    clean = frame.rename(columns=required)[list(required.values())].copy()
    for column in clean.columns:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    clean = clean.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
    clean = clean[~clean.index.duplicated(keep="last")]
    clean["Volume"] = clean["Volume"].fillna(0.0)
    return clean


def analyze_symbol(symbol: str, history: pd.DataFrame, settings: Settings, live_bar: bool) -> dict[str, Any]:
    df = prepare_history(history)
    if len(df) < 20:
        return {"symbol": symbol, "status": "YETERSİZ VERİ", "bars": len(df), "entry": False}

    o, h, l, c, v = (df[name] for name in ("Open", "High", "Low", "Close", "Volume"))
    sma = {length: c.rolling(length).mean() for length in (5, 8, 13, 21, 34, 55, 89, 144, 233)}
    ema = {length: c.ewm(span=length, adjust=False, min_periods=length).mean() for length in (5, 8, 13, 21, 34, 55, 89, 144, 233)}
    weighted = {length: wma(c, length) for length in (5, 8, 13, 21, 34, 55, 89, 144, 233)}

    fast = c.ewm(span=12, adjust=False, min_periods=12).mean()
    slow = c.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = fast - slow
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    histogram = macd - macd_signal

    smi_high = h.rolling(10).max()
    smi_low = l.rolling(10).min()
    smi_range = smi_high - smi_low
    smi_relative = c - (smi_high + smi_low) / 2.0
    smi_num = smi_relative.ewm(span=3, adjust=False, min_periods=3).mean().ewm(span=3, adjust=False, min_periods=3).mean()
    smi_den = smi_range.ewm(span=3, adjust=False, min_periods=3).mean().ewm(span=3, adjust=False, min_periods=3).mean()
    smi = pd.Series(np.where(smi_den != 0, 200.0 * smi_num / smi_den, 0.0), index=df.index)
    smi_signal = smi.ewm(span=3, adjust=False, min_periods=3).mean()

    prev_close = c.shift(1)
    true_range = pd.concat([(h - l), (h - prev_close).abs(), (l - prev_close).abs()], axis=1).max(axis=1)
    atr = rma(true_range, 14)
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    plus_di = 100.0 * rma(plus_dm, 14) / atr
    minus_di = 100.0 * rma(minus_dm, 14) / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = rma(dx.replace([np.inf, -np.inf], np.nan), 14)

    avg_volume = v.rolling(10).mean().shift(1)
    rvol = v / avg_volume
    turnover = c * v
    avg_turnover = turnover.rolling(20).mean().shift(1)
    relative_turnover = turnover / avg_turnover

    direction = np.sign(c.diff()).fillna(0.0)
    obv = (direction * v).cumsum()
    obv_ema = obv.ewm(span=20, adjust=False, min_periods=20).mean()

    bb_basis = c.rolling(20).mean()
    bb_dev = 2.0 * c.rolling(20).std(ddof=0)
    bb_width = ((bb_basis + bb_dev) - (bb_basis - bb_dev)) / bb_basis * 100.0
    bb_width_avg = bb_width.rolling(20).mean()

    high20 = h.rolling(20).max()
    previous20_high = high20.shift(1)
    rolling52 = h.rolling(252).max()
    listing_high = h.expanding().max()
    full_history = len(df) >= 252
    high52 = rolling52 if full_history else listing_high

    hist_rising = histogram > histogram.shift(1)
    hist_falling = histogram < histogram.shift(1)
    hist_positive = histogram > 0
    hist_negative = histogram < 0
    hist_cross_up = (histogram > 0) & (histogram.shift(1) <= 0)
    hist_cross_down = (histogram < 0) & (histogram.shift(1) >= 0)
    hist_bull_strengthening = hist_positive & hist_rising
    hist_bull_weakening = hist_positive & hist_falling
    hist_bear_recovering = hist_negative & hist_rising
    hist_bear_strengthening = hist_negative & hist_falling

    smi_bull_cross = crossover(smi, smi_signal)
    smi_bear_cross = crossover(smi_signal, smi)
    smi_above = smi > smi_signal
    smi_below = smi < smi_signal

    bull_cross58 = crossover(ema[5], ema[8])
    bull_cross513 = crossover(ema[5], ema[13])
    bull_cross813 = crossover(ema[8], ema[13])
    short_cross_event = bull_cross58 | bull_cross513 | bull_cross813
    since_short_cross = bars_since(short_cross_event)

    i = -1
    current = lambda series: series.iloc[i]
    previous = lambda series: series.iloc[i - 1] if len(series) >= 2 else math.nan
    close = safe_float(current(c))
    open_ = safe_float(current(o))
    high = safe_float(current(h))
    low = safe_float(current(l))
    volume = safe_float(current(v))
    atr_now = safe_float(current(atr))
    rvol_now = safe_float(current(rvol))
    adx_now = safe_float(current(adx))
    plus_di_now = safe_float(current(plus_di))
    minus_di_now = safe_float(current(minus_di))

    short_ready = len(df) >= 13 and not pd.isna(current(ema[13]))
    mid_ready = len(df) >= 55 and not pd.isna(current(ema[55]))
    long_ready = len(df) >= 233 and not pd.isna(current(ema[233]))
    history_ready = len(df) >= settings.minimum_history and mid_ready and not any(
        pd.isna(value) for value in (current(smi), current(smi_signal), atr_now, current(high52))
    )

    def above_count(lengths: tuple[int, int, int]) -> int:
        return sum(close > safe_float(current(ema[length])) for length in lengths)

    def full_above(collection: dict[int, pd.Series], lengths: tuple[int, int, int]) -> bool:
        return all(close > safe_float(current(collection[length])) for length in lengths)

    short_lengths, mid_lengths, long_lengths = (5, 8, 13), (21, 34, 55), (89, 144, 233)
    ema_short_count = above_count(short_lengths)
    ema_mid_count = above_count(mid_lengths)
    ema_long_count = above_count(long_lengths) if long_ready else 0
    ema_short_aligned = current(ema[5]) > current(ema[8]) > current(ema[13])
    ema_mid_aligned = current(ema[21]) > current(ema[34]) > current(ema[55])
    ema_long_aligned = long_ready and current(ema[89]) > current(ema[144]) > current(ema[233])
    ma_short_strong = ema_short_count == 3 and ema_short_aligned
    ma_mid_strong = ema_mid_count == 3 and ema_mid_aligned
    ma_long_strong = long_ready and ema_long_count == 3 and ema_long_aligned
    sma_short_full = full_above(sma, short_lengths)
    sma_mid_full = full_above(sma, mid_lengths)
    sma_long_full = long_ready and full_above(sma, long_lengths)
    wma_short_full = full_above(weighted, short_lengths)
    wma_mid_full = full_above(weighted, mid_lengths)
    wma_long_full = long_ready and full_above(weighted, long_lengths)
    ma_trend_core = short_ready and mid_ready and ema_short_count == 3 and ema_mid_count >= 2 and (not long_ready or ema_long_count >= 2)

    macd_above_signal = truth(current(macd > macd_signal))
    macd_above_zero = truth(current(macd > 0))
    h_bull_str = truth(current(hist_bull_strengthening))
    h_bull_weak = truth(current(hist_bull_weakening))
    h_bear_rec = truth(current(hist_bear_recovering))
    h_bear_str = truth(current(hist_bear_strengthening))
    h_cross_up = truth(current(hist_cross_up))
    h_cross_down = truth(current(hist_cross_down))
    smi_up_cross = truth(current(smi_bull_cross))
    smi_down_cross = truth(current(smi_bear_cross))
    smi_above_now = truth(current(smi_above))
    smi_below_now = truth(current(smi_below))
    smi_now = safe_float(current(smi))
    smi_above_minus40 = smi_now > -40.0
    smi_overbought = smi_now > 40.0
    smi_momentum_ok = smi_up_cross or (smi_above_now and smi_above_minus40)

    pct_52 = close / safe_float(current(high52)) * 100.0
    pct_20 = close / safe_float(current(high20)) * 100.0
    near_20 = pct_20 >= settings.min_20_high_pct
    breakout20 = close > safe_float(current(previous20_high))
    required_rvol = max(settings.min_rvol, 1.5) if breakout20 else settings.min_rvol
    participation_ok = rvol_now > required_rvol

    ema21_now = safe_float(current(ema[21]))
    dist_ema21_atr = (close - ema21_now) / atr_now

    dist_ema5_pct = (close - current(ema[5])) / current(ema[5]) * 100.0
    dist_ema8_pct = (close - current(ema[8])) / current(ema[8]) * 100.0
    dist_ema13_pct = (close - current(ema[13])) / current(ema[13]) * 100.0
    short_distance_pass_count = int(sum((
        0 <= dist_ema5_pct <= settings.max_dist_ema5_pct,
        0 <= dist_ema8_pct <= settings.max_dist_ema8_pct,
        0 <= dist_ema13_pct <= settings.max_dist_ema13_pct,
    )))
    warn_count = sum(value >= threshold for value, threshold in zip(
        (dist_ema5_pct, dist_ema8_pct, dist_ema13_pct), (2.5, 3.5, 5.0)
    ))
    high_count = sum(value >= threshold for value, threshold in zip(
        (dist_ema5_pct, dist_ema8_pct, dist_ema13_pct), (4.5, 6.0, 7.5)
    ))
    mean_reversion_high = ema_short_count == 3 and (high_count >= 2 or warn_count == 3)
    mean_reversion_warn = ema_short_count >= 2 and not mean_reversion_high and (high_count >= 1 or warn_count >= 2)

    short_cluster = (current(ema[5]) + current(ema[8]) + current(ema[13])) / 3.0
    dist_cluster_atr = (close - short_cluster) / atr_now
    short_cluster_distance_ok = 0 <= dist_cluster_atr <= settings.entry_cluster_max_atr
    distance_core = short_cluster_distance_ok and (
        short_distance_pass_count >= 2 or (breakout20 and short_distance_pass_count >= 1)
    )
    dist_ema5_atr = (close - current(ema[5])) / atr_now
    dist_ema13_atr = (close - current(ema[13])) / atr_now
    short_extension_hard = ema_short_count == 3 and dist_ema5_atr > 1.50 and dist_ema13_atr > 2.00
    short_reclaim = (
        ma_short_strong
        and low <= current(ema[8]) + 0.05 * atr_now
        and low >= current(ema[13]) - 0.25 * atr_now
        and close > current(ema[8])
        and close > open_
    )
    short_recovery = truth(current(bull_cross58)) or (
        current(ema[5]) > current(ema[8])
        and current(ema[5]) > previous(ema[5])
        and current(ema[8]) > previous(ema[8])
    )

    candle_range = high - low
    clv = (close - low) / candle_range if candle_range > 0 else 0.5
    upper_wick_atr = (high - max(open_, close)) / atr_now
    bb_width_rising = truth(current(bb_width > bb_width.shift(1)))
    bb_width_above_avg = truth(current(bb_width > bb_width_avg))
    atr_pct = atr_now / close * 100.0
    atr_pct_ok = settings.min_atr_pct <= atr_pct <= settings.max_atr_pct

    prior_ema21_dist_atr = (c - ema[21]) / atr
    was_above_ema21 = truth(current(prior_ema21_dist_atr.shift(1).rolling(10).max() >= 0.75))
    ema21_touched = low <= ema21_now + 0.25 * atr_now and low >= ema21_now - 0.50 * atr_now
    pullback_setup = (
        not breakout20
        and was_above_ema21
        and ema21_touched
        and close > ema21_now
        and dist_ema21_atr <= 1.0
        and (close > open_ or clv >= settings.min_clv)
    )
    pullback_momentum_ok = macd_above_zero and smi_momentum_ok and (h_bear_rec or h_cross_up or h_bull_str)
    pullback_short_ok = short_recovery or ma_short_strong
    breakout_momentum_ok = macd_above_signal and macd_above_zero and smi_momentum_ok and (h_bull_str or h_cross_up)
    trend_momentum_ok = breakout_momentum_ok
    breakout_quality_ok = bb_width_rising and clv >= settings.min_clv and upper_wick_atr <= settings.max_upper_wick_atr

    ma_short_score = (
        (5 if ema_short_count == 3 else 3 if ema_short_count == 2 else 1 if ema_short_count == 1 else 0)
        + (2 if ma_short_strong else 0) + (1 if sma_short_full else 0) + (1 if wma_short_full else 0)
    )
    ma_mid_score = (
        (4 if ema_mid_count == 3 else 3 if ema_mid_count == 2 else 1 if ema_mid_count == 1 else 0)
        + (2 if ma_mid_strong else 0) + (1 if sma_mid_full else 0) + (1 if wma_mid_full else 0)
    )
    ma_long_score = (
        (4 if ema_long_count == 3 else 3 if ema_long_count == 2 else 1 if ema_long_count == 1 else 0)
        + (2 if ma_long_strong else 0) + (1 if sma_long_full else 0) + (1 if wma_long_full else 0)
    )
    trend_raw = (ma_short_score if short_ready else 0) + (ma_mid_score if mid_ready else 0) + (ma_long_score if long_ready else 0)
    trend_max = (9 if short_ready else 0) + (8 if mid_ready else 0) + (8 if long_ready else 0)
    trend_score = round(trend_raw * 25.0 / trend_max) if trend_max else 0

    macd_bull_cross_above = h_cross_up and safe_float(current(macd)) > 0 and safe_float(current(macd_signal)) > 0
    macd_bull_cross_below = h_cross_up and safe_float(current(macd)) < 0 and safe_float(current(macd_signal)) < 0
    macd_bull_cross_near = h_cross_up and not macd_bull_cross_above and not macd_bull_cross_below
    macd_event_score = 11 if macd_bull_cross_above else 9 if macd_bull_cross_near else 7 if macd_bull_cross_below else 6 if h_bull_str else 4 if h_bear_rec else 2 if h_bull_weak else 0
    smi_score = 5 if smi_up_cross and smi_now <= -40 else 4 if smi_up_cross and smi_now > -40 else 1 if smi_above_now and smi_overbought else 3 if smi_above_now and smi_above_minus40 else 0
    momentum_score = (5 if macd_above_zero else 0) + (4 if macd_above_signal else 0) + macd_event_score + smi_score

    obv_above = truth(current(obv > obv_ema))
    obv_rising = safe_float(current(obv)) > safe_float(previous(obv))
    obv_positive = obv_above and obv_rising
    obv_score = 3 if obv_positive else 1 if obv_above or obv_rising else 0
    rvol_score = 15 if rvol_now >= 3 else 13 if rvol_now >= 2 else 10 if rvol_now >= 1.5 else 6 if rvol_now >= 1.2 else 3 if rvol_now >= 1 else 0
    volume_score = rvol_score + (2 if safe_float(current(relative_turnover)) >= 1 else 0) + obv_score
    di_ok = plus_di_now > minus_di_now
    adx_score = 7 if adx_now >= 40 else 6 if adx_now >= 30 else 5 if adx_now >= 25 else 3 if adx_now > 20 else 0
    strength_score = adx_score + (3 if di_ok else 0)
    location_score = (3 if pct_52 >= 95 else 2 if pct_52 >= 85 else 1 if pct_52 >= settings.min_52_high_pct else 0) + (2 if near_20 else 0)

    entry_macd_score = 3 if h_cross_up or h_bull_str else 2 if h_bull_weak else 1 if h_bear_rec else 0
    entry_smi_score = 3 if smi_up_cross and smi_now <= -40 else 2 if smi_up_cross and smi_now > -40 else 1 if smi_above_now and smi_overbought else 2 if smi_above_now and smi_above_minus40 else 0
    entry_macd_positive = h_cross_up or h_bull_str or h_bull_weak
    entry_smi_positive = smi_up_cross or (smi_above_now and smi_above_minus40)
    entry_core_present = entry_macd_positive and entry_smi_positive
    entry_short_distance_score = 2 if short_distance_pass_count == 3 else 1 if short_distance_pass_count == 2 else 0
    entry_cluster_score = 1 if 0 <= dist_cluster_atr <= settings.entry_cluster_mid_atr else 0
    cross_distance = safe_float(current(since_short_cross))
    entry_trigger_score = 1 if (ma_short_strong and math.isfinite(cross_distance) and cross_distance <= settings.entry_cross_fresh_bars) or short_reclaim else 0
    entry_score_base = entry_macd_score + entry_smi_score + entry_short_distance_score + entry_cluster_score + entry_trigger_score
    entry_score = min(entry_score_base, 6) if mean_reversion_high or short_extension_hard else entry_score_base
    volatility_score = (2 if bb_width_rising else 0) + (1 if bb_width_above_avg else 0) + (2 if atr_pct_ok else 0)
    total_score = int(trend_score + momentum_score + volume_score + strength_score + location_score + entry_score + volatility_score)

    strength_core = adx_now > settings.min_adx and di_ok
    trend_quality_core = ma_trend_core and strength_core
    score_ok = total_score >= settings.min_score
    common = history_ready and trend_quality_core and participation_ok and distance_core and score_ok and entry_core_present
    breakout_ready = common and breakout20 and ma_short_strong and breakout_momentum_ok and breakout_quality_ok
    pullback_ready = (
        history_ready and trend_quality_core and participation_ok and distance_core and score_ok
        and entry_core_present and pullback_setup and pullback_momentum_ok and pullback_short_ok
    )
    trend_ready = common and not breakout20 and not pullback_setup and ma_short_strong and trend_momentum_ok
    setup = "BREAKOUT" if breakout_ready else "PULLBACK" if pullback_ready else "TREND DEVAMI" if trend_ready else ""
    extended = history_ready and trend_quality_core and participation_ok and macd_above_signal and macd_above_zero and truth(current(hist_positive)) and smi_momentum_ok and not distance_core and total_score >= 60
    status = setup if setup else "UZAMIŞ" if extended else "İZLEME" if history_ready and total_score >= 60 else "UYGUN DEĞİL"

    avg_turnover_mn = safe_float(current(avg_turnover)) / 1_000_000.0
    liquidity_ok = avg_turnover_mn >= settings.min_turnover_mn
    live_entry = bool(setup) and liquidity_ok
    if live_entry and live_bar:
        status = setup + " · KAPANIŞ BEKLE"

    now = datetime.now(ISTANBUL)
    progress = 1.0
    if live_bar and time(10, 0) <= now.time() < time(18, 0):
        elapsed = (now.hour * 60 + now.minute) - 600
        progress = max(0.05, min(1.0, elapsed / 480.0))
    volume_pace = rvol_now / progress if math.isfinite(rvol_now) else math.nan

    return {
        "symbol": symbol,
        "date": str(pd.Timestamp(df.index[-1]).date()),
        "bar": "AÇIK" if live_bar else "KAPALI",
        "status": status,
        "setup": setup or "-",
        "entry": live_entry,
        "score": total_score,
        "trend_score": trend_score,
        "momentum_score": momentum_score,
        "participation_score": volume_score,
        "strength_score": strength_score,
        "location_score": location_score,
        "entry_score": entry_score,
        "volatility_score": volatility_score,
        "close": round(close, 4),
        "rvol": round(rvol_now, 3) if math.isfinite(rvol_now) else math.nan,
        "volume_pace": round(volume_pace, 3) if math.isfinite(volume_pace) else math.nan,
        "adx": round(adx_now, 2) if math.isfinite(adx_now) else math.nan,
        "avg_turnover_mn": round(avg_turnover_mn, 2),
        "pct_52_high": round(pct_52, 2),
        "dist_ema5_pct": round(dist_ema5_pct, 2),
        "dist_ema8_pct": round(dist_ema8_pct, 2),
        "dist_ema13_pct": round(dist_ema13_pct, 2),
        "short_distance_pass_count": short_distance_pass_count,
        "atr_pct": round(atr_pct, 2),
        "daily_stop": round(close - atr_now * settings.stop_atr, 4),
        "daily_tp1": round(close + atr_now * settings.stop_atr, 4),
        "daily_tp2": round(close + atr_now * settings.stop_atr * 2.0, 4),
        "daily_tp3": round(close + atr_now * settings.stop_atr * 3.0, 4),
        "history_mode": "TAM" if full_history else "ERKEN",
        "bars": len(df),
        "liquidity_ok": liquidity_ok,
        "mean_reversion_warn": mean_reversion_warn,
    }


def extract_downloaded(downloaded: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    if not isinstance(downloaded.columns, pd.MultiIndex):
        if len(symbols) == 1:
            result[symbols[0]] = downloaded
        return result
    level_zero = set(map(str, downloaded.columns.get_level_values(0)))
    fields = {"Open", "High", "Low", "Close", "Volume"}
    if fields.intersection(level_zero):
        available = set(map(str, downloaded.columns.get_level_values(1)))
        for symbol in symbols:
            if symbol in available:
                result[symbol] = downloaded.xs(symbol, axis=1, level=1)
    else:
        available = set(map(str, downloaded.columns.get_level_values(0)))
        for symbol in symbols:
            if symbol in available:
                result[symbol] = downloaded.xs(symbol, axis=1, level=0)
    return result


def download_universe(symbols: list[str], period: str, batch_size: int, workers: int) -> tuple[dict[str, pd.DataFrame], list[str]]:
    histories: dict[str, pd.DataFrame] = {}
    failures: list[str] = []
    batches = [symbols[start:start + batch_size] for start in range(0, len(symbols), batch_size)]

    def fetch(batch_number: int, batch: list[str]) -> tuple[int, list[str], dict[str, pd.DataFrame], str]:
        try:
            downloaded = bp.download(batch, period=period, interval="1d")
            extracted = extract_downloaded(downloaded, batch)
            return batch_number, batch, extracted, ""
        except Exception as exc:
            return batch_number, batch, {}, str(exc)

    total_batches = len(batches)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(fetch, number, batch): (number, batch)
            for number, batch in enumerate(batches, start=1)
        }
        for future in as_completed(futures):
            number, batch, extracted, error = future.result()
            if error:
                print(f"[{number}/{total_batches}] Hata: {batch[0]} ... {batch[-1]} · {error}", file=sys.stderr, flush=True)
            else:
                print(f"[{number}/{total_batches}] Tamamlandı: {batch[0]} ... {batch[-1]} · {len(extracted)}/{len(batch)}", flush=True)
            histories.update(extracted)
            failures.extend(symbol for symbol in batch if symbol not in extracted)
    return histories, sorted(set(failures))


def render_chart(symbol: str, history: pd.DataFrame, result: dict[str, Any], output_path: Path) -> None:
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError:
        return
    df = prepare_history(history).tail(120)
    if df.empty:
        return
    x = np.arange(len(df))
    fig, (price_ax, volume_ax) = plt.subplots(
        2, 1, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [4, 1]}
    )
    fig.patch.set_facecolor("#111111")
    for axis in (price_ax, volume_ax):
        axis.set_facecolor("#111111")
        axis.tick_params(colors="#d0d0d0")
        axis.grid(color="#333333", alpha=0.35)
        for spine in axis.spines.values():
            spine.set_color("#444444")
    for idx, (_, row) in enumerate(df.iterrows()):
        up = row["Close"] >= row["Open"]
        color = "#00b894" if up else "#ff3b4d"
        price_ax.vlines(idx, row["Low"], row["High"], color=color, linewidth=0.8)
        body_low = min(row["Open"], row["Close"])
        body_height = max(abs(row["Close"] - row["Open"]), max(row["Close"] * 0.0005, 0.001))
        price_ax.add_patch(Rectangle((idx - 0.33, body_low), 0.66, body_height, facecolor=color, edgecolor=color))
        volume_ax.bar(idx, row["Volume"], width=0.7, color=color, alpha=0.55)
    for length, color in ((5, "#f6c945"), (8, "#4da6ff"), (13, "#c084fc"), (21, "#ff8c42"), (55, "#e8e8e8")):
        price_ax.plot(x, df["Close"].ewm(span=length, adjust=False).mean(), color=color, linewidth=1.0, label=f"E{length}")
    price_ax.axhline(result["daily_stop"], color="#ff8c42", linestyle="--", linewidth=0.9, label="Günlük Zarar Kes")
    price_ax.axhline(result["daily_tp1"], color="#2ecc71", linestyle=":", linewidth=0.9, label="Günlük TP1")
    price_ax.set_title(
        f"{symbol} · {result['status']} · Skor {result['score']}/100 · RVOL {result['rvol']} · ADX {result['adx']}",
        color="white",
    )
    price_ax.legend(loc="upper left", ncol=7, fontsize=8, facecolor="#222222", labelcolor="white")
    step = max(1, len(df) // 10)
    ticks = x[::step]
    volume_ax.set_xticks(ticks)
    volume_ax.set_xticklabels([str(pd.Timestamp(df.index[pos]).date()) for pos in ticks], rotation=30, ha="right")
    volume_ax.set_ylabel("Hacim", color="#d0d0d0")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)


def telegram_result_lines(frame: pd.DataFrame, limit: int = 15) -> list[str]:
    lines: list[str] = []
    for _, row in frame.head(limit).iterrows():
        symbol = html.escape(str(row["symbol"]))
        status = html.escape(str(row["status"]))
        lines.append(
            f"• <b>{symbol}</b> · {int(row['score'])}/100 · {status}"
            f" · RVOL {float(row['rvol']):.2f} · ADX {float(row['adx']):.1f}"
        )
    return lines


def build_telegram_report(
    *,
    now: datetime,
    universe: str,
    analyzed_count: int,
    failed_count: int,
    live_entries: pd.DataFrame,
    closed_entries: pd.DataFrame,
    live_df: pd.DataFrame,
    min_score: int,
) -> str:
    parts = [
        "<b>📊 KARAR PANELİ · GÜNLÜK TARAMA</b>",
        f"<b>Evren:</b> {html.escape(universe.upper())}",
        f"<b>Zaman:</b> {now.strftime('%d.%m.%Y %H:%M')} (TR)",
        f"<b>Analiz:</b> {analyzed_count} hisse · <b>Eşik:</b> {min_score}/100",
    ]
    if failed_count:
        parts.append(f"<b>Eksik veri:</b> {failed_count} sembol")

    parts.extend(["", "<b>🟢 Açık mum giriş adayları</b>"])
    parts.extend(telegram_result_lines(live_entries) or ["• Yok"])

    parts.extend(["", "<b>✅ Son kapanmış mum adayları</b>"])
    parts.extend(telegram_result_lines(closed_entries) or ["• Yok"])

    watch = live_df[
        (live_df["score"] >= min_score)
        & (live_df["liquidity_ok"] == True)
        & (live_df["entry"] == False)
    ]
    parts.extend(["", f"<b>👀 {min_score}+ izleme listesi</b>"])
    parts.extend(telegram_result_lines(watch, limit=15) or ["• Yok"])
    parts.extend([
        "",
        "<i>Açık günlük mum sonuçları kapanışa kadar değişebilir. "
        "Bu çıktı yatırım tavsiyesi değildir.</i>",
    ])
    report = "\n".join(parts)
    if len(report) > 3900:
        report = report[:3800].rsplit("\n", 1)[0]
        report += "\n\n<i>Listenin devamı GitHub Actions artifact dosyalarındadır.</i>"
    return report


def send_telegram_report(text: str) -> None:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    thread_id = os.environ.get("TG_THREAD_ID", "").strip()
    if not token:
        raise RuntimeError("TG_BOT_TOKEN tanımlı değil.")
    if not chat_id:
        raise RuntimeError("TG_CHAT_ID tanımlı değil.")

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Telegram API hatası ({response.status_code}): {response.text[:500]}")
    print("Telegram raporu hedef konuya gönderildi.")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="BorsaPy ile günlük BIST Karar Paneli taraması")
    parser.add_argument("--universe", default="XU100", help="BorsaPy endeks kodu: XU100, XUTUM vb.")
    parser.add_argument("--period", default="2y", help="BorsaPy geçmiş periyodu")
    parser.add_argument("--min-score", type=int, default=70)
    parser.add_argument("--min-turnover-mn", type=float, default=25.0)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--workers", type=int, default=3, help="Paralel veri indirme işçisi")
    parser.add_argument("--max-symbols", type=int, default=0, help="0=tümü; deneme için pozitif sayı")
    parser.add_argument("--charts", type=int, default=5, help="Oluşturulacak aday/izleme grafiği sayısı")
    parser.add_argument("--output-dir", default="", help="Boşsa outputs/scan_YYYY-MM-DD")
    parser.add_argument("--telegram", action="store_true", help="Özet sonucu mevcut Telegram botuyla gönder")
    args = parser.parse_args()

    settings = Settings(min_score=args.min_score, min_turnover_mn=args.min_turnover_mn)
    symbols = list(bp.Index(args.universe).component_symbols)
    if args.max_symbols > 0:
        symbols = symbols[:args.max_symbols]
    print(f"Evren: {args.universe} · {len(symbols)} hisse · BorsaPy {getattr(bp, '__version__', '?')}")
    histories, failures = download_universe(symbols, args.period, args.batch_size, args.workers)
    if not histories:
        print("Hiç veri indirilemedi.", file=sys.stderr)
        return 2

    now = datetime.now(ISTANBUL)
    today = now.date()
    live_results: list[dict[str, Any]] = []
    closed_results: list[dict[str, Any]] = []
    for symbol, frame in histories.items():
        try:
            clean = prepare_history(frame)
            latest_date = pd.Timestamp(clean.index[-1]).date()
            has_open_today = latest_date == today and now.time() < time(18, 10)
            live_results.append(analyze_symbol(symbol, clean, settings, live_bar=has_open_today))
            closed_frame = clean.iloc[:-1] if has_open_today and len(clean) > settings.minimum_history else clean
            closed_results.append(analyze_symbol(symbol, closed_frame, settings, live_bar=False))
        except Exception as exc:
            failures.append(symbol)
            print(f"{symbol}: analiz hatası: {exc}", file=sys.stderr)

    live_df = pd.DataFrame(live_results)
    closed_df = pd.DataFrame(closed_results)
    if live_df.empty:
        print("Analiz sonucu üretilemedi.", file=sys.stderr)
        return 3
    live_df = live_df.sort_values(["entry", "score", "entry_score", "rvol"], ascending=[False, False, False, False])
    closed_df = closed_df.sort_values(["entry", "score", "entry_score", "rvol"], ascending=[False, False, False, False])

    output_dir = Path(args.output_dir) if args.output_dir else Path(__file__).resolve().parent / f"scan_{today.isoformat()}_{args.universe.upper()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    live_csv = output_dir / "gunici_acik_mum_taramasi.csv"
    closed_csv = output_dir / "son_kapanmis_mum_taramasi.csv"
    live_df.to_csv(live_csv, index=False, encoding="utf-8-sig")
    closed_df.to_csv(closed_csv, index=False, encoding="utf-8-sig")

    live_entries = live_df[live_df["entry"] == True]
    closed_entries = closed_df[closed_df["entry"] == True]
    review_pool = live_entries if not live_entries.empty else live_df[
        (live_df["score"] >= 60) & (live_df["liquidity_ok"] == True)
    ]
    review_pool = review_pool.head(max(args.charts, 0))
    chart_paths: list[str] = []
    for _, result in review_pool.iterrows():
        symbol = str(result["symbol"])
        path = output_dir / "charts" / f"{symbol}.png"
        render_chart(symbol, histories[symbol], result.to_dict(), path)
        if path.exists():
            chart_paths.append(str(path))

    summary = {
        "created_at": now.isoformat(),
        "borsapy_version": getattr(bp, "__version__", "?"),
        "universe": args.universe,
        "requested_symbols": len(symbols),
        "analyzed_symbols": len(live_df),
        "failed_symbols": sorted(set(failures)),
        "live_entry_count": len(live_entries),
        "closed_entry_count": len(closed_entries),
        "live_entries": live_entries.head(20).to_dict(orient="records"),
        "closed_entries": closed_entries.head(20).to_dict(orient="records"),
        "top_watch": live_df.head(20).to_dict(orient="records"),
        "charts": chart_paths,
        "notes": [
            "Açık günlük mum sonuçları kapanışa kadar değişebilir.",
            "Hacim temposu bilgi amaçlıdır; giriş kapısı klasik RVOL ile değerlendirilir.",
            f"Tarama güvenlik filtresi: önceki 20 gün ortalama işlem tutarı >= {settings.min_turnover_mn:.0f} Mn TRY.",
        ],
    }
    (output_dir / "ozet.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    if args.telegram:
        report = build_telegram_report(
            now=now,
            universe=args.universe,
            analyzed_count=len(live_df),
            failed_count=len(set(failures)),
            live_entries=live_entries,
            closed_entries=closed_entries,
            live_df=live_df,
            min_score=settings.min_score,
        )
        send_telegram_report(report)

    columns = ["symbol", "status", "score", "entry_score", "rvol", "volume_pace", "adx", "avg_turnover_mn", "close"]
    print("\nBUGÜN AÇIK MUMDA GİRİŞ ADAYLARI")
    print(live_entries[columns].to_string(index=False) if not live_entries.empty else "Yok")
    print("\nSON KAPANMIŞ MUMDA KESİN ADAYLAR")
    print(closed_entries[columns].to_string(index=False) if not closed_entries.empty else "Yok")
    print("\nEN YÜKSEK SKORLU İZLEME LİSTESİ")
    print(live_df[columns].head(15).to_string(index=False))
    print(f"\nÇıktı klasörü: {output_dir}")
    if failures:
        print(f"Eksik/hatalı sembol: {len(set(failures))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
