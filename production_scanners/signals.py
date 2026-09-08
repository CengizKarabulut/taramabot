"""Registry-driven production scanner signal evaluation.

Only models promoted to CORE/ACTIVE/SECONDARY by the frozen common registry are
evaluated here. RESEARCH, REJECT and FORWARD_WATCH models are intentionally
kept out of normal routing.

Signal semantics are based on completed bars. ``next_bar_open`` in the locked
research specs is the historical execution reference; live detection happens
when the completed signal bar becomes available.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable

import numpy as np
import pandas as pd

from .registry import ProductionRecord, ProductionRegistry, get_production_registry


FEATURE_TAGS: dict[str, frozenset[str]] = {
    "NE ARARSAN VAR": frozenset({"trend_ema", "rsi", "macd", "volume"}),
    "StochRSI / Momentum / Hacim": frozenset({"stoch_rsi", "momentum", "volume"}),
    "BB & SMA": frozenset({"trend_sma", "volume"}),
    "RSI & MACD - RVOL": frozenset({"rsi", "macd", "volume"}),
    "7 - BB Daralması": frozenset({"bollinger", "macd", "volume"}),
    "8 - TavanTarama": frozenset({"dmi_adx", "rsi", "stoch_rsi", "volume"}),
    "9 - BulutKeser": frozenset({"ichimoku", "dmi_adx", "bollinger", "volume"}),
    "11 - Stoc.RSI & RSI & BB & MACD": frozenset(
        {"rsi", "stoch_rsi", "bollinger", "macd", "volume"}
    ),
    "13 - MACD YenidenHareket": frozenset({"macd"}),
    "14 - MACD DipDönüşü": frozenset({"macd", "volume"}),
}


@dataclass(frozen=True, slots=True)
class SignalEvidence:
    family: str
    timeframe: str
    tier: str
    quality_score: float
    triggered: bool
    features: tuple[str, ...]
    components: dict[str, bool]
    values: dict[str, float | str | None]

    def as_dict(self) -> dict:
        return asdict(self)


def _frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("OHLCV frame is empty")
    out = df.copy()
    out.columns = [str(column).strip().lower() for column in out.columns]
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(out.columns)
    if missing:
        raise ValueError(f"OHLCV frame is missing columns: {sorted(missing)}")
    for column in required:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna(subset=["high", "low", "close", "volume"]).sort_index()
    if len(out) < 3:
        raise ValueError("OHLCV frame contains too few valid bars")
    return out


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).mean()


def _wilder(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder(gain, length)
    avg_loss = _wilder(loss, length)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))
    result = result.mask((avg_loss == 0.0) & (avg_gain > 0.0), 100.0)
    result = result.mask((avg_gain == 0.0) & (avg_loss > 0.0), 0.0)
    return result


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    level = _ema(close, 12) - _ema(close, 26)
    signal = _ema(level, 9)
    return level, signal, level - signal


def _bollinger(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    basis = _sma(close, 20)
    deviation = close.rolling(20, min_periods=20).std(ddof=0)
    upper = basis + 2.0 * deviation
    lower = basis - 2.0 * deviation
    width_pct = 100.0 * (upper - lower) / basis.replace(0.0, np.nan)
    return basis, upper, lower, width_pct


def _rvol(volume: pd.Series, length: int = 20) -> pd.Series:
    reference = volume.shift(1).rolling(length, min_periods=length).mean()
    return volume / reference.replace(0.0, np.nan)


def _volume_above_previous_mean(volume: pd.Series, length: int = 10) -> pd.Series:
    reference = volume.shift(1).rolling(length, min_periods=length).mean()
    return volume > reference


def _stoch_rsi(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    rsi = _rsi(close, 14)
    rolling_low = rsi.rolling(14, min_periods=14).min()
    rolling_high = rsi.rolling(14, min_periods=14).max()
    raw = 100.0 * (rsi - rolling_low) / (rolling_high - rolling_low).replace(0.0, np.nan)
    k = raw.rolling(3, min_periods=3).mean()
    d = k.rolling(3, min_periods=3).mean()
    return k, d


def _momentum(close: pd.Series, length: int = 10) -> pd.Series:
    return close - close.shift(length)


def _dmi_adx(df: pd.DataFrame, length: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0),
        index=df.index,
        dtype=float,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0),
        index=df.index,
        dtype=float,
    )

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = _wilder(true_range, length)
    plus_di = 100.0 * _wilder(plus_dm, length) / atr.replace(0.0, np.nan)
    minus_di = 100.0 * _wilder(minus_dm, length) / atr.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx = _wilder(dx, length)
    return plus_di, minus_di, adx


def _ichimoku(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    high = df["high"]
    low = df["low"]
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2.0
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2.0
    raw_a = (tenkan + kijun) / 2.0
    raw_b = (high.rolling(52).max() + low.rolling(52).min()) / 2.0
    # Locked spec: the cloud displayed at t is the raw span computed 26 bars ago.
    displayed_a = raw_a.shift(26)
    displayed_b = raw_b.shift(26)
    return tenkan, kijun, displayed_a, displayed_b


def _cross_up(left: pd.Series, right: pd.Series | float) -> bool:
    if isinstance(right, pd.Series):
        return bool(left.iloc[-1] > right.iloc[-1] and left.iloc[-2] <= right.iloc[-2])
    return bool(left.iloc[-1] > right and left.iloc[-2] <= right)


def _fresh_episode(condition: pd.Series) -> bool:
    if len(condition) < 2:
        return False
    current = bool(condition.iloc[-1]) if pd.notna(condition.iloc[-1]) else False
    previous = bool(condition.iloc[-2]) if pd.notna(condition.iloc[-2]) else False
    return current and not previous


def _hist_rising_two(hist: pd.Series) -> pd.Series:
    return (hist > hist.shift(1)) & (hist.shift(1) > hist.shift(2))


def _relative20(width: pd.Series) -> pd.Series:
    """Current width <= 20th percentile of the previous 120 widths."""
    reference = width.shift(1).rolling(120, min_periods=120).quantile(0.20)
    return width <= reference


def _release20(width: pd.Series) -> bool:
    squeeze = _relative20(width)
    return bool(
        pd.notna(squeeze.iloc[-2])
        and bool(squeeze.iloc[-2])
        and pd.notna(width.iloc[-1])
        and pd.notna(width.iloc[-2])
        and width.iloc[-1] > width.iloc[-2]
    )


def _safe_value(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def _result(
    record: ProductionRecord,
    components: dict[str, bool],
    values: dict[str, float | str | None],
) -> SignalEvidence:
    triggered = bool(components) and all(bool(value) for value in components.values())
    return SignalEvidence(
        family=record.family,
        timeframe=record.timeframe,
        tier=record.tier,
        quality_score=record.quality_score,
        triggered=triggered,
        features=tuple(sorted(FEATURE_TAGS.get(record.family, frozenset()))),
        components=components,
        values=values,
    )


def _ne_ararsan_var(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    close, volume = df["close"], df["volume"]
    ema5, ema8, ema13 = _ema(close, 5), _ema(close, 8), _ema(close, 13)
    rsi = _rsi(close)
    _, _, hist = _macd(close)
    rvol20 = _rvol(volume, 20)

    base = (
        (close > ema5)
        & (ema5 > ema8)
        & (ema8 > ema13)
        & (rsi > 30.0)
        & (rsi < 60.0)
        & (rvol20 > 1.50)
        & _hist_rising_two(hist)
    )
    components = {
        "fresh_episode": _fresh_episode(base),
        "ema_stack": bool(close.iloc[-1] > ema5.iloc[-1] > ema8.iloc[-1] > ema13.iloc[-1]),
        "rsi_30_60": bool(30.0 < rsi.iloc[-1] < 60.0),
        "rvol20_gt_1_50": bool(rvol20.iloc[-1] > 1.50),
        "hist_rising_two_bars": bool(_hist_rising_two(hist).iloc[-1]),
    }
    return _result(
        record,
        components,
        {
            "close": _safe_value(close.iloc[-1]),
            "rsi14": _safe_value(rsi.iloc[-1]),
            "rvol20": _safe_value(rvol20.iloc[-1]),
            "macd_hist": _safe_value(hist.iloc[-1]),
        },
    )


def _stoch_momentum_volume(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    close, volume = df["close"], df["volume"]
    k, d = _stoch_rsi(close)
    mom = _momentum(close, 10)
    volume_ok = _volume_above_previous_mean(volume, 10)

    if record.timeframe == "1D":
        components = {
            "stoch_rsi_fresh_cross": _cross_up(k, d),
            "mom10_gt_zero": bool(mom.iloc[-1] > 0.0),
            "volume_gt_prev10_mean": bool(volume_ok.iloc[-1]),
        }
    else:  # 1W production
        components = {
            "mom10_fresh_cross_zero": _cross_up(mom, 0.0),
            "stoch_rsi_k_gt_d": bool(k.iloc[-1] > d.iloc[-1]),
            "volume_gt_prev10_mean": bool(volume_ok.iloc[-1]),
        }
    return _result(
        record,
        components,
        {
            "stoch_k": _safe_value(k.iloc[-1]),
            "stoch_d": _safe_value(d.iloc[-1]),
            "mom10": _safe_value(mom.iloc[-1]),
        },
    )


def _bb_sma(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    close, volume = df["close"], df["volume"]
    sma20, sma50, sma200 = _sma(close, 20), _sma(close, 50), _sma(close, 200)
    volume_ok = _volume_above_previous_mean(volume, 10)
    rising_required = record.timeframe in {"2H", "1D"}

    components = {
        "close_fresh_cross_sma20": _cross_up(close, sma20),
        "sma20_gt_sma50_gt_sma200": bool(
            sma20.iloc[-1] > sma50.iloc[-1] > sma200.iloc[-1]
        ),
        "volume_gt_prev10_mean": bool(volume_ok.iloc[-1]),
    }
    if rising_required:
        components["sma20_rising"] = bool(sma20.iloc[-1] > sma20.iloc[-2])

    return _result(
        record,
        components,
        {
            "sma20": _safe_value(sma20.iloc[-1]),
            "sma50": _safe_value(sma50.iloc[-1]),
            "sma200": _safe_value(sma200.iloc[-1]),
        },
    )


def _rsi_macd_rvol(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    close, volume = df["close"], df["volume"]
    rsi = _rsi(close)
    macd, signal, hist = _macd(close)
    rvol20 = _rvol(volume, 20)

    if record.timeframe == "4H":
        components = {
            "rsi_55_70": bool(55.0 <= rsi.iloc[-1] < 70.0),
            "macd_fresh_bullish_cross": _cross_up(macd, signal),
            "rvol20_gt_1_50": bool(rvol20.iloc[-1] > 1.50),
        }
    elif record.timeframe == "1D":
        hist_episode = _hist_rising_two(hist)
        components = {
            "rsi_50_65": bool(50.0 <= rsi.iloc[-1] < 65.0),
            "fresh_hist_rising_episode": _fresh_episode(hist_episode),
            "rvol20_gt_1_50": bool(rvol20.iloc[-1] > 1.50),
        }
    else:  # 1W
        components = {
            "rsi_50_65": bool(50.0 <= rsi.iloc[-1] < 65.0),
            "macd_fresh_bullish_cross": _cross_up(macd, signal),
            "rvol20_gt_1_50": bool(rvol20.iloc[-1] > 1.50),
        }
    return _result(
        record,
        components,
        {
            "rsi14": _safe_value(rsi.iloc[-1]),
            "macd": _safe_value(macd.iloc[-1]),
            "macd_signal": _safe_value(signal.iloc[-1]),
            "macd_hist": _safe_value(hist.iloc[-1]),
            "rvol20": _safe_value(rvol20.iloc[-1]),
        },
    )


def _bb_squeeze(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    close, volume = df["close"], df["volume"]
    basis, upper, lower, width = _bollinger(close)
    macd, signal, hist = _macd(close)
    volume_ok = _volume_above_previous_mean(volume, 10)

    if record.timeframe == "4H":
        components = {
            "relative20_squeeze": bool(_relative20(width).iloc[-1]),
            "macd_fresh_bullish_cross": _cross_up(macd, signal),
            "macd_level_gt_zero": bool(macd.iloc[-1] > 0.0),
            "volume_gt_prev10_mean": bool(volume_ok.iloc[-1]),
        }
    elif record.timeframe == "1D":
        components = {
            "bb_width_pct_lte_10": bool(width.iloc[-1] <= 10.0),
            "macd_level_gt_zero": bool(macd.iloc[-1] > 0.0),
            "fresh_hist_rising_episode": _fresh_episode(_hist_rising_two(hist)),
            "volume_gt_prev10_mean": bool(volume_ok.iloc[-1]),
        }
    else:  # 1W
        components = {
            "release20": _release20(width),
            "macd_level_gt_zero": bool(macd.iloc[-1] > 0.0),
            "fresh_hist_rising_episode": _fresh_episode(_hist_rising_two(hist)),
            "volume_gt_prev10_mean": bool(volume_ok.iloc[-1]),
        }

    return _result(
        record,
        components,
        {
            "bb_basis": _safe_value(basis.iloc[-1]),
            "bb_upper": _safe_value(upper.iloc[-1]),
            "bb_lower": _safe_value(lower.iloc[-1]),
            "bb_width_pct": _safe_value(width.iloc[-1]),
            "macd": _safe_value(macd.iloc[-1]),
            "macd_hist": _safe_value(hist.iloc[-1]),
        },
    )


def _tavan(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    close, volume = df["close"], df["volume"]
    rsi = _rsi(close)
    k, d = _stoch_rsi(close)
    plus_di, minus_di, adx = _dmi_adx(df)
    rvol20 = _rvol(volume, 20)
    dmi_trigger = bool(
        plus_di.iloc[-1] > minus_di.iloc[-1]
        and plus_di.iloc[-1] > adx.iloc[-1]
        and plus_di.iloc[-2] <= adx.iloc[-2]
    )

    if record.timeframe == "1D":
        components = {
            "original_pdi_cross_adx": dmi_trigger,
            "rsi_50_70": bool(50.0 <= rsi.iloc[-1] < 70.0),
            "stoch_rsi_fresh_cross": _cross_up(k, d),
            "rvol20_gt_1_50": bool(rvol20.iloc[-1] > 1.50),
        }
    else:  # 1W
        components = {
            "original_pdi_cross_adx": dmi_trigger,
            "rsi_40_70": bool(40.0 <= rsi.iloc[-1] < 70.0),
            "stoch_rsi_k_gt_d": bool(k.iloc[-1] > d.iloc[-1]),
            "rvol20_gt_1_50": bool(rvol20.iloc[-1] > 1.50),
        }

    return _result(
        record,
        components,
        {
            "plus_di": _safe_value(plus_di.iloc[-1]),
            "minus_di": _safe_value(minus_di.iloc[-1]),
            "adx14": _safe_value(adx.iloc[-1]),
            "rsi14": _safe_value(rsi.iloc[-1]),
            "rvol20": _safe_value(rvol20.iloc[-1]),
        },
    )


def _bulut_keser(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    close, volume = df["close"], df["volume"]
    tenkan, kijun, span_a, span_b = _ichimoku(df)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bottom = pd.concat([span_a, span_b], axis=1).min(axis=1)
    _, _, adx = _dmi_adx(df)
    basis, upper, lower, _ = _bollinger(close)
    rvol20 = _rvol(volume, 20)
    cross = _cross_up(tenkan, kijun)

    if record.timeframe == "4H":
        components = {
            "tenkan_kijun_fresh_cross": cross,
            "inside_kumo": bool(
                cloud_bottom.iloc[-1] <= close.iloc[-1] <= cloud_top.iloc[-1]
            ),
            "adx_gt_20": bool(adx.iloc[-1] > 20.0),
            "inside_bollinger": bool(lower.iloc[-1] < close.iloc[-1] < upper.iloc[-1]),
            "rvol20_gt_1_20": bool(rvol20.iloc[-1] > 1.20),
        }
    elif record.timeframe == "1D":
        components = {
            "tenkan_kijun_fresh_cross": cross,
            "below_kumo": bool(close.iloc[-1] < cloud_bottom.iloc[-1]),
            "adx_20_35": bool(20.0 <= adx.iloc[-1] <= 35.0),
            "bb_basis_lt_close_lt_upper": bool(
                basis.iloc[-1] < close.iloc[-1] < upper.iloc[-1]
            ),
            "rvol20_gt_1_50": bool(rvol20.iloc[-1] > 1.50),
        }
    else:  # 1W
        components = {
            "tenkan_kijun_fresh_cross": cross,
            "above_kumo": bool(close.iloc[-1] > cloud_top.iloc[-1]),
            "adx_20_35": bool(20.0 <= adx.iloc[-1] <= 35.0),
            "rvol20_gt_1_50": bool(rvol20.iloc[-1] > 1.50),
        }

    return _result(
        record,
        components,
        {
            "tenkan": _safe_value(tenkan.iloc[-1]),
            "kijun": _safe_value(kijun.iloc[-1]),
            "cloud_top": _safe_value(cloud_top.iloc[-1]),
            "cloud_bottom": _safe_value(cloud_bottom.iloc[-1]),
            "adx14": _safe_value(adx.iloc[-1]),
            "rvol20": _safe_value(rvol20.iloc[-1]),
        },
    )


def _scan11(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    close, volume = df["close"], df["volume"]
    rsi = _rsi(close)
    k, d = _stoch_rsi(close)
    basis, _, _, _ = _bollinger(close)
    macd, signal, _ = _macd(close)
    rvol20 = _rvol(volume, 20)

    components = {
        "rsi_gt_30": bool(rsi.iloc[-1] > 30.0),
        "bb_basis_fresh_reclaim": _cross_up(close, basis),
        "stoch_rsi_k_gt_d": bool(k.iloc[-1] > d.iloc[-1]),
        "macd_level_gt_signal": bool(macd.iloc[-1] > signal.iloc[-1]),
        "rvol20_gt_1_50": bool(rvol20.iloc[-1] > 1.50),
    }
    return _result(
        record,
        components,
        {
            "rsi14": _safe_value(rsi.iloc[-1]),
            "stoch_k": _safe_value(k.iloc[-1]),
            "stoch_d": _safe_value(d.iloc[-1]),
            "bb_basis": _safe_value(basis.iloc[-1]),
            "macd": _safe_value(macd.iloc[-1]),
            "macd_signal": _safe_value(signal.iloc[-1]),
            "rvol20": _safe_value(rvol20.iloc[-1]),
        },
    )


def _macd_yeniden(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    close = df["close"]
    macd, signal, hist = _macd(close)

    if record.timeframe in {"4H", "1D"}:
        episode = (macd > 0.0) & (macd > signal) & _hist_rising_two(hist)
        components = {
            "macd_level_gt_zero": bool(macd.iloc[-1] > 0.0),
            "macd_level_gt_signal": bool(macd.iloc[-1] > signal.iloc[-1]),
            "fresh_positive_hist_reacceleration": _fresh_episode(episode),
        }
    else:  # 1W
        components = {
            "macd_level_gt_zero": bool(macd.iloc[-1] > 0.0),
            "macd_fresh_bullish_cross": _cross_up(macd, signal),
        }

    return _result(
        record,
        components,
        {
            "macd": _safe_value(macd.iloc[-1]),
            "macd_signal": _safe_value(signal.iloc[-1]),
            "macd_hist": _safe_value(hist.iloc[-1]),
        },
    )


def _macd_dip(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    close, volume = df["close"], df["volume"]
    macd, signal, _ = _macd(close)
    components = {
        "macd_level_lt_zero": bool(macd.iloc[-1] < 0.0),
        "macd_fresh_bullish_cross": _cross_up(macd, signal),
    }
    values: dict[str, float | str | None] = {
        "macd": _safe_value(macd.iloc[-1]),
        "macd_signal": _safe_value(signal.iloc[-1]),
    }
    if record.timeframe == "4H":
        rvol20 = _rvol(volume, 20)
        components["rvol20_gt_1_50"] = bool(rvol20.iloc[-1] > 1.50)
        values["rvol20"] = _safe_value(rvol20.iloc[-1])

    return _result(record, components, values)


_EVALUATORS: dict[str, Callable[[pd.DataFrame, ProductionRecord], SignalEvidence]] = {
    "NE ARARSAN VAR": _ne_ararsan_var,
    "StochRSI / Momentum / Hacim": _stoch_momentum_volume,
    "BB & SMA": _bb_sma,
    "RSI & MACD - RVOL": _rsi_macd_rvol,
    "7 - BB Daralması": _bb_squeeze,
    "8 - TavanTarama": _tavan,
    "9 - BulutKeser": _bulut_keser,
    "11 - Stoc.RSI & RSI & BB & MACD": _scan11,
    "13 - MACD YenidenHareket": _macd_yeniden,
    "14 - MACD DipDönüşü": _macd_dip,
}


def evaluate_record(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    """Evaluate one promoted family/timeframe model on the latest completed bar."""
    if not record.routable:
        raise ValueError(f"{record.family}/{record.timeframe} is not routable production")
    evaluator = _EVALUATORS.get(record.family)
    if evaluator is None:
        raise KeyError(f"No production evaluator implemented for {record.family}")
    frame = _frame(df)
    return evaluator(frame, record)


def evaluate_timeframe(
    df: pd.DataFrame,
    timeframe: str,
    *,
    registry: ProductionRegistry | None = None,
    only_triggered: bool = True,
) -> list[SignalEvidence]:
    """Evaluate every promoted model for one timeframe.

    This function is intentionally side-effect free: no state writes and no
    Telegram routing. It is suitable for shadow-mode parity checks first.
    """
    selected_registry = registry or get_production_registry()
    evidence = [
        evaluate_record(df, record)
        for record in selected_registry.production_records(timeframe=timeframe)
    ]
    return [item for item in evidence if item.triggered] if only_triggered else evidence
