"""Pine-compatible signal layer for Karar Paneli v6.4.5.

The signal state is intentionally based on the exact no-lookahead v6.4.4 Pine
replay already validated on long history.  v6.4.5 promotes only the Pullback
entry guard that survived chronological validation:

* Pullback RVOL must be <= 2.0.
* Pullback close must be at least 95% of the rolling 20-bar high.

Trend Devami stays on the v6.4.4 entry definition because the tested orderly-
trend filters did not beat the baseline. Breakout also stays unchanged because
the 1.75 ATR candidate did not have enough later validation observations.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from historical_decision_panel_v644 import _normalize, decision_series


VERSION = "v6.4.5"
PULLBACK_MAX_RVOL = 2.0
PULLBACK_MIN_PCT20 = 95.0


def _fresh(condition: pd.Series) -> pd.Series:
    condition = condition.fillna(False).astype(bool)
    return condition & (~condition.shift(1).fillna(False))


def decision_v645_series(frame: pd.DataFrame, *, min_score: int = 75) -> pd.DataFrame:
    """Return causal Pine-compatible setup/entry state for every bar."""
    clean = _normalize(frame)
    base = decision_series(clean, min_score=min_score)

    close = clean["close"]
    high = clean["high"]
    volume = clean["volume"]
    avg_volume = volume.rolling(10).mean().shift(1)
    rvol = volume / avg_volume.replace(0, np.nan)
    high20 = high.rolling(20).max()
    pct20 = close / high20.replace(0, np.nan) * 100.0

    breakout = base["breakout"].fillna(False).astype(bool)
    trend = base["trend"].fillna(False).astype(bool)
    pullback_base = base["pullback"].fillna(False).astype(bool)
    pullback_guard = (rvol <= PULLBACK_MAX_RVOL) & (pct20 >= PULLBACK_MIN_PCT20)
    pullback = pullback_base & pullback_guard.fillna(False)

    new_breakout = _fresh(breakout)
    new_pullback = _fresh(pullback)
    new_trend = _fresh(trend)

    active_setup = pd.Series("", index=clean.index, dtype=object)
    active_setup.loc[trend] = "TREND DEVAMI"
    active_setup.loc[pullback] = "PULLBACK"
    active_setup.loc[breakout] = "BREAKOUT"

    new_setup = pd.Series("", index=clean.index, dtype=object)
    new_setup.loc[new_trend] = "TREND DEVAMI"
    new_setup.loc[new_pullback] = "PULLBACK"
    new_setup.loc[new_breakout] = "BREAKOUT"

    out = pd.DataFrame(index=clean.index)
    out["score"] = base["score"].astype(int)
    out["rvol"] = rvol
    out["pct20"] = pct20
    out["adx"] = base["adx"]
    out["pullback_base"] = pullback_base
    out["pullback_guard"] = pullback_guard.fillna(False)
    out["breakout"] = breakout
    out["pullback"] = pullback
    out["trend"] = trend
    out["new_breakout"] = new_breakout
    out["new_pullback"] = new_pullback
    out["new_trend"] = new_trend
    out["active_setup"] = active_setup
    out["new_setup"] = new_setup
    out["entry"] = new_setup.ne("")
    return out


def latest_decision(frame: pd.DataFrame, *, min_score: int = 75) -> dict[str, Any]:
    state = decision_v645_series(frame, min_score=min_score)
    if state.empty:
        return {
            "version": VERSION,
            "score": 0,
            "active_setup": "",
            "new_setup": "",
            "entry": False,
            "rvol": 0.0,
            "pct20": 0.0,
            "pullback_guard": False,
        }
    row = state.iloc[-1]

    def number(name: str, default: float = 0.0) -> float:
        try:
            value = float(row[name])
            return value if np.isfinite(value) else default
        except Exception:
            return default

    return {
        "version": VERSION,
        "score": int(row["score"]),
        "active_setup": str(row["active_setup"] or ""),
        "new_setup": str(row["new_setup"] or ""),
        "entry": bool(row["entry"]),
        "rvol": number("rvol"),
        "pct20": number("pct20"),
        "adx": number("adx"),
        "pullback_guard": bool(row["pullback_guard"]),
        "pullback_base": bool(row["pullback_base"]),
    }
