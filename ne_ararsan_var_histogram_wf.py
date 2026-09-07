"""NE ARARSAN VAR histogram-entry + histogram-exit walk-forward research.

Frozen common entry skeleton:
- close > EMA5 > EMA8 > EMA13
- 30 < RSI14 < 60
- RVOL20 > 1.50
- no StochRSI

MACD histogram entry families (fresh event only):
- hist_rise_any: H[t] > H[t-1] > H[t-2]
- hist_rise_neg: same, H[t] < 0
- hist_rise_pos: same, H[t] > 0
Each is tested with CCI off and with -100 < CCI20 < 100.

Exit families keep one timeframe-specific structural risk skeleton and compare
control vs histogram-aware SAT logic. Four expanding walk-forward folds are
used. 10 bps commission + 10 bps slippage per side are applied.

The old crossover holdout has already been observed; therefore this is
walk-forward research, not a newly untouched holdout.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from market_data_store import MarketDataStore
from ne_ararsan_var_research import WARMUP, build_indicator_frame

COMMISSION_BPS = 10.0
SLIPPAGE_BPS = 10.0
BOUNDARIES = (0.45, 0.60, 0.75, 0.90, 1.00)
PERIODS = ("15m", "30m", "45m", "1H", "2H", "4H", "1D", "1W", "1M")


def entry_variants() -> list[dict[str, Any]]:
    rows = []
    for sign in ("any", "neg", "pos"):
        for cci in (False, True):
            rows.append({
                "name": f"hist_rise_{sign}_{'cci' if cci else 'nocci'}",
                "sign": sign,
                "cci": cci,
            })
    return rows


def structural(period: str) -> dict[str, Any]:
    table = {
        "15m": dict(stop_mode="atr", stop_atr=0.90, swing_lookback=5, atr_buffer=0.10, trail_atr=1.50, tight_trail_atr=1.00, max_hold=24),
        "30m": dict(stop_mode="atr", stop_atr=1.00, swing_lookback=5, atr_buffer=0.15, trail_atr=1.60, tight_trail_atr=1.15, max_hold=24),
        "45m": dict(stop_mode="atr", stop_atr=1.10, swing_lookback=5, atr_buffer=0.15, trail_atr=1.70, tight_trail_atr=1.25, max_hold=24),
        "1H": dict(stop_mode="atr", stop_atr=1.15, swing_lookback=7, atr_buffer=0.20, trail_atr=1.80, tight_trail_atr=1.35, max_hold=32),
        "2H": dict(stop_mode="atr", stop_atr=1.20, swing_lookback=7, atr_buffer=0.20, trail_atr=2.30, tight_trail_atr=1.65, max_hold=40),
        "4H": dict(stop_mode="swing", stop_atr=1.20, swing_lookback=9, atr_buffer=0.15, trail_atr=1.70, tight_trail_atr=1.30, max_hold=40),
        "1D": dict(stop_mode="swing", stop_atr=1.25, swing_lookback=7, atr_buffer=0.20, trail_atr=2.30, tight_trail_atr=1.75, max_hold=40),
        "1W": dict(stop_mode="swing", stop_atr=1.30, swing_lookback=9, atr_buffer=0.15, trail_atr=2.30, tight_trail_atr=1.80, max_hold=80),
        "1M": dict(stop_mode="swing", stop_atr=1.30, swing_lookback=9, atr_buffer=0.15, trail_atr=2.50, tight_trail_atr=2.00, max_hold=36),
    }
    return dict(table[period])


def exit_variants(period: str) -> list[dict[str, Any]]:
    s = structural(period)
    variants = [
        ("control_123", "none", (1.0, 2.0, 3.0)),
        ("control_fast", "none", (0.8, 1.5, 2.4)),
        ("hist_fade2_exit", "hist_fade2", (1.0, 2.0, 3.0)),
        ("hist_neg_falling_exit", "hist_neg_falling", (1.0, 2.0, 3.0)),
        ("ema13_hist_neg_falling", "ema13_hist_neg_falling", (1.0, 2.0, 3.0)),
        ("hist_adaptive", "hist_adaptive", (1.0, 2.0, 3.0)),
    ]
    out = []
    for name, rule, tps in variants:
        p = dict(s)
        p.update({
            "name": name,
            "exit_rule": rule,
            "tp1_r": tps[0],
            "tp2_r": tps[1],
            "tp3_r": tps[2],
            "allocations": (0.30, 0.30, 0.20),
            "runner": 0.20,
        })
        out.append(p)
    return out


def add_histogram(data: pd.DataFrame) -> pd.DataFrame:
    out = data.copy()
    out["hist"] = pd.to_numeric(out["macd"], errors="coerce") - pd.to_numeric(out["macd_signal"], errors="coerce")
    return out


def build_entry_events(data: pd.DataFrame, variant: dict[str, Any]) -> pd.Series:
    close = pd.to_numeric(data["close"], errors="coerce")
    hist = pd.to_numeric(data["hist"], errors="coerce")
    rising2 = (hist > hist.shift(1)) & (hist.shift(1) > hist.shift(2))
    common = (
        (close > data["ema5"])
        & (data["ema5"] > data["ema8"])
        & (data["ema8"] > data["ema13"])
        & (data["rsi14"] > 30.0)
        & (data["rsi14"] < 60.0)
        & (data["rvol20"] > 1.50)
        & rising2
    )
    if variant["sign"] == "neg":
        common &= hist < 0.0
    elif variant["sign"] == "pos":
        common &= hist > 0.0
    if variant["cci"]:
        common &= (data["cci20"] > -100.0) & (data["cci20"] < 100.0)
    common = common.fillna(False)
    # Only the first bar entering the condition is a new scanner event.
    return (common & ~common.shift(1).fillna(False)).fillna(False)


def _initial_stop(data: pd.DataFrame, signal_pos: int, entry: float, p: dict[str, Any]) -> tuple[float, float]:
    atr = float(data["atr14"].iloc[signal_pos])
    if not np.isfinite(atr) or atr <= 0:
        atr = max(entry * 0.02, 0.0001)
    if p["stop_mode"] == "atr":
        risk = atr * float(p["stop_atr"])
        return entry - risk, risk
    start = max(0, signal_pos - int(p["swing_lookback"]) + 1)
    swing_low = float(pd.to_numeric(data["low"].iloc[start:signal_pos + 1], errors="coerce").min())
    stop = swing_low - atr * float(p["atr_buffer"])
    risk = entry - stop
    if not np.isfinite(risk) or risk <= 0:
        risk = atr * float(p["stop_atr"])
    risk = min(max(risk, 0.60 * atr), 2.60 * atr)
    return entry - risk, risk


def _hist_flags(data: pd.DataFrame, pos: int) -> tuple[bool, bool]:
    if pos < 2:
        return False, False
    h0 = float(data["hist"].iloc[pos])
    h1 = float(data["hist"].iloc[pos - 1])
    h2 = float(data["hist"].iloc[pos - 2])
    if not all(np.isfinite(v) for v in (h0, h1, h2)):
        return False, False
    fade2 = h0 < h1 < h2
    neg_falling = h0 < 0.0 and h0 < h1
    return fade2, neg_falling


def simulate(data: pd.DataFrame, signal_pos: int, p: dict[str, Any]) -> dict[str, Any] | None:
    entry_pos = signal_pos + 1
    if entry_pos >= len(data):
        return None
    entry = float(data["open"].iloc[entry_pos])
    if not np.isfinite(entry) or entry <= 0:
        return None
    stop, risk = _initial_stop(data, signal_pos, entry, p)
    if risk <= 0:
        return None

    tp1 = entry + risk * float(p["tp1_r"])
    tp2 = entry + risk * float(p["tp2_r"])
    tp3 = entry + risk * float(p["tp3_r"])
    alloc = tuple(float(x) for x in p["allocations"])
    remaining = 1.0
    cash = 0.0
    tp1_hit = tp2_hit = tp3_hit = False
    trail_active = False
    adaptive_tight = False
    highest = entry
    max_high = entry
    min_low = entry
    exit_reason = "TIME"
    exit_pos = entry_pos
    last_pos = min(len(data) - 1, entry_pos + int(p["max_hold"]) - 1)

    for pos in range(entry_pos, last_pos + 1):
        row = data.iloc[pos]
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        highest = max(highest, high)
        max_high = max(max_high, high)
        min_low = min(min_low, low)

        # Conservative OHLC ordering: an active stop wins over same-bar targets.
        if low <= stop:
            cash += remaining * stop
            remaining = 0.0
            exit_reason = "TRAIL" if trail_active or adaptive_tight else "STOP"
            exit_pos = pos
            break

        if not tp1_hit and high >= tp1:
            q = min(remaining, alloc[0])
            cash += q * tp1
            remaining -= q
            tp1_hit = True
        if remaining > 1e-12 and not tp2_hit and high >= tp2:
            q = min(remaining, alloc[1])
            cash += q * tp2
            remaining -= q
            tp2_hit = True
            trail_active = True
        if remaining > 1e-12 and not tp3_hit and high >= tp3:
            q = min(remaining, alloc[2])
            cash += q * tp3
            remaining -= q
            tp3_hit = True

        fade2, neg_falling = _hist_flags(data, pos)
        rule = str(p["exit_rule"])
        full_exit = False
        if rule == "hist_fade2":
            full_exit = fade2
        elif rule == "hist_neg_falling":
            full_exit = neg_falling
        elif rule == "ema13_hist_neg_falling":
            full_exit = neg_falling and close < float(row["ema13"])
        elif rule == "hist_adaptive":
            if fade2:
                adaptive_tight = True
            full_exit = neg_falling and close < float(row["ema13"])

        if full_exit and remaining > 1e-12:
            cash += remaining * close
            remaining = 0.0
            exit_reason = "HIST_TECH"
            exit_pos = pos
            break

        if remaining > 1e-12 and (trail_active or adaptive_tight):
            atr = float(row["atr14"])
            if np.isfinite(atr) and atr > 0:
                mult = float(p["tight_trail_atr"] if adaptive_tight else p["trail_atr"])
                # Tightened stop is active only from the next bar.
                stop = max(stop, highest - mult * atr)

        if pos == last_pos and remaining > 1e-12:
            cash += remaining * close
            remaining = 0.0
            exit_reason = "TIME"
            exit_pos = pos
            break

    pnl = cash - entry
    return {
        "signal_time": pd.Timestamp(data.index[signal_pos]),
        "entry_time": pd.Timestamp(data.index[entry_pos]),
        "exit_time": pd.Timestamp(data.index[exit_pos]),
        "entry": entry,
        "risk": risk,
        "realized_r": pnl / risk,
        "return_pct": pnl / entry * 100.0,
        "mfe_r": (max_high - entry) / risk,
        "mae_r": (min_low - entry) / risk,
        "tp1_hit": tp1_hit,
        "tp2_hit": tp2_hit,
        "tp3_hit": tp3_hit,
        "exit_reason": exit_reason,
        "exit_pos": exit_pos,
    }


def _net_r(t: dict[str, Any], commission_bps: float = COMMISSION_BPS, slippage_bps: float = SLIPPAGE_BPS) -> float:
    entry = float(t["entry"])
    risk = float(t["risk"])
    gross_r = float(t["realized_r"])
    weighted_exit = entry + gross_r * risk
    c = commission_bps / 10000.0
    s = slippage_bps / 10000.0
    buy = entry * (1.0 + s)
    sell = weighted_exit * (1.0 - s)
    net_pnl = sell * (1.0 - c) - buy * (1.0 + c)
    return net_pnl / risk


def metrics(trades: list[dict[str, Any]], commission_bps: float = COMMISSION_BPS, slippage_bps: float = SLIPPAGE_BPS) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "profit_factor": None, "expectancy_r": 0.0, "win_rate_pct": 0.0}
    r = np.array([_net_r(t, commission_bps, slippage_bps) for t in trades], dtype=float)
    gp = float(r[r > 0].sum())
    gl = float(-r[r < 0].sum())
    return {
        "trades": len(trades),
        "win_rate_pct": float((r > 0).mean() * 100.0),
        "expectancy_r": float(r.mean()),
        "median_r": float(np.median(r)),
        "profit_factor": gp / gl if gl > 0 else None,
        "avg_mfe_r": float(np.mean([t["mfe_r"] for t in trades])),
        "avg_mae_r": float(np.mean([t["mae_r"] for t in trades])),
        "tp1_rate_pct": float(np.mean([t["tp1_hit"] for t in trades]) * 100.0),
        "tp2_rate_pct": float(np.mean([t["tp2_hit"] for t in trades]) * 100.0),
        "tp3_rate_pct": float(np.mean([t["tp3_hit"] for t in trades]) * 100.0),
        "exit_reasons": dict(sorted(Counter(t["exit_reason"] for t in trades).items())),
    }


def score(m: dict[str, Any]) -> float:
    if int(m.get("trades", 0)) < 25:
        return -1e9
    exp = float(m.get("expectancy_r", -99.0))
    pf = float(m.get("profit_factor") or 0.01)
    return exp + 0.05 * math.log(max(pf, 0.01))


def collect(frames, entry_name: str, p: dict[str, Any], start=None, end=None):
    trades = []
    for symbol, data, positions_by_entry in frames:
        positions = positions_by_entry.get(entry_name, [])
        last_exit = -1
        for signal_pos in positions:
            st = pd.Timestamp(data.index[signal_pos])
            if start is not None and st < start:
                continue
            if end is not None and st >= end:
                break
            if signal_pos <= last_exit:
                continue
            t = simulate(data, signal_pos, p)
            if t is None:
                continue
            if end is not None and pd.Timestamp(t["exit_time"]) >= end:
                continue
            t["symbol"] = symbol
            trades.append(t)
            last_exit = int(t["exit_pos"])
    trades.sort(key=lambda x: x["entry_time"])
    return trades


def analyze(db: str, period: str) -> dict[str, Any]:
    entries = entry_variants()
    exits = exit_variants(period)
    combos = [(e, x) for e in entries for x in exits]
    frames = []
    all_times = []
    per_entry_signal_count = Counter()

    with MarketDataStore(db, read_only=True) as store:
        symbols = store.list_symbols("BIST", period)
        for i, symbol in enumerate(symbols, 1):
            frame = store.load_dataframe(symbol, "BIST", period, limit=0)
            if frame is None or len(frame) <= WARMUP + 5:
                continue
            data = add_histogram(build_indicator_frame(frame))
            positions_by_entry = {}
            for e in entries:
                events = build_entry_events(data, e)
                positions = [int(x) for x in np.flatnonzero(events.to_numpy(bool)) if x >= WARMUP and x + 1 < len(data)]
                positions_by_entry[e["name"]] = positions
                per_entry_signal_count[e["name"]] += len(positions)
                all_times.extend(pd.Timestamp(data.index[x]) for x in positions)
            if any(positions_by_entry.values()):
                frames.append((symbol, data, positions_by_entry))
            if i % 100 == 0 or i == len(symbols):
                print(f"[{period}] {i}/{len(symbols)} loaded", flush=True)

    all_times.sort()
    if len(all_times) < 80:
        return {
            "version": "ne-ararsan-var-histogram-wf-v1",
            "period": period,
            "classification": "INSUFFICIENT_SAMPLE",
            "all_candidate_events": len(all_times),
            "entry_signal_counts": dict(per_entry_signal_count),
        }

    bounds = [pd.Timestamp(all_times[min(len(all_times) - 1, int(len(all_times) * q))]) for q in BOUNDARIES[:-1]]
    bounds.append(pd.Timestamp(all_times[-1]) + pd.Timedelta(days=3700))

    folds = []
    stitched = []
    selected_entries = []
    selected_exits = []

    for fold_idx in range(4):
        test_start = bounds[fold_idx]
        test_end = bounds[fold_idx + 1]
        train_rows = []
        for e, x in combos:
            tr = collect(frames, e["name"], x, start=None, end=test_start)
            m = metrics(tr)
            train_rows.append({
                "entry": e,
                "exit": x,
                "metrics": m,
                "score": score(m),
            })
        train_rows.sort(key=lambda r: r["score"], reverse=True)
        chosen = train_rows[0]
        te = collect(frames, chosen["entry"]["name"], chosen["exit"], start=test_start, end=test_end)
        tm = metrics(te)
        stitched.extend(te)
        selected_entries.append(chosen["entry"]["name"])
        selected_exits.append(chosen["exit"]["name"])
        folds.append({
            "fold": fold_idx + 1,
            "test_start": test_start.isoformat(),
            "test_end": test_end.isoformat(),
            "selected_entry": chosen["entry"],
            "selected_exit": chosen["exit"],
            "train_metrics": chosen["metrics"],
            "test_metrics": tm,
            "train_top5": train_rows[:5],
        })

    stitched_m = metrics(stitched)
    fold_exp = [float(f["test_metrics"].get("expectancy_r", 0.0)) for f in folds]
    fold_pf = [float(f["test_metrics"].get("profit_factor") or 0.0) for f in folds]
    positive_folds = sum(v > 0 for v in fold_exp)
    pf1_folds = sum(v > 1 for v in fold_pf)

    cls = "REJECT"
    if stitched_m["trades"] >= 80 and stitched_m["expectancy_r"] > 0 and (stitched_m["profit_factor"] or 0) > 1.10 and positive_folds >= 3:
        cls = "PROMISING"
    if stitched_m["trades"] >= 80 and stitched_m["expectancy_r"] > 0.08 and (stitched_m["profit_factor"] or 0) > 1.20 and positive_folds == 4:
        cls = "STRONG_WALK_FORWARD"
    if stitched_m["trades"] < 30:
        cls = "INSUFFICIENT_SAMPLE"

    return {
        "version": "ne-ararsan-var-histogram-wf-v1",
        "period": period,
        "method": "4 expanding walk-forward folds; next-bar-open entry; 10bps commission + 10bps slippage per side",
        "common_entry": "close > EMA5 > EMA8 > EMA13; 30<RSI14<60; RVOL20>1.50; no StochRSI",
        "entry_candidate_count": len(entries),
        "exit_candidate_count": len(exits),
        "combo_count": len(combos),
        "entry_signal_counts": dict(per_entry_signal_count),
        "folds": folds,
        "stitched_walk_forward_oos": stitched_m,
        "stability": {
            "positive_expectancy_folds": positive_folds,
            "pf_above_1_folds": pf1_folds,
            "median_fold_expectancy_r": float(np.median(fold_exp)),
            "selected_entries": selected_entries,
            "selected_exits": selected_exits,
        },
        "current_forward_candidate": {
            "entry": folds[-1]["selected_entry"],
            "exit": folds[-1]["selected_exit"],
        },
        "classification": cls,
        "note": "Research only. Previous crossover holdout was already observed; future live data is required for a fresh final validation.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--period", choices=PERIODS, required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    payload = analyze(args.db, args.period)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
