"""Karar Paneli v2 - merged Pine decision/confirmation model for the BIST snapshot.

The engine keeps correlated indicators in capped factor groups:
trend, momentum breadth, participation/flow and volatility regime.  It also
uses the shared strategy-aware exit engine for setup-specific trade plans.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import borsapy as bp
import numpy as np
import pandas as pd
import requests

from exit_engine import build_exit_plan
from market_data_store import MarketDataStore


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
    technical_confirm_min: int = 7
    technical_conflict_high: int = 3
    multi_volume_factor: float = 1.20
    flow_len: int = 20
    oscillator_len: int = 14
    cloud_fast_len: int = 9
    cloud_base_len: int = 26
    cloud_slow_len: int = 52
    cloud_shift: int = 26
    supertrend_atr_len: int = 10
    supertrend_factor: float = 3.0
    squeeze_kc_factor: float = 1.5


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


def safe_float(value: Any, default: float = math.nan) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def truth(value: Any) -> bool:
    try:
        return False if pd.isna(value) else bool(value)
    except Exception:
        return bool(value)


def prepare_history(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {str(column).lower(): column for column in frame.columns}
    mapping = {}
    for wanted in ("open", "high", "low", "close", "volume"):
        if wanted not in rename:
            raise ValueError(f"Eksik sütun: {wanted}")
        mapping[rename[wanted]] = wanted.title()
    clean = frame.rename(columns=mapping)[list(mapping.values())].copy()
    for column in clean.columns:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    clean = clean.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
    clean = clean[~clean.index.duplicated(keep="last")]
    clean["Volume"] = clean["Volume"].fillna(0.0)
    return clean


def calc_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ], axis=1,
    ).max(axis=1)
    return rma(tr, length)


def calc_adx(df: pd.DataFrame, length: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    high, low = df["High"], df["Low"]
    atr = calc_atr(df, length)
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    plus_di = 100.0 * rma(plus_dm, length) / atr.replace(0, np.nan)
    minus_di = 100.0 * rma(minus_dm, length) / atr.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = rma(dx.replace([np.inf, -np.inf], np.nan), length)
    return adx, plus_di, minus_di


def calc_smi(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    high10 = df["High"].rolling(10).max()
    low10 = df["Low"].rolling(10).min()
    span = high10 - low10
    rel = df["Close"] - (high10 + low10) / 2.0
    num = rel.ewm(span=3, adjust=False).mean().ewm(span=3, adjust=False).mean()
    den = span.ewm(span=3, adjust=False).mean().ewm(span=3, adjust=False).mean()
    smi = pd.Series(np.where(den != 0, 200.0 * num / den, 0.0), index=df.index)
    return smi, smi.ewm(span=3, adjust=False).mean()


def supertrend_direction(df: pd.DataFrame, length: int, factor: float) -> pd.Series:
    atr = calc_atr(df, length)
    hl2 = (df["High"] + df["Low"]) / 2.0
    upper_basic = hl2 + factor * atr
    lower_basic = hl2 - factor * atr
    upper = upper_basic.copy()
    lower = lower_basic.copy()
    direction = pd.Series(1, index=df.index, dtype=int)  # +1 bearish, -1 bullish (Pine convention)
    for pos in range(1, len(df)):
        prev = pos - 1
        if pd.notna(upper.iloc[prev]):
            upper.iloc[pos] = (
                upper_basic.iloc[pos]
                if upper_basic.iloc[pos] < upper.iloc[prev] or df["Close"].iloc[prev] > upper.iloc[prev]
                else upper.iloc[prev]
            )
        if pd.notna(lower.iloc[prev]):
            lower.iloc[pos] = (
                lower_basic.iloc[pos]
                if lower_basic.iloc[pos] > lower.iloc[prev] or df["Close"].iloc[prev] < lower.iloc[prev]
                else lower.iloc[prev]
            )
        if direction.iloc[prev] < 0:
            direction.iloc[pos] = 1 if df["Close"].iloc[pos] < lower.iloc[pos] else -1
        else:
            direction.iloc[pos] = -1 if df["Close"].iloc[pos] > upper.iloc[pos] else 1
    return direction


def _score_ratio(bull: int, ready: int, maximum: int) -> int:
    return int(round(float(bull) * maximum / ready)) if ready > 0 else 0


def analyze_symbol(symbol: str, history: pd.DataFrame, settings: Settings, live_bar: bool) -> dict[str, Any]:
    df = prepare_history(history)
    if len(df) < 20:
        return {"symbol": symbol, "status": "YETERSİZ VERİ", "bars": len(df), "entry": False}

    o, h, l, c, v = (df[name] for name in ("Open", "High", "Low", "Close", "Volume"))
    sma = {n: c.rolling(n).mean() for n in (5, 8, 13, 21, 34, 55, 89, 144, 233)}
    ema = {n: c.ewm(span=n, adjust=False, min_periods=n).mean() for n in (5, 8, 13, 21, 34, 55, 89, 144, 233)}
    weighted = {n: wma(c, n) for n in (5, 8, 13, 21, 34, 55, 89, 144, 233)}

    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - macd_signal
    smi, smi_signal = calc_smi(df)
    atr = calc_atr(df, 14)
    adx, plus_di, minus_di = calc_adx(df, 14)

    avg_volume = v.rolling(10).mean().shift(1)
    rvol = v / avg_volume.replace(0, np.nan)
    turnover = c * v
    avg_turnover = turnover.rolling(20).mean().shift(1)
    relative_turnover = turnover / avg_turnover.replace(0, np.nan)
    direction = np.sign(c.diff()).fillna(0.0)
    obv = (direction * v).cumsum()
    obv_ema = obv.ewm(span=20, adjust=False).mean()

    bb_basis = c.rolling(20).mean()
    bb_dev = 2.0 * c.rolling(20).std(ddof=0)
    bb_upper, bb_lower = bb_basis + bb_dev, bb_basis - bb_dev
    bb_width = (bb_upper - bb_lower) / bb_basis.replace(0, np.nan) * 100.0
    bb_width_avg = bb_width.rolling(20).mean()

    high20 = h.rolling(20).max()
    previous20_high = high20.shift(1)
    rolling52 = h.rolling(252).max()
    listing_high = h.expanding().max()
    full_history = len(df) >= 252
    high52 = rolling52 if full_history else listing_high

    hist_rising = histogram > histogram.shift(1)
    hist_positive = histogram > 0
    hist_negative = histogram < 0
    hist_cross_up = hist_positive & (histogram.shift(1) <= 0)
    hist_bull_strengthening = hist_positive & hist_rising
    hist_bull_weakening = hist_positive & (histogram < histogram.shift(1))
    hist_bear_recovering = hist_negative & hist_rising
    smi_bull_cross = crossover(smi, smi_signal)
    smi_above = smi > smi_signal
    smi_below = smi < smi_signal

    bull_cross58 = crossover(ema[5], ema[8])
    bull_cross513 = crossover(ema[5], ema[13])
    bull_cross813 = crossover(ema[8], ema[13])
    short_cross_event = bull_cross58 | bull_cross513 | bull_cross813
    since_short_cross = bars_since(short_cross_event)

    i = -1
    cur = lambda series: series.iloc[i]
    prev = lambda series: series.iloc[-2] if len(series) >= 2 else math.nan
    close, open_, high, low = map(float, (cur(c), cur(o), cur(h), cur(l)))
    atr_now = safe_float(cur(atr), max(close * 0.02, 0.0001))
    rvol_now = safe_float(cur(rvol), 0.0)
    adx_now = safe_float(cur(adx), 0.0)
    plus_di_now, minus_di_now = safe_float(cur(plus_di), 0.0), safe_float(cur(minus_di), 0.0)

    short_ready = len(df) >= 13 and pd.notna(cur(ema[13]))
    mid_ready = len(df) >= 55 and pd.notna(cur(ema[55]))
    long_ready = len(df) >= 233 and pd.notna(cur(ema[233]))
    history_ready = len(df) >= settings.minimum_history and mid_ready and pd.notna(cur(smi)) and pd.notna(cur(high52))

    def above_count(lengths: tuple[int, int, int]) -> int:
        return sum(close > safe_float(cur(ema[n]), math.inf) for n in lengths)

    def full_above(collection: dict[int, pd.Series], lengths: tuple[int, int, int]) -> bool:
        return all(close > safe_float(cur(collection[n]), math.inf) for n in lengths)

    short_lengths, mid_lengths, long_lengths = (5, 8, 13), (21, 34, 55), (89, 144, 233)
    ema_short_count = above_count(short_lengths)
    ema_mid_count = above_count(mid_lengths)
    ema_long_count = above_count(long_lengths) if long_ready else 0
    ma_short_strong = short_ready and ema_short_count == 3 and cur(ema[5]) > cur(ema[8]) > cur(ema[13])
    ma_mid_strong = mid_ready and ema_mid_count == 3 and cur(ema[21]) > cur(ema[34]) > cur(ema[55])
    ma_long_strong = long_ready and ema_long_count == 3 and cur(ema[89]) > cur(ema[144]) > cur(ema[233])
    ma_trend_core = short_ready and mid_ready and ema_short_count == 3 and ema_mid_count >= 2 and (not long_ready or ema_long_count >= 2)

    macd_above_signal = truth(cur(macd > macd_signal))
    macd_above_zero = truth(cur(macd > 0))
    h_cross_up = truth(cur(hist_cross_up))
    h_bull_str = truth(cur(hist_bull_strengthening))
    h_bull_weak = truth(cur(hist_bull_weakening))
    h_bear_rec = truth(cur(hist_bear_recovering))
    smi_up_cross = truth(cur(smi_bull_cross))
    smi_above_now = truth(cur(smi_above))
    smi_below_now = truth(cur(smi_below))
    smi_now = safe_float(cur(smi), 0.0)
    smi_above_minus40 = smi_now > -40.0
    smi_overbought = smi_now > 40.0
    smi_momentum_ok = smi_up_cross or (smi_above_now and smi_above_minus40)

    pct_52 = close / max(safe_float(cur(high52), close), 1e-12) * 100.0
    pct_20 = close / max(safe_float(cur(high20), close), 1e-12) * 100.0
    near_20 = pct_20 >= settings.min_20_high_pct
    breakout20 = close > safe_float(cur(previous20_high), math.inf)
    required_rvol = max(settings.min_rvol, 1.5) if breakout20 else settings.min_rvol
    participation_ok = rvol_now > required_rvol

    dist_ema5_pct = (close / cur(ema[5]) - 1.0) * 100.0
    dist_ema8_pct = (close / cur(ema[8]) - 1.0) * 100.0
    dist_ema13_pct = (close / cur(ema[13]) - 1.0) * 100.0
    short_distance_pass_count = sum((
        0 <= dist_ema5_pct <= settings.max_dist_ema5_pct,
        0 <= dist_ema8_pct <= settings.max_dist_ema8_pct,
        0 <= dist_ema13_pct <= settings.max_dist_ema13_pct,
    ))
    short_cluster = (cur(ema[5]) + cur(ema[8]) + cur(ema[13])) / 3.0
    dist_cluster_atr = (close - short_cluster) / atr_now
    distance_core = 0 <= dist_cluster_atr <= settings.entry_cluster_max_atr and (
        short_distance_pass_count >= 2 or (breakout20 and short_distance_pass_count >= 1)
    )
    warn_count = sum(value >= threshold for value, threshold in zip(
        (dist_ema5_pct, dist_ema8_pct, dist_ema13_pct), (2.5, 3.5, 5.0)
    ))
    high_count = sum(value >= threshold for value, threshold in zip(
        (dist_ema5_pct, dist_ema8_pct, dist_ema13_pct), (4.5, 6.0, 7.5)
    ))
    mean_reversion_high = ema_short_count == 3 and (high_count >= 2 or warn_count == 3)
    mean_reversion_warn = ema_short_count >= 2 and not mean_reversion_high and (high_count >= 1 or warn_count >= 2)
    short_extension_hard = ema_short_count == 3 and (close - cur(ema[5])) / atr_now > 1.5 and (close - cur(ema[13])) / atr_now > 2.0

    candle_range = max(high - low, 1e-12)
    clv = (close - low) / candle_range
    upper_wick_atr = (high - max(open_, close)) / atr_now
    bb_width_rising = truth(cur(bb_width > bb_width.shift(1)))
    bb_width_above_avg = truth(cur(bb_width > bb_width_avg))
    atr_pct = atr_now / close * 100.0
    atr_pct_ok = settings.min_atr_pct <= atr_pct <= settings.max_atr_pct

    # ------------------------- grouped technical confirmation -------------------------
    cloud_conversion = (l.rolling(settings.cloud_fast_len).min() + h.rolling(settings.cloud_fast_len).max()) / 2.0
    cloud_base = (l.rolling(settings.cloud_base_len).min() + h.rolling(settings.cloud_base_len).max()) / 2.0
    cloud_a = ((cloud_conversion + cloud_base) / 2.0).shift(settings.cloud_shift)
    cloud_b = ((l.rolling(settings.cloud_slow_len).min() + h.rolling(settings.cloud_slow_len).max()) / 2.0).shift(settings.cloud_shift)
    cloud_ready = pd.notna(cur(cloud_a)) and pd.notna(cur(cloud_b)) and pd.notna(cur(cloud_base))
    cloud_top, cloud_bottom = max(safe_float(cur(cloud_a), close), safe_float(cur(cloud_b), close)), min(safe_float(cur(cloud_a), close), safe_float(cur(cloud_b), close))
    cloud_bull = cloud_ready and close > cloud_top and cur(cloud_conversion) > cur(cloud_base) and close > cur(cloud_base)
    cloud_bear = cloud_ready and close < cloud_bottom and cur(cloud_conversion) < cur(cloud_base) and close < cur(cloud_base)
    st_direction = supertrend_direction(df, settings.supertrend_atr_len, settings.supertrend_factor)
    st_ready = pd.notna(cur(st_direction))
    st_bull, st_bear = st_ready and cur(st_direction) < 0, st_ready and cur(st_direction) > 0
    trend_confirm_ready = int(cloud_ready) + int(st_ready)
    trend_confirm_bull = int(cloud_bull) + int(st_bull)
    trend_confirm_bear = int(cloud_bear) + int(st_bear)
    trend_confirmation_score4 = _score_ratio(trend_confirm_bull, trend_confirm_ready, 4)
    trend_confirmation_score2 = _score_ratio(trend_confirm_bull, trend_confirm_ready, 2)

    delta = c.diff()
    gains, losses = delta.clip(lower=0), (-delta).clip(lower=0)
    rsi = 100 - 100 / (1 + gains.ewm(alpha=1/settings.oscillator_len, adjust=False).mean() / losses.ewm(alpha=1/settings.oscillator_len, adjust=False).mean().replace(0, np.nan))
    rsi_avg = rsi.ewm(span=5, adjust=False).mean()
    cci_mean = ((h+l+c)/3).rolling(settings.oscillator_len).mean()
    cci_dev = ((h+l+c)/3).rolling(settings.oscillator_len).apply(lambda x: np.mean(np.abs(x-np.mean(x))), raw=True)
    cci = (((h+l+c)/3) - cci_mean) / (0.015 * cci_dev.replace(0, np.nan))
    cci_avg = cci.ewm(span=5, adjust=False).mean()
    rsi_low = rsi.rolling(settings.oscillator_len).min()
    rsi_high = rsi.rolling(settings.oscillator_len).max()
    stoch_raw = 100 * (rsi-rsi_low)/(rsi_high-rsi_low).replace(0, np.nan)
    stoch_k = stoch_raw.rolling(3).mean(); stoch_d = stoch_k.rolling(3).mean()
    rsi_bull = cur(rsi) > 50 and cur(rsi) > cur(rsi_avg) and cur(rsi) < 75
    cci_bull = cur(cci) > 0 and cur(cci) > cur(cci_avg)
    stoch_bull = cur(stoch_k) > cur(stoch_d) and cur(stoch_k) < 85
    rsi_bear = cur(rsi) < 45 and cur(rsi) < cur(rsi_avg)
    cci_bear = cur(cci) < 0 and cur(cci) < cur(cci_avg)
    stoch_bear = cur(stoch_k) < cur(stoch_d) and cur(stoch_k) > 15
    momentum_bull_count = sum(map(bool, (rsi_bull, cci_bull, stoch_bull)))
    momentum_bear_count = sum(map(bool, (rsi_bear, cci_bear, stoch_bear)))
    momentum_breadth_score = _score_ratio(momentum_bull_count, 3, 3)

    flow_range = (h-l).replace(0, np.nan)
    flow_multiplier = ((c-l)-(h-c))/flow_range
    flow_money = flow_multiplier * v
    flow_value = flow_money.rolling(settings.flow_len).sum() / v.rolling(settings.flow_len).sum().replace(0, np.nan)
    flow_now = safe_float(cur(flow_value), 0.0)
    flow_bull, flow_strong_bull = flow_now > 0, flow_now > 0.10
    flow_bear, flow_strong_bear = flow_now < 0, flow_now < -0.10
    flow_score = 2 if flow_strong_bull else 1 if flow_bull else 0

    volume_passes, volume_ready = 0, 0
    for length in (5, 13, 34, 89):
        reference = v.rolling(length).mean().shift(1)
        ref = safe_float(cur(reference), 0.0)
        if ref > 0:
            volume_ready += 1
            volume_passes += int(float(cur(v)) >= ref * settings.multi_volume_factor)
    volume_ratio = volume_passes / volume_ready if volume_ready else math.nan
    multi_volume_score = 2 if volume_ready and volume_ratio >= .75 else 1 if volume_ready and volume_ratio >= .50 else 0
    multi_volume_negative = multi_volume_score == 2 and close < open_ and clv <= .35

    squeeze_basis = c.ewm(span=20, adjust=False).mean()
    squeeze_atr = calc_atr(df, 20)
    squeeze_upper = squeeze_basis + settings.squeeze_kc_factor*squeeze_atr
    squeeze_lower = squeeze_basis - settings.squeeze_kc_factor*squeeze_atr
    squeeze_on = (bb_upper < squeeze_upper) & (bb_lower > squeeze_lower)
    squeeze_release = truth(cur(squeeze_on.shift(1))) and not truth(cur(squeeze_on))
    bullish_squeeze_release = squeeze_release and close > cur(bb_basis) and close > open_ and clv >= .60
    bearish_squeeze_release = squeeze_release and close < cur(bb_basis) and close < open_ and clv <= .40
    price_regime_score = int(close > safe_float(cur(bb_basis), close) and not bearish_squeeze_release)

    obv_above = truth(cur(obv > obv_ema)); obv_rising = cur(obv) > prev(obv)
    obv_positive = obv_above and obv_rising
    obv_negative = cur(obv) < cur(obv_ema) and not obv_rising
    technical_confirm_score = trend_confirmation_score2 + momentum_breadth_score + flow_score + multi_volume_score + price_regime_score
    technical_conflict_count = sum((
        trend_confirm_ready > 0 and trend_confirm_bull == 0 and trend_confirm_bear > 0,
        momentum_bull_count <= 1 and momentum_bear_count >= 2,
        flow_strong_bear and obv_negative,
        multi_volume_negative,
        bearish_squeeze_release,
    ))
    technical_strong = technical_confirm_score >= settings.technical_confirm_min and technical_conflict_count <= 1
    technical_weak = technical_confirm_score <= 4 or technical_conflict_count >= settings.technical_conflict_high
    technical_hard_conflict = bearish_squeeze_release or multi_volume_negative or (flow_strong_bear and obv_negative)

    # Pullback/entry geometry.
    ema21_now = safe_float(cur(ema[21]), close)
    prior_ema21_dist = (c-ema[21])/atr.replace(0, np.nan)
    was_above_ema21 = truth(cur(prior_ema21_dist.shift(1).rolling(10).max() >= .75))
    ema21_touched = low <= ema21_now+.25*atr_now and low >= ema21_now-.50*atr_now
    pullback_setup = not breakout20 and was_above_ema21 and ema21_touched and close > ema21_now and (close-ema21_now)/atr_now <= 1 and (close > open_ or clv >= settings.min_clv)
    short_recovery = truth(cur(bull_cross58)) or (cur(ema[5]) > cur(ema[8]) and cur(ema[5]) > prev(ema[5]) and cur(ema[8]) > prev(ema[8]))
    pullback_momentum_ok = macd_above_zero and smi_momentum_ok and (h_bear_rec or h_cross_up or h_bull_str)
    pullback_short_ok = short_recovery or ma_short_strong
    breakout_momentum_ok = macd_above_signal and macd_above_zero and smi_momentum_ok and (h_bull_str or h_cross_up)
    breakout_quality_ok = bb_width_rising and clv >= settings.min_clv and upper_wick_atr <= settings.max_upper_wick_atr

    # Factor-capped 100 point model: 25/25/20/10/5/10/5.
    ma_short_score = (5 if ema_short_count==3 else 3 if ema_short_count==2 else 1 if ema_short_count==1 else 0) + 2*int(ma_short_strong) + int(full_above(sma, short_lengths)) + int(full_above(weighted, short_lengths))
    ma_mid_score = (4 if ema_mid_count==3 else 3 if ema_mid_count==2 else 1 if ema_mid_count==1 else 0) + 2*int(ma_mid_strong) + int(full_above(sma, mid_lengths)) + int(full_above(weighted, mid_lengths))
    ma_long_score = ((4 if ema_long_count==3 else 3 if ema_long_count==2 else 1 if ema_long_count==1 else 0) + 2*int(ma_long_strong) + int(full_above(sma, long_lengths)) + int(full_above(weighted, long_lengths))) if long_ready else 0
    trend_raw = (ma_short_score if short_ready else 0)+(ma_mid_score if mid_ready else 0)+(ma_long_score if long_ready else 0)
    trend_available_max = (9 if short_ready else 0)+(8 if mid_ready else 0)+(8 if long_ready else 0)
    trend_ma_score = round(trend_raw*21/trend_available_max) if trend_available_max else 0
    trend_score = min(25, trend_ma_score+trend_confirmation_score4)

    macd_event_score = 8 if h_cross_up and cur(macd)>0 and cur(macd_signal)>0 else 7 if h_cross_up else 5 if h_bull_str else 3 if h_bear_rec else 1 if h_bull_weak else 0
    smi_score = 5 if smi_up_cross and smi_now <= -40 else 4 if smi_up_cross else 1 if smi_above_now and smi_overbought else 3 if smi_above_now and smi_above_minus40 else 0
    momentum_score = min(25, 5*int(macd_above_zero)+4*int(macd_above_signal)+macd_event_score+smi_score+momentum_breadth_score)
    rvol_score = 11 if rvol_now>=3 else 10 if rvol_now>=2 else 8 if rvol_now>=1.5 else 5 if rvol_now>=1.2 else 2 if rvol_now>=1 else 0
    obv_score = 3 if obv_positive else 1 if obv_above or obv_rising else 0
    volume_score = min(20, rvol_score + 2*int(safe_float(cur(relative_turnover),0)>=1) + obv_score + flow_score + multi_volume_score)
    di_ok = plus_di_now > minus_di_now
    adx_score = 7 if adx_now>=40 else 6 if adx_now>=30 else 5 if adx_now>=25 else 3 if adx_now>20 else 0
    strength_score = adx_score + 3*int(di_ok)
    location_score = (3 if pct_52>=95 else 2 if pct_52>=85 else 1 if pct_52>=settings.min_52_high_pct else 0)+2*int(near_20)
    entry_macd_score = 3 if h_cross_up or h_bull_str else 2 if h_bull_weak else 1 if h_bear_rec else 0
    entry_smi_score = 3 if smi_up_cross and smi_now<=-40 else 2 if smi_up_cross else 1 if smi_above_now and smi_overbought else 2 if smi_above_now and smi_above_minus40 else 0
    entry_core_present = (h_cross_up or h_bull_str or h_bull_weak) and (smi_up_cross or (smi_above_now and smi_above_minus40))
    entry_short_distance_score = 2 if short_distance_pass_count==3 else 1 if short_distance_pass_count==2 else 0
    entry_cluster_score = int(0 <= dist_cluster_atr <= settings.entry_cluster_mid_atr)
    cross_distance = safe_float(cur(since_short_cross), math.inf)
    short_reclaim = ma_short_strong and low <= cur(ema[8])+.05*atr_now and low >= cur(ema[13])-.25*atr_now and close > cur(ema[8]) and close > open_
    entry_trigger_score = int((ma_short_strong and cross_distance <= settings.entry_cross_fresh_bars) or short_reclaim or bullish_squeeze_release)
    entry_technical_score = entry_short_distance_score+entry_cluster_score+entry_trigger_score
    entry_score = entry_macd_score+entry_smi_score+entry_technical_score
    if mean_reversion_high or short_extension_hard: entry_score = min(entry_score,6)
    volatility_score = 2*int(bb_width_rising)+int(bb_width_above_avg)+2*int(atr_pct_ok)
    total_score = int(trend_score+momentum_score+volume_score+strength_score+location_score+entry_score+volatility_score)

    strength_core = adx_now > settings.min_adx and di_ok
    trend_quality_core = ma_trend_core and strength_core
    common = history_ready and trend_quality_core and participation_ok and distance_core and total_score>=settings.min_score and entry_core_present
    breakout_ready = common and breakout20 and ma_short_strong and breakout_momentum_ok and breakout_quality_ok
    pullback_ready = history_ready and trend_quality_core and participation_ok and distance_core and total_score>=settings.min_score and entry_core_present and pullback_setup and pullback_momentum_ok and pullback_short_ok
    trend_ready = common and not breakout20 and not pullback_setup and ma_short_strong and breakout_momentum_ok
    setup = "BREAKOUT" if breakout_ready else "PULLBACK" if pullback_ready else "TREND DEVAMI" if trend_ready else ""
    avg_turnover_mn = safe_float(cur(avg_turnover),0)/1_000_000
    liquidity_ok = avg_turnover_mn >= settings.min_turnover_mn
    entry = bool(setup) and liquidity_ok
    extended = history_ready and trend_quality_core and participation_ok and macd_above_signal and macd_above_zero and truth(cur(hist_positive)) and smi_momentum_ok and not distance_core and total_score>=60
    status = setup if setup else "UZAMIŞ" if extended else "İZLEME" if history_ready and total_score>=60 else "UYGUN DEĞİL"
    if entry: status += " · TEYİTLİ" if technical_strong else " · KARIŞIK" if technical_weak else ""
    if entry and live_bar: status += " · KAPANIŞ BEKLE"

    trend_phase = (
        "BOZULMA" if ema_short_count<=1 and truth(cur(hist_negative)) and obv_negative else
        "UZAMIŞ" if short_extension_hard or mean_reversion_high else
        "ERKEN DÖNÜŞ" if h_bear_rec and smi_up_cross and obv_positive else
        "YENİ TREND" if truth(cur(short_cross_event)) and entry_core_present and obv_positive else
        "GÜÇLENEN" if ma_trend_core and h_bull_str and smi_momentum_ok and obv_positive else
        "ZAYIFLAMA" if ma_trend_core and (h_bull_weak or smi_below_now or obv_negative) else
        "OLGUN TREND" if ma_trend_core else "GEÇİŞ"
    )
    base_risk_level = 3 if short_extension_hard or mean_reversion_high else 2 if not atr_pct_ok else 1 if mean_reversion_warn else 0
    technical_risk = 2 if technical_hard_conflict else 1 if technical_weak else 0
    combined_risk = max(base_risk_level, technical_risk)
    risk_structure_score = 4 if combined_risk==0 else 3 if combined_risk==1 else 1 if combined_risk==2 else 0
    risk_score = min(10, entry_macd_score+entry_smi_score+risk_structure_score)

    plan = None
    if setup:
        try:
            plan = build_exit_plan(df, "decision_panel", setup=setup, entry_price=close)
        except Exception:
            plan = None

    return {
        "symbol":symbol,"date":str(pd.Timestamp(df.index[-1]).date()),"bar":"AÇIK" if live_bar else "KAPALI",
        "status":status,"setup":setup or "-","entry":entry,"score":total_score,"trend_score":trend_score,
        "momentum_score":momentum_score,"participation_score":volume_score,"strength_score":strength_score,
        "location_score":location_score,"entry_score":entry_score,"volatility_score":volatility_score,
        "technical_confirm_score":technical_confirm_score,"technical_conflicts":technical_conflict_count,
        "technical_confirmation":"GÜÇLÜ" if technical_strong else "KARIŞIK" if technical_weak else "ORTA",
        "technical_hard_conflict":technical_hard_conflict,"trend_phase":trend_phase,"risk_score":risk_score,
        "close":round(close,4),"rvol":round(rvol_now,3),"adx":round(adx_now,2),"avg_turnover_mn":round(avg_turnover_mn,2),
        "pct_52_high":round(pct_52,2),"atr_pct":round(atr_pct,2),"liquidity_ok":liquidity_ok,
        "flow":round(flow_now,3),"multi_volume_score":multi_volume_score,"squeeze_release":"YUKARI" if bullish_squeeze_release else "AŞAĞI" if bearish_squeeze_release else "-",
        "relative_strength":"BENCHMARK SNAPSHOT BEKLİYOR","mean_reversion_warn":mean_reversion_warn,"history_mode":"TAM" if full_history else "ERKEN","bars":len(df),
        "exit_plan":plan,
        "stop":round(plan["stop"],4) if plan else math.nan,"tp1":round(plan["tp1"],4) if plan else math.nan,
        "tp2":round(plan["tp2"],4) if plan else math.nan,"tp3":round(plan["tp3"],4) if plan else math.nan,
        "stop_pct":round(plan["stop_pct"],2) if plan else math.nan,"tp1_pct":round(plan["tp1_pct"],2) if plan else math.nan,
        "tp2_pct":round(plan["tp2_pct"],2) if plan else math.nan,"tp3_pct":round(plan["tp3_pct"],2) if plan else math.nan,
    }


def load_snapshot_universe(database_path: str, max_symbols: int=0) -> tuple[list[str],dict[str,pd.DataFrame],list[str]]:
    histories={}; failures=[]
    with MarketDataStore(database_path,read_only=True) as store:
        symbols=store.list_symbols("BIST","1D")
        if max_symbols>0: symbols=symbols[:max_symbols]
        for symbol in symbols:
            frame=store.load_dataframe(symbol,"BIST","1D",limit=400)
            if frame is None or frame.empty: failures.append(symbol)
            else: histories[symbol]=frame
    return symbols,histories,failures


def download_universe(symbols:list[str],period:str,batch_size:int,workers:int):
    histories={}; failures=[]
    for start in range(0,len(symbols),batch_size):
        batch=symbols[start:start+batch_size]
        try:
            data=bp.download(batch,period=period,interval="1d")
            if isinstance(data.columns,pd.MultiIndex):
                fields=set(map(str,data.columns.get_level_values(0)))
                for symbol in batch:
                    try:
                        frame=data.xs(symbol,axis=1,level=1 if {"Open","High","Low","Close"}&fields else 0)
                        histories[symbol]=frame
                    except Exception: failures.append(symbol)
            elif len(batch)==1: histories[batch[0]]=data
        except Exception: failures.extend(batch)
    return histories,sorted(set(failures))


def result_lines(frame:pd.DataFrame,limit:int=15)->list[str]:
    lines=[]
    for _,row in frame.head(limit).iterrows():
        plan=""
        if pd.notna(row.get("stop")):
            plan=f" · 🛑 {row['stop']:.2f} · 🎯 {row['tp1']:.2f}/{row['tp2']:.2f}/{row['tp3']:.2f}"
        lines.append(f"• <b>{html.escape(str(row['symbol']))}</b> · {int(row['score'])}/100 · {html.escape(str(row['status']))} · Teyit {int(row.get('technical_confirm_score',0))}/10{plan}")
    return lines


def build_telegram_report(now,universe,analyzed_count,failed_count,live_entries,closed_entries,live_df,min_score):
    parts=["<b>📊 KARAR PANELİ v2 · GÜNLÜK TARAMA</b>",f"<b>Evren:</b> {html.escape(universe.upper())}",f"<b>Zaman:</b> {now.strftime('%d.%m.%Y %H:%M')} (TR)",f"<b>Analiz:</b> {analyzed_count} hisse · <b>Eşik:</b> {min_score}/100"]
    if failed_count: parts.append(f"<b>Eksik veri:</b> {failed_count}")
    parts += ["","<b>🟢 Açık mum giriş adayları</b>"] + (result_lines(live_entries) or ["• Yok"])
    parts += ["","<b>✅ Son kapanmış mum adayları</b>"] + (result_lines(closed_entries) or ["• Yok"])
    watch=live_df[(live_df["score"]>=min_score)&(live_df["liquidity_ok"]==True)&(live_df["entry"]==False)]
    parts += ["",f"<b>👀 {min_score}+ izleme listesi</b>"] + (result_lines(watch,10) or ["• Yok"])
    report="\n".join(parts)
    return report if len(report)<=3900 else report[:3850]+"…"


def send_telegram(text:str):
    token=os.getenv("TG_BOT_TOKEN","").strip(); chat=os.getenv("TG_CHAT_ID","").strip(); thread=os.getenv("TG_THREAD_ID","").strip()
    if not token or not chat: raise RuntimeError("Telegram ayarları eksik")
    payload={"chat_id":chat,"text":text,"parse_mode":"HTML","disable_web_page_preview":True}
    if thread: payload["message_thread_id"]=thread
    response=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",json=payload,timeout=30); response.raise_for_status()


def _signature(frame:pd.DataFrame)->list[str]:
    return sorted(str(x) for x in frame.get("symbol",pd.Series(dtype=str)).tolist())


def send_if_changed(report,now,live_entries,closed_entries,state_file,only_on_change):
    path=Path(state_file) if state_file else None; state={}
    if path and path.exists():
        try: state=json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception: state={}
    sig={"date":now.date().isoformat(),"live_entries":_signature(live_entries),"closed_entries":_signature(closed_entries)}
    if only_on_change and state.get("decision_panel_v2",{}).get("last_signature")==sig: return False
    send_telegram(report)
    if path:
        state["decision_panel_v2"]={"last_signature":sig,"last_sent_at":now.isoformat(timespec="seconds")}
        path.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return True


def main()->int:
    parser=argparse.ArgumentParser(description="BIST Karar Paneli v2")
    parser.add_argument("--universe",default="XU100"); parser.add_argument("--period",default="2y")
    parser.add_argument("--snapshot-db",default=""); parser.add_argument("--state-file",default="")
    parser.add_argument("--telegram-on-change",action="store_true"); parser.add_argument("--min-score",type=int,default=70)
    parser.add_argument("--min-turnover-mn",type=float,default=25.0); parser.add_argument("--batch-size",type=int,default=20)
    parser.add_argument("--workers",type=int,default=3); parser.add_argument("--max-symbols",type=int,default=0)
    parser.add_argument("--charts",type=int,default=0); parser.add_argument("--output-dir",default=""); parser.add_argument("--telegram",action="store_true")
    args=parser.parse_args(); settings=Settings(min_score=args.min_score,min_turnover_mn=args.min_turnover_mn)
    if args.snapshot_db: symbols,histories,failures=load_snapshot_universe(args.snapshot_db,args.max_symbols); source="SQLite snapshot"
    else:
        symbols=list(bp.Index(args.universe).component_symbols); symbols=symbols[:args.max_symbols] if args.max_symbols>0 else symbols
        histories,failures=download_universe(symbols,args.period,args.batch_size,args.workers); source="BorsaPy"
    now=datetime.now(ISTANBUL); today=now.date(); live=[]; closed=[]
    for symbol,frame in histories.items():
        try:
            clean=prepare_history(frame); open_today=pd.Timestamp(clean.index[-1]).date()==today and now.time()<time(18,10)
            live.append(analyze_symbol(symbol,clean,settings,open_today)); closed_frame=clean.iloc[:-1] if open_today and len(clean)>settings.minimum_history else clean
            closed.append(analyze_symbol(symbol,closed_frame,settings,False))
        except Exception as exc: failures.append(symbol); print(f"{symbol}: {exc}",file=sys.stderr)
    live_df=pd.DataFrame(live); closed_df=pd.DataFrame(closed)
    if live_df.empty: return 3
    live_df=live_df.sort_values(["entry","score","entry_score","rvol"],ascending=[False,False,False,False]); closed_df=closed_df.sort_values(["entry","score","entry_score","rvol"],ascending=[False,False,False,False])
    live_entries=live_df[live_df["entry"]==True]; closed_entries=closed_df[closed_df["entry"]==True]
    out=Path(args.output_dir) if args.output_dir else Path(f"scan_{today.isoformat()}_{args.universe.upper()}"); out.mkdir(parents=True,exist_ok=True)
    live_df.to_csv(out/"gunici_acik_mum_taramasi.csv",index=False,encoding="utf-8-sig"); closed_df.to_csv(out/"son_kapanmis_mum_taramasi.csv",index=False,encoding="utf-8-sig")
    summary={"created_at":now.isoformat(),"engine":"decision_panel_v2","data_source":source,"universe":args.universe,"analyzed_symbols":len(live_df),"failed_symbols":sorted(set(failures)),"live_entries":live_entries.head(25).to_dict("records"),"closed_entries":closed_entries.head(25).to_dict("records"),"top_watch":live_df.head(25).to_dict("records"),"notes":["Teknik teyit grupları korelasyon tekrarını sınırlayacak şekilde tavanlıdır.","Relatif güç mevcut snapshot XU100 benchmark serisi eklenene kadar bilgi amaçlı beklemededir.","Stop/TP seviyeleri strategy_exit_profiles başlangıç profilleridir; tarihsel optimizasyon sonucu değildir."]}
    (out/"ozet.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    if args.telegram:
        report=build_telegram_report(now,args.universe,len(live_df),len(set(failures)),live_entries,closed_entries,live_df,args.min_score)
        send_if_changed(report,now,live_entries,closed_entries,args.state_file,args.telegram_on_change)
    cols=["symbol","status","score","technical_confirm_score","technical_conflicts","trend_phase","risk_score","stop","tp1","tp2","tp3","rvol","adx","close"]
    print(live_df[cols].head(30).to_string(index=False)); return 0


if __name__=="__main__": raise SystemExit(main())
