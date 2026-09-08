"""Vectorized historical signal evaluator for frozen production scanners.

This module exists for research-to-production parity and shadow replay. It
returns one boolean signal series per promoted family/timeframe using the same
completed-bar semantics as the locked research specifications.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .registry import ProductionRecord


def _frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(out.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    for c in required:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.sort_index()


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def _wilder(s: pd.Series, length: int) -> pd.Series:
    return s.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    au = _wilder(up, length)
    ad = _wilder(dn, length)
    rs = au / ad.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(ad != 0.0, 100.0)


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    level = _ema(close, 12) - _ema(close, 26)
    signal = _ema(level, 9)
    return level, signal, level - signal


def _stoch_rsi(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    r = _rsi(close, 14)
    lo = r.rolling(14, min_periods=14).min()
    hi = r.rolling(14, min_periods=14).max()
    raw = 100.0 * (r - lo) / (hi - lo).replace(0.0, np.nan)
    k = raw.rolling(3, min_periods=3).mean()
    d = k.rolling(3, min_periods=3).mean()
    return k, d


def _rvol(volume: pd.Series, length: int) -> pd.Series:
    avg = volume.shift(1).rolling(length, min_periods=length).mean()
    return volume / avg.replace(0.0, np.nan)


def _vol_ok(volume: pd.Series, length: int = 10) -> pd.Series:
    avg = volume.shift(1).rolling(length, min_periods=length).mean()
    return (volume > avg).fillna(False)


def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return ((a > b) & (a.shift(1) <= b.shift(1))).fillna(False)


def _fresh(cond: pd.Series) -> pd.Series:
    cond = cond.fillna(False)
    return (cond & ~cond.shift(1).fillna(False)).fillna(False)


def _hist_rise2(hist: pd.Series) -> pd.Series:
    return ((hist > hist.shift(1)) & (hist.shift(1) > hist.shift(2))).fillna(False)


def _bollinger(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    basis = close.rolling(20, min_periods=20).mean()
    sd = close.rolling(20, min_periods=20).std(ddof=0)
    upper = basis + 2.0 * sd
    lower = basis - 2.0 * sd
    width = 100.0 * (upper - lower) / basis.replace(0.0, np.nan)
    return basis, upper, lower, width


def _dmi(df: pd.DataFrame, length: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    h, l, c = df["high"], df["low"], df["close"]
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = _wilder(tr, length)
    pdi = 100.0 * _wilder(plus_dm, length) / atr.replace(0.0, np.nan)
    mdi = 100.0 * _wilder(minus_dm, length) / atr.replace(0.0, np.nan)
    dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0.0, np.nan)
    adx = _wilder(dx, length)
    return pdi, mdi, adx


def _ichimoku(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    h, l = df["high"], df["low"]
    tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2.0
    kijun = (h.rolling(26).max() + l.rolling(26).min()) / 2.0
    a = ((tenkan + kijun) / 2.0).shift(26)
    b = ((h.rolling(52).max() + l.rolling(52).min()) / 2.0).shift(26)
    return tenkan, kijun, a, b


def event_series(df: pd.DataFrame, record: ProductionRecord) -> pd.Series:
    """Return the locked completed-bar entry event for one production record."""
    d = _frame(df)
    c = d["close"]
    v = d["volume"]
    family = record.family
    tf = record.timeframe

    if family == "NE ARARSAN VAR":
        ema5, ema8, ema13 = _ema(c, 5), _ema(c, 8), _ema(c, 13)
        r = _rsi(c)
        _, _, hist = _macd(c)
        cond = (
            (c > ema5) & (ema5 > ema8) & (ema8 > ema13)
            & (r > 30.0) & (r < 60.0)
            & (_rvol(v, 20) > 1.50)
            & _hist_rise2(hist)
        )
        return _fresh(cond)

    if family == "StochRSI / Momentum / Hacim":
        k, sd = _stoch_rsi(c)
        mom = c - c.shift(10)
        vol = _vol_ok(v, 10)
        stoch_cross = _cross_up(k, sd)
        mom_cross = ((mom > 0.0) & (mom.shift(1) <= 0.0)).fillna(False)
        if tf == "1D":
            return _fresh(stoch_cross & (mom > 0.0) & vol)
        if tf == "1W":
            return _fresh(mom_cross & (k > sd) & vol)

    if family == "BB & SMA":
        sma20 = c.rolling(20, min_periods=20).mean()
        sma50 = c.rolling(50, min_periods=50).mean()
        sma200 = c.rolling(200, min_periods=200).mean()
        reclaim = _cross_up(c, sma20)
        trend = (sma20 > sma50) & (sma50 > sma200)
        cond = reclaim & trend & _vol_ok(v, 10)
        if tf in {"2H", "1D"}:
            cond &= sma20 > sma20.shift(1)
        return cond.fillna(False)

    if family == "RSI & MACD - RVOL":
        r = _rsi(c)
        macd, sig, hist = _macd(c)
        rv = _rvol(v, 20)
        if tf == "4H":
            return (((r >= 55.0) & (r < 70.0) & (rv > 1.50)) & _cross_up(macd, sig)).fillna(False)
        if tf == "1D":
            full = (r >= 50.0) & (r < 65.0) & (rv > 1.50) & _hist_rise2(hist)
            return _fresh(full)
        if tf == "1W":
            return (((r >= 50.0) & (r < 65.0) & (rv > 1.50)) & _cross_up(macd, sig)).fillna(False)

    if family == "7 - BB Daralması":
        _, _, _, width = _bollinger(c)
        macd, sig, hist = _macd(c)
        vol = _vol_ok(v, 10)
        q20 = width.shift(1).rolling(120, min_periods=60).quantile(0.20)
        if tf == "4H":
            squeeze = (width <= q20).fillna(False)
            return (squeeze & vol & (macd > 0.0) & _cross_up(macd, sig)).fillna(False)
        if tf == "1D":
            full = (width <= 10.0) & vol & (macd > 0.0) & _hist_rise2(hist)
            return _fresh(full)
        if tf == "1W":
            prev_squeeze = (width.shift(1) <= q20.shift(1)).fillna(False)
            release = (prev_squeeze & (width > width.shift(1))).fillna(False)
            full = release & vol & (macd > 0.0) & _hist_rise2(hist)
            return _fresh(full)

    if family == "8 - TavanTarama":
        pdi, mdi, adx = _dmi(d)
        r = _rsi(c)
        k, sd = _stoch_rsi(c)
        rv = _rvol(v, 20)
        trig = (pdi > mdi) & (pdi > adx) & (pdi.shift(1) <= adx.shift(1))
        if tf == "1D":
            return (trig & (r >= 50.0) & (r < 70.0) & _cross_up(k, sd) & (rv > 1.50)).fillna(False)
        if tf == "1W":
            return (trig & (r >= 40.0) & (r < 70.0) & (k > sd) & (rv > 1.50)).fillna(False)

    if family == "9 - BulutKeser":
        tenkan, kijun, span_a, span_b = _ichimoku(d)
        top = pd.concat([span_a, span_b], axis=1).max(axis=1)
        bottom = pd.concat([span_a, span_b], axis=1).min(axis=1)
        _, _, adx = _dmi(d)
        basis, upper, lower, _ = _bollinger(c)
        rv = _rvol(v, 20)
        cross = _cross_up(tenkan, kijun)
        if tf == "4H":
            return (cross & (c >= bottom) & (c <= top) & (adx > 20.0) & (c > lower) & (c < upper) & (rv > 1.20)).fillna(False)
        if tf == "1D":
            return (cross & (c < bottom) & (adx >= 20.0) & (adx <= 35.0) & (c > basis) & (c < upper) & (rv > 1.50)).fillna(False)
        if tf == "1W":
            return (cross & (c > top) & (adx >= 20.0) & (adx <= 35.0) & (rv > 1.50)).fillna(False)

    if family == "11 - Stoc.RSI & RSI & BB & MACD":
        r = _rsi(c)
        k, sd = _stoch_rsi(c)
        basis, _, _, _ = _bollinger(c)
        macd, sig, _ = _macd(c)
        rv = _rvol(v, 20)
        return ((r > 30.0) & _cross_up(c, basis) & (k > sd) & (macd > sig) & (rv > 1.50)).fillna(False)

    if family == "13 - MACD YenidenHareket":
        macd, sig, hist = _macd(c)
        cross = _cross_up(macd, sig)
        if tf in {"4H", "1D"}:
            cond = (macd > 0.0) & (macd > sig) & _hist_rise2(hist)
            return _fresh(cond)
        if tf == "1W":
            return ((macd > 0.0) & cross).fillna(False)

    if family == "14 - MACD DipDönüşü":
        macd, sig, _ = _macd(c)
        base = ((macd < 0.0) & _cross_up(macd, sig)).fillna(False)
        if tf == "4H":
            return (base & (_rvol(v, 20) > 1.50)).fillna(False)
        if tf in {"1D", "1W"}:
            return base

    raise KeyError(f"No historical evaluator for {family}/{tf}")
