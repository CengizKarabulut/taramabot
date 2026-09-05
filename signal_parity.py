"""Vectorized A-I scanner parity engine.

This module mirrors the production scanner formulas over a whole OHLCV frame so
historical replay does not have to recompute the complete prefix on every bar.
The canonical external codes are the same codes used by Telegram:

A=macd_cross, B=h8, C=i9, D=ema, E=rsi_macd, F=new_scan,
G=smi_macd_full, H=smi_macd (early), I=rsi.

For BIST intraday periods the production limit-up gate uses the current session
price against the previous DAILY close.  ``previous_daily_close`` reproduces
that rule without look-ahead.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd


CODE_TO_STRATEGY: Mapping[str, str] = {
    "A": "macd_cross",
    "B": "h8",
    "C": "i9",
    "D": "ema",
    "E": "rsi_macd",
    "F": "new_scan",
    "G": "smi_macd_full",
    "H": "smi_macd",
    "I": "rsi",
}
STRATEGY_TO_CODE = {value: key for key, value in CODE_TO_STRATEGY.items()}
STRATEGIES = tuple(CODE_TO_STRATEGY.values())


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi_indicator(close: pd.Series, length: int) -> pd.Series:
    """Match indicators.calc_rsi (first delta remains NaN)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _rsi_filter(close: pd.Series, length: int) -> pd.Series:
    """Match filters.calc_rsi (initial change is converted to zero)."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr_filter(frame: pd.DataFrame, length: int = 14) -> pd.Series:
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def _efficiency_ratio(close: pd.Series, length: int = 10) -> pd.Series:
    direction = (close - close.shift(length)).abs()
    noise = close.diff().abs().rolling(length).sum()
    return direction / noise.replace(0, np.nan)


def _cmf(frame: pd.DataFrame, length: int = 20) -> pd.Series:
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    volume = frame["volume"].astype(float)
    price_range = (high - low).replace(0, np.nan)
    multiplier = ((close - low) - (high - close)) / price_range
    money_flow = multiplier * volume
    return money_flow.rolling(length).sum() / volume.rolling(length).sum().replace(0, np.nan)


def previous_daily_close(
    intraday: pd.DataFrame,
    daily: pd.DataFrame | None = None,
) -> pd.Series:
    """Return the confirmed previous-session close aligned to every intraday bar.

    When a daily frame is supplied, only daily rows strictly before the current
    intraday session are used.  The fallback derives session closes from the
    intraday frame itself, which is useful for fixtures/tests.
    """
    target_index = intraday.index
    intraday_dates = pd.to_datetime(target_index).normalize()

    if daily is not None and not daily.empty:
        daily_close = pd.to_numeric(daily["close"], errors="coerce")
        daily_dates = pd.to_datetime(daily.index).normalize()
        # Keep the last record if a source contains duplicate session dates.
        by_date = pd.Series(daily_close.to_numpy(), index=daily_dates).groupby(level=0).last().sort_index()
    else:
        close = pd.to_numeric(intraday["close"], errors="coerce")
        by_date = pd.Series(close.to_numpy(), index=intraday_dates).groupby(level=0).last().sort_index()

    prev_by_date = by_date.shift(1)
    aligned = pd.Series(intraday_dates, index=target_index).map(prev_by_date)
    aligned.index = target_index
    return pd.to_numeric(aligned, errors="coerce")


def build_signal_frame(
    frame: pd.DataFrame,
    *,
    daily: pd.DataFrame | None = None,
    warmup: int = 240,
) -> pd.DataFrame:
    """Calculate final production-equivalent A-I states for every bar."""
    if frame is None or frame.empty:
        return pd.DataFrame(index=getattr(frame, "index", None))

    data = frame.copy()
    data.columns = [str(column).lower() for column in data.columns]
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"Eksik OHLCV sutunlari: {sorted(missing)}")

    open_ = pd.to_numeric(data["open"], errors="coerce")
    high = pd.to_numeric(data["high"], errors="coerce")
    low = pd.to_numeric(data["low"], errors="coerce")
    close = pd.to_numeric(data["close"], errors="coerce")
    volume = pd.to_numeric(data["volume"], errors="coerce")

    sma5 = close.rolling(5).mean()
    sma8 = close.rolling(8).mean()
    sma21 = close.rolling(21).mean()
    sma50 = close.rolling(50).mean()
    sma55 = close.rolling(55).mean()
    sma200 = close.rolling(200).mean()

    ema5 = _ema(close, 5)
    ema8 = _ema(close, 8)
    ema13 = _ema(close, 13)
    ema21 = _ema(close, 21)
    ema55 = _ema(close, 55)
    ema200 = _ema(close, 200)

    macd = _ema(close, 12) - _ema(close, 26)
    macd_signal = _ema(macd, 9)
    hist = macd - macd_signal
    macd_cross_or_rise = (
        ((macd.shift(1) <= macd_signal.shift(1)) & (macd > macd_signal))
        | ((macd > macd_signal) & (macd > macd.shift(1)))
    )

    hh10 = high.rolling(10).max()
    ll10 = low.rolling(10).min()
    width10 = hh10 - ll10
    relative10 = close - (hh10 + ll10) / 2.0
    smi_num = _ema(_ema(relative10, 3), 3)
    smi_den = _ema(_ema(width10, 3), 3).replace(0, 0.000001)
    smi = 200.0 * smi_num / smi_den
    smi_ref = _ema(smi, 3)
    smi_cross_or_rise = (
        ((smi.shift(1) <= smi_ref.shift(1)) & (smi > smi_ref))
        | ((smi > smi_ref) & (smi > smi.shift(1)))
    )

    rsi7 = _rsi_indicator(close, 7)
    rsi14 = _rsi_indicator(close, 14)
    filter_rsi7 = _rsi_filter(close, 7)
    filter_rsi14 = _rsi_filter(close, 14)
    atr14 = _atr_filter(data, 14)

    vol10 = volume.rolling(10).mean()
    vol20 = volume.rolling(20).mean()

    er = _efficiency_ratio(close, 10)
    trend_regime = er >= 0.30
    soft_trend_regime = er >= 0.22
    reversal_regime = er <= 0.35

    cmf20 = _cmf(data, 20)
    directional_volume = (volume > vol20 * 1.5) & (close > open_) & (cmf20 > 0)

    prior_low5 = low.shift(1).rolling(5).min()
    reversal_structure = (
        (close > ema8)
        & (close > open_)
        & (close > close.shift(1))
        & (low >= prior_low5)
    )
    ema_extension_ok = ((close - ema21) <= atr14 * 2.5) & (filter_rsi14.isna() | (filter_rsi14 <= 75))
    rsi_heat_ok = filter_rsi7.isna() | (filter_rsi7 <= 85)

    prev_daily = previous_daily_close(data, daily=daily)
    near_limit = prev_daily.gt(0) & (((close - prev_daily) / prev_daily) * 100.0 >= 9.5)
    limit_ok = ~near_limit.fillna(False)

    # H / G: SMI-MACD negative-region early/full pair.
    sm_core = smi_cross_or_rise & (smi < 0) & (hist < 0) & (hist > hist.shift(1))
    sm_full_raw = sm_core & (close > sma200) & (volume > vol20 * 1.5)
    sm_early_raw = sm_core & ~sm_full_raw
    sm_early = sm_early_raw & reversal_regime & reversal_structure & directional_volume
    sm_full = sm_full_raw & directional_volume

    # I: RSI + volume.
    rsi_cross_or_rise = (
        ((rsi7.shift(1) <= 50) & (rsi7 > 50))
        | ((rsi7 > 50) & (rsi7 > rsi7.shift(1)))
    )
    rsi_raw = (rsi7 > 60) & rsi_cross_or_rise & (volume > vol10 * 1.5)
    rsi_final = rsi_raw & (close > sma50) & trend_regime & directional_volume & rsi_heat_ok

    # F: SMA stack + MACD + volume.
    new_raw = (
        (volume > vol20 * 1.5)
        & (close > sma5)
        & (close > sma8)
        & (close > sma21)
        & (close > sma50)
        & (close > sma55)
        & (close > sma200)
        & (macd > 0)
        & macd_cross_or_rise
    )
    new_final = new_raw

    # E: RSI14 + MACD + directional confirmation.
    rsi14_up = (
        ((rsi14.shift(1) <= 50) & (rsi14 > 50))
        | ((rsi14 > 50) & (rsi14 > rsi14.shift(1)))
    )
    rsi_macd_raw = rsi14_up & (rsi14 < 70) & macd_cross_or_rise & (volume > vol20 * 1.5)
    rsi_macd_final = rsi_macd_raw & (close > sma200) & trend_regime & directional_volume

    # D: EMA trend stack.
    ev4 = (
        ((ema8.shift(1) <= ema13.shift(1)) & (ema8 > ema13))
        | ((ema8 > ema13) & (ema8 > ema8.shift(1)))
    )
    ev5 = (
        ((ema5.shift(1) <= ema8.shift(1)) & (ema5 > ema8))
        | ((ema5 > ema8) & (ema5 > ema5.shift(1)))
    )
    ev6 = (
        ((ema5.shift(1) <= ema13.shift(1)) & (ema5 > ema13))
        | ((ema5 > ema13) & (ema5 > ema5.shift(1)))
    )
    ema_raw = (
        (close > ema200)
        & (close > ema55)
        & (close > ema21)
        & ev4 & ev5 & ev6
        & (volume > vol20 * 1.5)
    )
    ema_final = ema_raw & ema_extension_ok

    # A: positive MACD cross/rise.
    macd_raw = (rsi14 > 30) & macd_cross_or_rise & (macd > 0)
    macd_final = macd_raw & (close > sma50) & trend_regime & directional_volume

    # B / C: positive SMI-MACD continuation / full confirmation.
    h8_raw = smi_cross_or_rise & (smi > 0) & (hist > 0) & (hist > hist.shift(1))
    h8_final = h8_raw & (close > sma50) & trend_regime & directional_volume
    i9_raw = h8_raw & (close > sma200) & (volume > vol20 * 1.5)
    i9_final = i9_raw & soft_trend_regime & directional_volume

    by_strategy = {
        "macd_cross": macd_final,
        "h8": h8_final,
        "i9": i9_final,
        "ema": ema_final,
        "rsi_macd": rsi_macd_final,
        "new_scan": new_final,
        "smi_macd_full": sm_full,
        "smi_macd": sm_early,
        "rsi": rsi_final,
    }

    output = pd.DataFrame(index=data.index)
    for strategy, series in by_strategy.items():
        active = (series & limit_ok).fillna(False).astype(bool)
        if warmup > 0 and len(active):
            active.iloc[: min(warmup, len(active))] = False
        output[strategy] = active
        output[f"fresh_{strategy}"] = active & ~active.shift(1, fill_value=False)
        output[STRATEGY_TO_CODE[strategy]] = active
        output[f"fresh_{STRATEGY_TO_CODE[strategy]}"] = output[f"fresh_{strategy}"]

    output["previous_daily_close"] = prev_daily
    output["near_limit"] = near_limit.fillna(False)
    return output


def fresh_signal_indexes(signal_frame: pd.DataFrame, strategy: str) -> list[int]:
    if strategy not in STRATEGIES:
        raise ValueError(f"Bilinmeyen strateji: {strategy}")
    column = f"fresh_{strategy}"
    values = signal_frame[column].to_numpy(dtype=bool)
    # Last bar cannot open a next-bar trade in historical simulation.
    if len(values):
        values[-1] = False
    return np.flatnonzero(values).astype(int).tolist()
