"""Strategy-specific exit profiles.

Profiles start conservatively and are promoted only after walk-forward/OOS
validation. Keeping them separate prevents one ATR/TP recipe from being forced
onto every signal family.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Tuple


@dataclass(frozen=True)
class ExitProfile:
    key: str
    family: str
    stop_mode: str
    atr_mult: float = 1.5
    atr_buffer: float = 0.20
    ema_period: int = 21
    swing_lookback: int = 7
    min_risk_atr: float = 0.70
    max_risk_atr: float = 2.75
    tp1_r: float = 1.0
    tp2_r: float = 2.0
    tp3_r: float = 3.0
    tp_allocations: Tuple[float, float, float] = (0.30, 0.30, 0.20)
    breakeven_after_tp1: bool = True
    trailing_after_tp2: bool = True
    trailing_atr_mult: float = 2.0
    max_hold_bars: int = 80


BASE_PROFILES: Dict[str, ExitProfile] = {
    # OOS-promoted 2026-09-04. S-M-2: 54 OOS trades, 66.67% win, PF 2.18.
    "smi_macd": ExitProfile(
        key="smi_macd", family="early_reversal", stop_mode="swing",
        atr_mult=1.10, atr_buffer=0.25, swing_lookback=6,
        min_risk_atr=0.75, max_risk_atr=2.25,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.35, 0.30, 0.20), trailing_atr_mult=1.8,
    ),
    # OOS-promoted 2026-09-04. S-M-V-2: 208 OOS trades, 63.46% win, PF 1.56.
    "smi_macd_full": ExitProfile(
        key="smi_macd_full", family="trend_reversal", stop_mode="swing",
        atr_mult=1.25, atr_buffer=0.25, ema_period=21, swing_lookback=7,
        min_risk_atr=0.80, max_risk_atr=2.50,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=2.2,
    ),

    # Fast momentum: tighter invalidation and earlier partial realization.
    "rsi": ExitProfile(
        key="rsi", family="fast_momentum", stop_mode="signal_low",
        atr_mult=1.20, atr_buffer=0.15, swing_lookback=4,
        min_risk_atr=0.65, max_risk_atr=1.90,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.35, 0.30, 0.20), trailing_atr_mult=1.7,
        max_hold_bars=55,
    ),
    # OOS-promoted 2026-09-04. R-M-V-1: 1,155 OOS trades,
    # 61.99% win, PF 1.44, +0.165R expectancy.
    "rsi_macd": ExitProfile(
        key="rsi_macd", family="confirmed_momentum", stop_mode="swing",
        atr_mult=1.10, atr_buffer=0.20, ema_period=13, swing_lookback=6,
        min_risk_atr=0.70, max_risk_atr=2.20,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=1.9,
    ),

    # Trend continuation family.
    "ema": ExitProfile(
        key="ema", family="trend", stop_mode="hybrid_tight",
        atr_mult=1.50, atr_buffer=0.20, ema_period=21, swing_lookback=8,
        min_risk_atr=0.80, max_risk_atr=2.50,
        tp1_r=1.0, tp2_r=2.0, tp3_r=3.2,
        tp_allocations=(0.25, 0.30, 0.20), trailing_atr_mult=2.1,
    ),
    "new_scan": ExitProfile(
        key="new_scan", family="trend", stop_mode="hybrid_tight",
        atr_mult=1.50, atr_buffer=0.20, ema_period=21, swing_lookback=8,
        min_risk_atr=0.80, max_risk_atr=2.60,
        tp1_r=1.0, tp2_r=2.0, tp3_r=3.0,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=2.0,
    ),
    "macd_cross": ExitProfile(
        key="macd_cross", family="trend_momentum", stop_mode="hybrid_tight",
        atr_mult=1.40, atr_buffer=0.20, ema_period=21, swing_lookback=7,
        min_risk_atr=0.75, max_risk_atr=2.35,
        tp1_r=1.0, tp2_r=1.8, tp3_r=2.8,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=1.9,
    ),
    "h8": ExitProfile(
        key="h8", family="momentum_continuation", stop_mode="hybrid_tight",
        atr_mult=1.35, atr_buffer=0.20, ema_period=13, swing_lookback=6,
        min_risk_atr=0.70, max_risk_atr=2.20,
        tp1_r=1.0, tp2_r=1.8, tp3_r=2.8,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=1.9,
    ),
    "i9": ExitProfile(
        key="i9", family="confirmed_momentum", stop_mode="hybrid_tight",
        atr_mult=1.45, atr_buffer=0.20, ema_period=21, swing_lookback=7,
        min_risk_atr=0.75, max_risk_atr=2.40,
        tp1_r=1.0, tp2_r=2.0, tp3_r=3.0,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=2.0,
    ),

    # Decision panel defaults; setup-specific overrides are applied below.
    "decision_panel": ExitProfile(
        key="decision_panel", family="decision_panel", stop_mode="hybrid_tight",
        atr_mult=1.50, atr_buffer=0.25, ema_period=21, swing_lookback=8,
        min_risk_atr=0.80, max_risk_atr=2.60,
        tp1_r=1.0, tp2_r=2.0, tp3_r=3.0,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=2.0,
    ),
}


ALIASES = {
    "full": "smi_macd_full",
    "smi": "smi_macd",
    "r-v-1": "rsi",
    "r-m-v-1": "rsi_macd",
    "e-v-1": "ema",
    "a-m-v-1": "new_scan",
    "m-1": "macd_cross",
    "s-m-1": "h8",
    "s-m-v-1": "i9",
    "s-m-2": "smi_macd",
    "s-m-v-2": "smi_macd_full",
}


def get_exit_profile(strategy: str, setup: str | None = None) -> ExitProfile:
    key = str(strategy or "").strip().lower()
    key = ALIASES.get(key, key)
    profile = BASE_PROFILES.get(key, BASE_PROFILES["decision_panel"])

    if key != "decision_panel":
        return profile

    setup_key = str(setup or "").strip().upper()
    if setup_key == "BREAKOUT":
        return replace(
            profile,
            family="decision_breakout",
            stop_mode="hybrid_tight",
            ema_period=13,
            swing_lookback=6,
            atr_buffer=0.20,
            min_risk_atr=0.75,
            max_risk_atr=2.20,
            tp1_r=1.0,
            tp2_r=2.0,
            tp3_r=3.2,
        )
    if setup_key == "PULLBACK":
        # OOS-promoted 2026-09-04 after fixed current-vs-proposed comparison.
        # Selected entry guard: RVOL<=2.0 and close>=95% of rolling 20-bar high.
        # Proposed exit OOS: 47 trades, 74.47% win, PF 2.60, +0.384R.
        return replace(
            profile,
            family="decision_pullback",
            stop_mode="hybrid_wide",
            atr_mult=1.25,
            ema_period=21,
            swing_lookback=8,
            atr_buffer=0.25,
            min_risk_atr=0.80,
            max_risk_atr=2.60,
            tp1_r=0.8,
            tp2_r=1.5,
            tp3_r=2.4,
            trailing_atr_mult=1.8,
        )
    if setup_key in {"TREND DEVAMI", "TREND"}:
        # Keep current profile: proposed higher-win profile lost PF/expectancy in final OOS.
        return replace(
            profile,
            family="decision_trend",
            stop_mode="hybrid_tight",
            ema_period=21,
            swing_lookback=7,
            min_risk_atr=0.80,
            max_risk_atr=2.50,
            tp1_r=1.0,
            tp2_r=2.0,
            tp3_r=3.0,
        )
    return profile
