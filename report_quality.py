"""Quality guards for institutional stock reports.

Prevents stale/broken swing levels from being presented as current support or
resistance, removes provider sentinel multiples, and provides a safe statement
row selector for duplicate financial line items.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


MULTIPLE_LIMITS = {
    "pe": (0.0, 500.0),
    "pb": (0.0, 100.0),
    "ev_ebitda": (0.0, 250.0),
    "ev_sales": (0.0, 100.0),
    "dividend_yield": (0.0, 50.0),
}


def sanitize_peer_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Replace provider sentinels / economically unusable multiples with NaN."""
    out = frame.copy()
    for col, (lo, hi) in MULTIPLE_LIMITS.items():
        if col not in out.columns:
            continue
        s = pd.to_numeric(out[col], errors="coerce")
        out[col] = s.where((s > lo) & (s < hi), np.nan)
    return out


def _pivot_prices(item: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for p in item.get("pivots", []) or []:
        try:
            x = float(p.get("price"))
        except (TypeError, ValueError):
            continue
        if np.isfinite(x):
            values.append(x)
    for key in ("last_swing_high", "last_swing_low"):
        try:
            x = float(item.get(key))
        except (TypeError, ValueError):
            continue
        if np.isfinite(x):
            values.append(x)
    return sorted(set(values))


def sanitize_timeframe(item: dict[str, Any]) -> dict[str, Any]:
    """Classify levels relative to current price instead of trusting pivot role."""
    out = dict(item)
    try:
        price = float(out.get("close"))
    except (TypeError, ValueError):
        return out
    if not np.isfinite(price) or price <= 0:
        return out

    levels = _pivot_prices(out)
    below = [x for x in levels if x < price]
    above = [x for x in levels if x > price]
    support = max(below) if below else None
    resistance = min(above) if above else None

    # Previous pivot levels on the wrong side of current price are broken/history,
    # never current support/resistance.
    historical = [x for x in levels if x not in {support, resistance}]
    out["current_support"] = support
    out["current_resistance"] = resistance
    out["historical_levels"] = historical
    out["last_swing_low_raw"] = out.get("last_swing_low")
    out["last_swing_high_raw"] = out.get("last_swing_high")
    out["last_swing_low"] = support
    out["last_swing_high"] = resistance
    out["distance_to_support_pct"] = None if support is None else round((price / support - 1.0) * 100.0, 2)
    out["distance_to_resistance_pct"] = None if resistance is None else round((resistance / price - 1.0) * 100.0, 2)
    out["level_note"] = (
        "Güncel fiyatın altında doğrulanmış yakın pivot yok; eski dipler kırılmış/tarihsel seviyedir."
        if support is None
        else "Destek yalnız güncel fiyatın altındaki doğrulanmış pivotlardan seçildi."
    )
    return out


def sanitize_mtf_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["timeframes"] = {
        period: sanitize_timeframe(item) if isinstance(item, dict) else item
        for period, item in (payload.get("timeframes") or {}).items()
    }
    out["level_policy"] = "support_below_price_resistance_above_price; broken_levels_are_context_only"
    return out


def safe_statement_row(frame: pd.DataFrame, aliases: tuple[str, ...], normalizer) -> pd.Series | None:
    """Select a single numeric row even when statement labels are duplicated."""
    if frame is None or frame.empty:
        return None
    labels = [(normalizer(idx), idx) for idx in frame.index]
    chosen = None
    for alias in aliases:
        a = normalizer(alias)
        exact = [idx for label, idx in labels if label == a]
        if exact:
            chosen = exact[0]
            break
    if chosen is None:
        candidates: list[tuple[int, Any]] = []
        for alias in aliases:
            a = normalizer(alias)
            for label, idx in labels:
                if a and a in label:
                    candidates.append((len(label), idx))
        if not candidates:
            return None
        chosen = sorted(candidates, key=lambda x: x[0])[0][1]

    selected = frame.loc[chosen]
    if isinstance(selected, pd.DataFrame):
        # Duplicate financial labels are common. Prefer the row with the most
        # populated periods; tie-break deterministically by first appearance.
        numeric = selected.apply(pd.to_numeric, errors="coerce", axis=1)
        counts = numeric.notna().sum(axis=1).to_numpy()
        selected = numeric.iloc[int(np.argmax(counts))]
    return pd.to_numeric(selected, errors="coerce")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mtf-json")
    p.add_argument("--peer-csv")
    a = p.parse_args()
    if a.mtf_json:
        path = Path(a.mtf_json)
        payload = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(sanitize_mtf_payload(payload), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    if a.peer_csv:
        path = Path(a.peer_csv)
        sanitize_peer_frame(pd.read_csv(path)).to_csv(path, index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
