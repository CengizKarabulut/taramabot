"""Strategy-specific exit profiles.

Profiles are promoted only after chronological walk-forward/OOS validation and
explicit cost sensitivity. Keeping them separate prevents one ATR/TP recipe
from being forced onto every signal family.
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
    # OOS + moderate-cost promoted. S-M-2: OOS PF 2.18; net PF 1.97.
    "smi_macd": ExitProfile(
        key="smi_macd", family="early_reversal", stop_mode="swing",
        atr_mult=1.10, atr_buffer=0.25, ema_period=21, swing_lookback=6,
        min_risk_atr=0.75, max_risk_atr=2.25,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.35, 0.30, 0.20), trailing_atr_mult=1.8,
    ),
    # OOS + moderate-cost promoted. S-M-V-2: OOS PF 1.56; net PF 1.41.
    "smi_macd_full": ExitProfile(
        key="smi_macd_full", family="trend_reversal", stop_mode="swing",
        atr_mult=1.25, atr_buffer=0.25, ema_period=21, swing_lookback=7,
        min_risk_atr=0.80, max_risk_atr=2.50,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=2.2,
    ),

    # OOS + moderate-cost promoted. R-V-1: OOS PF 1.37; net PF 1.21.
    "rsi": ExitProfile(
        key="rsi", family="fast_momentum", stop_mode="hybrid_wide",
        atr_mult=0.95, atr_buffer=0.15, ema_period=21, swing_lookback=4,
        min_risk_atr=0.65, max_risk_atr=1.90,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.35, 0.30, 0.20), trailing_atr_mult=1.5,
        max_hold_bars=55,
    ),
    # OOS + moderate-cost promoted. R-M-V-1: OOS PF 1.44; net PF 1.29.
    "rsi_macd": ExitProfile(
        key="rsi_macd", family="confirmed_momentum", stop_mode="swing",
        atr_mult=1.10, atr_buffer=0.20, ema_period=13, swing_lookback=6,
        min_risk_atr=0.70, max_risk_atr=2.20,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=1.9,
    ),

    # E-V-1 optimized candidate stayed net-positive but moderate-cost PF=1.14,
    # below the strict 1.20 promotion gate. Keep the pre-research production profile.
    "ema": ExitProfile(
        key="ema", family="trend", stop_mode="hybrid_tight",
        atr_mult=1.50, atr_buffer=0.20, ema_period=21, swing_lookback=8,
        min_risk_atr=0.80, max_risk_atr=2.50,
        tp1_r=1.0, tp2_r=2.0, tp3_r=3.2,
        tp_allocations=(0.25, 0.30, 0.20), trailing_atr_mult=2.1,
    ),
    # OOS + moderate-cost promoted. A-M-V-1: OOS PF 1.32; net PF 1.21.
    "new_scan": ExitProfile(
        key="new_scan", family="trend", stop_mode="swing",
        atr_mult=1.25, atr_buffer=0.20, ema_period=21, swing_lookback=8,
        min_risk_atr=0.80, max_risk_atr=2.60,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=2.2,
    ),
    # M-1 optimized candidate stayed net-positive but moderate-cost PF=1.18,
    # below the strict 1.20 promotion gate. Keep the pre-research production profile.
    "macd_cross": ExitProfile(
        key="macd_cross", family="trend_momentum", stop_mode="hybrid_tight",
        atr_mult=1.40, atr_buffer=0.20, ema_period=21, swing_lookback=7,
        min_risk_atr=0.75, max_risk_atr=2.35,
        tp1_r=1.0, tp2_r=1.8, tp3_r=2.8,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=1.9,
    ),
    # OOS + moderate-cost promoted. S-M-1: OOS PF 1.35; net PF 1.22.
    "h8": ExitProfile(
        key="h8", family="momentum_continuation", stop_mode="swing",
        atr_mult=1.10, atr_buffer=0.20, ema_period=13, swing_lookback=6,
        min_risk_atr=0.70, max_risk_atr=2.20,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=1.7,
    ),
    # OOS + moderate-cost promoted. S-M-V-1: OOS PF 1.34; net PF 1.22.
    "i9": ExitProfile(
        key="i9", family="confirmed_momentum", stop_mode="hybrid_wide",
        atr_mult=1.20, atr_buffer=0.20, ema_period=21, swing_lookback=7,
        min_risk_atr=0.75, max_risk_atr=2.40,
        tp1_r=0.8, tp2_r=1.5, tp3_r=2.4,
        tp_allocations=(0.30, 0.30, 0.20), trailing_atr_mult=2.2,
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
        # Cost study: final untouched OOS had no completed trades; retain profile
        # but do not label this setup cost-validated.
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
        # OOS + cost validated: 47 OOS trades, gross PF 2.60; moderate-cost PF 2.29.
        # Selected entry guard: RVOL<=2.0 and close>=95% of rolling 20-bar high.
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
        # Moderate-cost OOS PF fell below 1.0 with negative expectancy. Preserve
        # historical profile for compatibility, but callers/UI should mark the
        # setup as research/caution rather than cost-validated.
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
