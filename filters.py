"""
Additional signal filters.

The indicator functions decide whether a setup exists. This module adds
orthogonal gates for regime, directional volume, limit-up moves, and
over-extension so noisy matches are filtered before reporting.
"""

from typing import Optional

import numpy as np
import pandas as pd

import config as cfg


def _setting(name: str, default):
    return getattr(cfg, name, default)


def _as_float(value, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def calc_atr(df: pd.DataFrame, length: Optional[int] = None) -> pd.Series:
    length = int(length or _setting("ATR_PERIOD", 14))
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def calc_rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def efficiency_ratio(close: pd.Series, length: Optional[int] = None) -> pd.Series:
    length = int(length or _setting("ER_PERIOD", 10))
    direction = (close - close.shift(length)).abs()
    volatility = close.diff().abs().rolling(length).sum()
    return direction / volatility.replace(0, np.nan)


def calc_adx(df: pd.DataFrame, length: Optional[int] = None) -> pd.Series:
    length = int(length or _setting("ADX_PERIOD", 14))
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()

    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.ewm(alpha=1 / length, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / length, adjust=False).mean()


def calc_cmf(df: pd.DataFrame, length: Optional[int] = None) -> pd.Series:
    length = int(length or _setting("CMF_PERIOD", 20))
    high, low, close, vol = df["high"], df["low"], df["close"], df["volume"]
    price_range = (high - low).replace(0, np.nan)
    multiplier = ((close - low) - (high - close)) / price_range
    money_flow = multiplier * vol
    return money_flow.rolling(length).sum() / vol.rolling(length).sum().replace(0, np.nan)


def _enough_data(df: pd.DataFrame, bars: int) -> bool:
    return df is not None and not df.empty and len(df) >= bars


def _above_ma(df: pd.DataFrame, period: int) -> bool:
    if not _enough_data(df, period):
        return False
    ma = df["close"].rolling(period).mean().iloc[-1]
    return not pd.isna(ma) and _as_float(df["close"].iloc[-1]) > _as_float(ma)


def _regime_ok(df: pd.DataFrame, mode: str = "trend", soft: bool = False) -> bool:
    if not _setting("ENABLE_REGIME_FILTER", True):
        return True

    method = str(_setting("REGIME_METHOD", "ER")).upper()
    if method == "ADX":
        value = calc_adx(df).iloc[-1]
        if pd.isna(value):
            return False
        trend_min = _setting("ADX_TREND_SOFT_MIN" if soft else "ADX_TREND_MIN", 18 if soft else 25)
        reversal_max = _setting("ADX_REVERSAL_MAX", 20)
        return value >= trend_min if mode == "trend" else value <= reversal_max

    value = efficiency_ratio(df["close"]).iloc[-1]
    if pd.isna(value):
        return False
    trend_min = _setting("ER_TREND_SOFT_MIN" if soft else "ER_TREND_MIN", 0.22 if soft else 0.30)
    reversal_max = _setting("ER_REVERSAL_MAX", 0.35)
    return value >= trend_min if mode == "trend" else value <= reversal_max


def _directional_volume_ok(df: pd.DataFrame) -> bool:
    if not _setting("ENABLE_DIRECTIONAL_VOLUME", True):
        return True
    if not _enough_data(df, max(25, int(_setting("CMF_PERIOD", 20)) + 1)):
        return False

    vol = df["volume"].astype(float)
    vol_ma = vol.rolling(20).mean().iloc[-1]
    if pd.isna(vol_ma) or vol_ma <= 0:
        return False

    last = df.iloc[-1]
    spike = vol.iloc[-1] > vol_ma * _setting("DIRECTIONAL_VOL_MULT", 1.5)
    green = _as_float(last["close"]) > _as_float(last["open"])
    cmf = calc_cmf(df).iloc[-1]
    accumulation = not pd.isna(cmf) and cmf > 0
    return spike and green and accumulation


def _near_limit_up(df: pd.DataFrame) -> bool:
    if not _setting("ENABLE_LIMIT_UP_FILTER", True) or not _enough_data(df, 2):
        return False
    prev_close = _as_float(df["close"].iloc[-2])
    if prev_close <= 0:
        return False
    change = (_as_float(df["close"].iloc[-1]) - prev_close) / prev_close * 100
    return change >= _setting("LIMIT_UP_THRESHOLD", 9.5)


def _not_limit_up(df: pd.DataFrame, daily_df: Optional[pd.DataFrame] = None) -> bool:
    limit_source = daily_df if daily_df is not None and not daily_df.empty else df
    return not _near_limit_up(limit_source)


def _ema_not_overextended(df: pd.DataFrame) -> bool:
    if not _setting("ENABLE_EXTENSION_FILTER", True):
        return True
    if not _enough_data(df, max(int(_setting("EMA_21", 21)), int(_setting("ATR_PERIOD", 14)), 20) + 1):
        return True

    close = df["close"].astype(float)
    ema21 = close.ewm(span=int(_setting("EMA_21", 21)), adjust=False).mean().iloc[-1]
    atr = calc_atr(df).iloc[-1]
    last_close = close.iloc[-1]

    if not pd.isna(atr) and atr > 0:
        max_distance = _setting("EMA_EXTENSION_ATR_MULT", 2.5) * atr
        if last_close - ema21 > max_distance:
            return False

    rsi = calc_rsi(close, 14).iloc[-1]
    return pd.isna(rsi) or rsi <= _setting("EMA_RSI_MAX", 75)


def _rsi_not_too_hot(df: pd.DataFrame) -> bool:
    if not _setting("ENABLE_RSI_CAP_FILTER", True):
        return True
    period = int(_setting("RSI_PERIOD", 7))
    if not _enough_data(df, period + 5):
        return True
    rsi = calc_rsi(df["close"].astype(float), period).iloc[-1]
    return pd.isna(rsi) or rsi <= _setting("RSI_UPPER_LIMIT", 85)


def _reversal_structure_ok(df: pd.DataFrame) -> bool:
    if not _setting("ENABLE_REVERSAL_STRUCTURE_FILTER", True):
        return True
    lookback = int(_setting("REVERSAL_LOOKBACK", 5))
    if not _enough_data(df, max(lookback + 2, int(_setting("EMA_8", 8)) + 2)):
        return False

    close = df["close"].astype(float)
    ema8 = close.ewm(span=int(_setting("EMA_8", 8)), adjust=False).mean().iloc[-1]
    last = df.iloc[-1]
    prev = df.iloc[-2]
    prior_low = df["low"].iloc[-lookback - 1:-1].min()

    reclaimed_ema8 = _as_float(last["close"]) > _as_float(ema8)
    green_reclaim = _as_float(last["close"]) > _as_float(last["open"])
    rising_close = _as_float(last["close"]) > _as_float(prev["close"])
    not_new_low = _as_float(last["low"]) >= _as_float(prior_low)
    return reclaimed_ema8 and green_reclaim and rising_close and not_new_low


def _has_daily_limit_source_needed(period: str) -> bool:
    return str(period).lower() in {"15m", "30m", "45m", "1h", "2h", "4h"}


def limit_filter_enabled() -> bool:
    return bool(_setting("ENABLE_LIMIT_UP_FILTER", True))


def should_fetch_daily_limit_df(exchange: str, period: str, has_candidate: bool) -> bool:
    return (
        has_candidate
        and limit_filter_enabled()
        and str(exchange).upper() == "BIST"
        and _has_daily_limit_source_needed(period)
    )


def candidate_signal_exists(signals: dict) -> bool:
    for strategy, data in signals.items():
        if not isinstance(data, dict):
            continue
        if strategy == "smi_macd" and (data.get("full_buy_signal") or data.get("smi_macd_buy")):
            return True
        if strategy == "i9" and (data.get("full_signal") or data.get("h8_signal")):
            return True
        if data.get("signal"):
            return True
    return False


def passes_strategy_filter(
    df: pd.DataFrame,
    strategy: str,
    signal_kind: Optional[str] = None,
    daily_df: Optional[pd.DataFrame] = None,
) -> bool:
    if df is None or df.empty:
        return False
    if not _not_limit_up(df, daily_df=daily_df):
        return False

    if strategy == "new_scan":
        return True

    if strategy == "ema":
        return _ema_not_overextended(df)

    if strategy == "i9":
        return _regime_ok(df, mode="trend", soft=True) and _directional_volume_ok(df)

    if strategy == "h8":
        return _above_ma(df, int(_setting("TREND_FLOOR_MA", 50))) and _regime_ok(df, "trend") and _directional_volume_ok(df)

    if strategy == "macd_cross":
        return _above_ma(df, int(_setting("TREND_FLOOR_MA", 50))) and _regime_ok(df, "trend") and _directional_volume_ok(df)

    if strategy == "rsi":
        return (
            _above_ma(df, int(_setting("TREND_FLOOR_MA", 50)))
            and _regime_ok(df, "trend")
            and _directional_volume_ok(df)
            and _rsi_not_too_hot(df)
        )

    if strategy == "rsi_macd":
        return (
            _above_ma(df, int(_setting("TREND_CONFIRM_MA", 200)))
            and _regime_ok(df, "trend")
            and _directional_volume_ok(df)
        )

    if strategy == "smi_macd":
        if signal_kind == "full":
            return _directional_volume_ok(df)
        return _regime_ok(df, "reversal") and _reversal_structure_ok(df) and _directional_volume_ok(df)

    return True

