"""Stop/target planning shared by scanners and historical trade simulation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from strategy_exit_profiles import ExitProfile, get_exit_profile


def _columns(frame: pd.DataFrame) -> dict[str, str]:
    lookup = {str(column).lower(): str(column) for column in frame.columns}
    required = {}
    for name in ("open", "high", "low", "close", "volume"):
        if name not in lookup:
            raise ValueError(f"Eksik OHLCV sütunu: {name}")
        required[name] = lookup[name]
    return required


def atr(frame: pd.DataFrame, length: int = 14) -> pd.Series:
    cols = _columns(frame)
    high = pd.to_numeric(frame[cols["high"]], errors="coerce")
    low = pd.to_numeric(frame[cols["low"]], errors="coerce")
    close = pd.to_numeric(frame[cols["close"]], errors="coerce")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def _finite(value: Any, fallback: float) -> float:
    try:
        number = float(value)
        return number if np.isfinite(number) else fallback
    except (TypeError, ValueError):
        return fallback


def _structural_stop(
    frame: pd.DataFrame,
    entry: float,
    atr_now: float,
    profile: ExitProfile,
) -> tuple[float, str]:
    cols = _columns(frame)
    close = pd.to_numeric(frame[cols["close"]], errors="coerce")
    low = pd.to_numeric(frame[cols["low"]], errors="coerce")

    lookback = max(2, min(profile.swing_lookback, len(frame)))
    swing_low = _finite(low.tail(lookback).min(), entry - atr_now * profile.atr_mult)
    swing_stop = swing_low - atr_now * profile.atr_buffer

    signal_low = _finite(low.iloc[-1], entry - atr_now * profile.atr_mult)
    signal_stop = signal_low - atr_now * profile.atr_buffer

    ema = close.ewm(span=max(2, profile.ema_period), adjust=False).mean()
    ema_now = _finite(ema.iloc[-1], entry - atr_now * profile.atr_mult)
    ema_stop = ema_now - atr_now * profile.atr_buffer

    fallback = entry - atr_now * profile.atr_mult
    candidates = [value for value in (swing_stop, signal_stop, ema_stop) if value < entry]

    if profile.stop_mode == "swing":
        raw, reason = swing_stop, f"swing{lookback}-ATR"
    elif profile.stop_mode == "signal_low":
        raw, reason = signal_stop, "signal-low-ATR"
    elif profile.stop_mode == "hybrid_wide":
        raw = min(candidates) if candidates else fallback
        reason = f"wide(swing{lookback}/EMA{profile.ema_period})"
    elif profile.stop_mode == "hybrid_tight":
        # Higher valid stop = tighter structural invalidation.
        raw = max(candidates) if candidates else fallback
        reason = f"tight(swing{lookback}/EMA{profile.ema_period})"
    else:
        raw, reason = fallback, f"ATRx{profile.atr_mult:.2f}"

    risk = max(entry - raw, 0.0)
    min_risk = atr_now * profile.min_risk_atr
    max_risk = atr_now * profile.max_risk_atr
    if risk < min_risk:
        raw = entry - min_risk
        reason += "+minATR"
    elif risk > max_risk:
        raw = entry - max_risk
        reason += "+maxATR"
    return raw, reason


def build_exit_plan(
    frame: pd.DataFrame,
    strategy: str,
    *,
    setup: str | None = None,
    entry_price: float | None = None,
    profile: ExitProfile | None = None,
) -> dict[str, Any]:
    """Create a deterministic plan using only data available at the signal bar."""
    if frame is None or frame.empty:
        raise ValueError("Çıkış planı için fiyat geçmişi boş")
    cols = _columns(frame)
    close = pd.to_numeric(frame[cols["close"]], errors="coerce")
    entry = _finite(entry_price, _finite(close.iloc[-1], 0.0))
    if entry <= 0:
        raise ValueError("Geçerli giriş fiyatı bulunamadı")

    selected = profile or get_exit_profile(strategy, setup=setup)
    atr_series = atr(frame)
    atr_now = _finite(atr_series.iloc[-1], 0.0)
    if atr_now <= 0:
        # Early/listing fallback; still deterministic and price scaled.
        atr_now = max(entry * 0.02, 0.0001)

    stop, stop_reason = _structural_stop(frame, entry, atr_now, selected)
    risk = entry - stop
    if risk <= 0:
        risk = atr_now * max(selected.min_risk_atr, 0.5)
        stop = entry - risk
        stop_reason = "ATR-fallback"

    tp1 = entry + risk * selected.tp1_r
    tp2 = entry + risk * selected.tp2_r
    tp3 = entry + risk * selected.tp3_r
    a1, a2, a3 = selected.tp_allocations
    runner = max(0.0, 1.0 - a1 - a2 - a3)

    def pct(level: float) -> float:
        return (level / entry - 1.0) * 100.0

    return {
        "strategy": strategy,
        "setup": setup or "",
        "profile_key": selected.key,
        "family": selected.family,
        "entry": float(entry),
        "atr": float(atr_now),
        "stop": float(stop),
        "stop_reason": stop_reason,
        "risk_per_share": float(risk),
        "risk_atr": float(risk / atr_now),
        "stop_pct": float(pct(stop)),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3),
        "tp1_pct": float(pct(tp1)),
        "tp2_pct": float(pct(tp2)),
        "tp3_pct": float(pct(tp3)),
        "tp1_r": selected.tp1_r,
        "tp2_r": selected.tp2_r,
        "tp3_r": selected.tp3_r,
        "tp_allocations": [a1, a2, a3],
        "runner_allocation": runner,
        "breakeven_after_tp1": selected.breakeven_after_tp1,
        "trailing_after_tp2": selected.trailing_after_tp2,
        "trailing_atr_mult": selected.trailing_atr_mult,
        "max_hold_bars": selected.max_hold_bars,
        "profile": asdict(selected),
    }


def attach_strategy_plans(frame: pd.DataFrame, signals: dict[str, dict]) -> dict[str, dict]:
    """Build plans only for strategies whose raw signal exists on the last bar."""
    plans: dict[str, dict] = {}
    for strategy, result in signals.items():
        if not isinstance(result, dict):
            continue
        if strategy == "smi_macd":
            if result.get("full_buy_signal"):
                plans["smi_macd_full"] = build_exit_plan(frame, "smi_macd_full")
            if result.get("smi_macd_buy"):
                plans["smi_macd"] = build_exit_plan(frame, "smi_macd")
        elif strategy == "i9":
            if result.get("full_signal"):
                plans["i9"] = build_exit_plan(frame, "i9")
            elif result.get("h8_signal"):
                plans["h8"] = build_exit_plan(frame, "h8")
        elif result.get("signal"):
            plans[strategy] = build_exit_plan(frame, strategy)
    return plans
