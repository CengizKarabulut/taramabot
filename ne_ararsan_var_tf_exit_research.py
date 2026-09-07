"""Timeframe-specific exit research for NE ARARSAN VAR v1.

Research goal: test different exit *logic families* by timeframe rather than
reusing one generic SAT architecture everywhere.

Important methodology note:
The previous final 20% holdout has already been observed. This module therefore
uses expanding walk-forward folds over the full history and reports the stitched
next-window OOS performance. Candidate exit families are fixed in code before
the run. The latest selected profile is only a forward candidate, not a new
untouched holdout result.
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
TEST_BOUNDARIES = (0.45, 0.60, 0.75, 0.90, 1.00)


def _p(name: str, **kwargs: Any) -> dict[str, Any]:
    base = {
        "name": name,
        "stop_mode": "atr",
        "stop_atr": 1.0,
        "swing_lookback": 5,
        "atr_buffer": 0.15,
        "target_r": None,
        "trail_start_r": None,
        "trail_atr": None,
        "max_hold": 12,
        "exit_rule": "none",
    }
    base.update(kwargs)
    return base


def candidates(period: str) -> list[dict[str, Any]]:
    if period == "15m":
        return [
            _p("15m_fast_ema5_4", stop_atr=0.75, max_hold=4, exit_rule="ema5_break"),
            _p("15m_fast_ema5_8", stop_atr=0.90, max_hold=8, exit_rule="ema5_break"),
            _p("15m_ema8_8", stop_atr=1.00, max_hold=8, exit_rule="ema8_break"),
            _p("15m_macd_6", stop_atr=0.90, max_hold=6, exit_rule="macd_bear"),
            _p("15m_twoof3_8", stop_atr=1.00, max_hold=8, exit_rule="two_of_three"),
            _p("15m_twoof3_12", stop_atr=1.10, max_hold=12, exit_rule="two_of_three"),
            _p("15m_bracket_1R_6", stop_atr=0.85, target_r=1.0, max_hold=6),
            _p("15m_bracket_125R_8", stop_atr=0.90, target_r=1.25, max_hold=8),
            _p("15m_bracket_15R_12", stop_atr=1.00, target_r=1.5, max_hold=12),
            _p("15m_trail_08_15", stop_atr=0.90, trail_start_r=0.8, trail_atr=1.5, max_hold=12, exit_rule="ema8_break"),
            _p("15m_swing3_twoof3", stop_mode="swing", swing_lookback=3, atr_buffer=0.10, max_hold=8, exit_rule="two_of_three"),
            _p("15m_swing5_ema5", stop_mode="swing", swing_lookback=5, atr_buffer=0.10, max_hold=12, exit_rule="ema5_break"),
        ]
    if period == "30m":
        return [
            _p("30m_ema5_8", stop_atr=0.85, max_hold=8, exit_rule="ema5_break"),
            _p("30m_ema8_12", stop_atr=1.00, max_hold=12, exit_rule="ema8_break"),
            _p("30m_ema58_12", stop_atr=1.00, max_hold=12, exit_rule="ema58_bear"),
            _p("30m_macd_10", stop_atr=1.00, max_hold=10, exit_rule="macd_bear"),
            _p("30m_twoof3_12", stop_atr=1.10, max_hold=12, exit_rule="two_of_three"),
            _p("30m_twoof3_16", stop_atr=1.15, max_hold=16, exit_rule="two_of_three"),
            _p("30m_bracket_125R_10", stop_atr=0.95, target_r=1.25, max_hold=10),
            _p("30m_bracket_15R_12", stop_atr=1.00, target_r=1.5, max_hold=12),
            _p("30m_bracket_18R_16", stop_atr=1.10, target_r=1.8, max_hold=16),
            _p("30m_trail_10_17", stop_atr=1.00, trail_start_r=1.0, trail_atr=1.7, max_hold=16, exit_rule="ema8_break"),
            _p("30m_swing3_twoof3", stop_mode="swing", swing_lookback=3, atr_buffer=0.10, max_hold=12, exit_rule="two_of_three"),
            _p("30m_swing5_ema8", stop_mode="swing", swing_lookback=5, atr_buffer=0.15, max_hold=16, exit_rule="ema8_break"),
        ]
    if period == "45m":
        return [
            _p("45m_ema8_12", stop_atr=1.00, max_hold=12, exit_rule="ema8_break"),
            _p("45m_ema58_16", stop_atr=1.10, max_hold=16, exit_rule="ema58_bear"),
            _p("45m_macd_12", stop_atr=1.10, max_hold=12, exit_rule="macd_bear"),
            _p("45m_rsi50_16", stop_atr=1.10, max_hold=16, exit_rule="rsi50_break"),
            _p("45m_twoof3_16", stop_atr=1.15, max_hold=16, exit_rule="two_of_three"),
            _p("45m_twoof3_20", stop_atr=1.20, max_hold=20, exit_rule="two_of_three"),
            _p("45m_bracket_15R_12", stop_atr=1.00, target_r=1.5, max_hold=12),
            _p("45m_bracket_18R_16", stop_atr=1.10, target_r=1.8, max_hold=16),
            _p("45m_bracket_20R_20", stop_atr=1.15, target_r=2.0, max_hold=20),
            _p("45m_trail_10_18", stop_atr=1.10, trail_start_r=1.0, trail_atr=1.8, max_hold=20, exit_rule="ema8_break"),
            _p("45m_swing5_twoof3", stop_mode="swing", swing_lookback=5, atr_buffer=0.15, max_hold=16, exit_rule="two_of_three"),
            _p("45m_swing7_ema8", stop_mode="swing", swing_lookback=7, atr_buffer=0.15, max_hold=20, exit_rule="ema8_break"),
        ]
    if period == "1H":
        return [
            _p("1H_ema8_12", stop_atr=1.00, max_hold=12, exit_rule="ema8_break"),
            _p("1H_ema58_16", stop_atr=1.10, max_hold=16, exit_rule="ema58_bear"),
            _p("1H_macd_16", stop_atr=1.15, max_hold=16, exit_rule="macd_bear"),
            _p("1H_rsi50_20", stop_atr=1.15, max_hold=20, exit_rule="rsi50_break"),
            _p("1H_twoof3_16", stop_atr=1.15, max_hold=16, exit_rule="two_of_three"),
            _p("1H_twoof3_24", stop_atr=1.25, max_hold=24, exit_rule="two_of_three"),
            _p("1H_bracket_15R_16", stop_atr=1.05, target_r=1.5, max_hold=16),
            _p("1H_bracket_20R_20", stop_atr=1.15, target_r=2.0, max_hold=20),
            _p("1H_bracket_24R_24", stop_atr=1.20, target_r=2.4, max_hold=24),
            _p("1H_trail_10_20", stop_atr=1.10, trail_start_r=1.0, trail_atr=2.0, max_hold=24, exit_rule="ema8_break"),
            _p("1H_swing5_twoof3", stop_mode="swing", swing_lookback=5, atr_buffer=0.15, max_hold=20, exit_rule="two_of_three"),
            _p("1H_swing7_ema13macd", stop_mode="swing", swing_lookback=7, atr_buffer=0.20, max_hold=24, exit_rule="ema13_macd"),
        ]
    if period == "2H":
        return [
            _p("2H_ema8_16", stop_atr=1.10, max_hold=16, exit_rule="ema8_break"),
            _p("2H_ema13macd_24", stop_atr=1.20, max_hold=24, exit_rule="ema13_macd"),
            _p("2H_twoof3_24", stop_atr=1.20, max_hold=24, exit_rule="two_of_three"),
            _p("2H_twoof3_32", stop_atr=1.30, max_hold=32, exit_rule="two_of_three"),
            _p("2H_bracket_18R_20", stop_atr=1.10, target_r=1.8, max_hold=20),
            _p("2H_bracket_20R_24", stop_atr=1.20, target_r=2.0, max_hold=24),
            _p("2H_bracket_24R_32", stop_atr=1.25, target_r=2.4, max_hold=32),
            _p("2H_trail_10_20", stop_atr=1.15, trail_start_r=1.0, trail_atr=2.0, max_hold=32, exit_rule="ema13_macd"),
            _p("2H_trail_12_23", stop_atr=1.20, trail_start_r=1.2, trail_atr=2.3, max_hold=40, exit_rule="none"),
            _p("2H_swing5_ema13macd", stop_mode="swing", swing_lookback=5, atr_buffer=0.15, max_hold=24, exit_rule="ema13_macd"),
            _p("2H_swing7_twoof3", stop_mode="swing", swing_lookback=7, atr_buffer=0.20, max_hold=32, exit_rule="two_of_three"),
            _p("2H_swing9_trail", stop_mode="swing", swing_lookback=9, atr_buffer=0.15, trail_start_r=1.0, trail_atr=2.2, max_hold=40),
        ]
    raise ValueError(f"unsupported period: {period}")


def _exit_signal(row: pd.Series, prev: pd.Series, rule: str) -> bool:
    if rule == "none":
        return False
    if rule == "ema5_break":
        return float(row["close"]) < float(row["ema5"])
    if rule == "ema8_break":
        return float(row["close"]) < float(row["ema8"])
    if rule == "ema58_bear":
        return float(row["ema5"]) < float(row["ema8"])
    if rule == "macd_bear":
        return float(row["macd"]) < float(row["macd_signal"])
    if rule == "rsi50_break":
        return float(row["rsi14"]) < 50.0
    if rule == "ema13_macd":
        return float(row["close"]) < float(row["ema13"]) and float(row["macd"]) < float(row["macd_signal"])
    if rule == "two_of_three":
        votes = [
            float(row["close"]) < float(row["ema8"]),
            float(row["macd"]) < float(row["macd_signal"]),
            float(row["rsi14"]) < 50.0,
        ]
        return sum(bool(v) for v in votes) >= 2
    raise ValueError(rule)


def _initial_stop(data: pd.DataFrame, signal_pos: int, entry: float, p: dict[str, Any]) -> tuple[float, float]:
    atr = float(data["atr14"].iloc[signal_pos])
    if not np.isfinite(atr) or atr <= 0:
        atr = max(entry * 0.02, 0.0001)
    if p["stop_mode"] == "atr":
        risk = atr * float(p["stop_atr"])
        return entry - risk, risk
    start = max(0, signal_pos - int(p["swing_lookback"]) + 1)
    low = float(pd.to_numeric(data["low"].iloc[start:signal_pos + 1], errors="coerce").min())
    stop = low - atr * float(p["atr_buffer"])
    risk = entry - stop
    if not np.isfinite(risk) or risk <= 0:
        risk = atr * float(p["stop_atr"])
        stop = entry - risk
    risk = min(max(risk, 0.60 * atr), 2.60 * atr)
    return entry - risk, risk


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
    target = None if p["target_r"] is None else entry + risk * float(p["target_r"])
    max_high = entry
    min_low = entry
    highest = entry
    trail_active = False
    exit_price = entry
    exit_reason = "TIME"
    exit_pos = entry_pos
    last_pos = min(len(data) - 1, entry_pos + int(p["max_hold"]) - 1)

    for pos in range(entry_pos, last_pos + 1):
        row = data.iloc[pos]
        prev = data.iloc[max(entry_pos, pos - 1)]
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        max_high = max(max_high, high)
        min_low = min(min_low, low)
        highest = max(highest, high)

        if low <= stop:
            exit_price, exit_reason, exit_pos = stop, "STOP", pos
            break
        if target is not None and high >= target:
            exit_price, exit_reason, exit_pos = target, "TARGET", pos
            break

        if p["trail_start_r"] is not None and high >= entry + risk * float(p["trail_start_r"]):
            trail_active = True

        if _exit_signal(row, prev, str(p["exit_rule"])):
            exit_price, exit_reason, exit_pos = close, "TECH", pos
            break

        if trail_active and p["trail_atr"] is not None:
            atr = float(row["atr14"])
            if np.isfinite(atr) and atr > 0:
                # New stop is active next bar, avoiding same-bar lookahead.
                stop = max(stop, highest - atr * float(p["trail_atr"]))

        if pos == last_pos:
            exit_price, exit_reason, exit_pos = close, "TIME", pos

    gross_r = (exit_price - entry) / risk
    return {
        "entry": entry,
        "risk": risk,
        "gross_r": gross_r,
        "mfe_r": (max_high - entry) / risk,
        "mae_r": (min_low - entry) / risk,
        "signal_time": pd.Timestamp(data.index[signal_pos]),
        "entry_time": pd.Timestamp(data.index[entry_pos]),
        "exit_time": pd.Timestamp(data.index[exit_pos]),
        "exit_pos": exit_pos,
        "exit_reason": exit_reason,
    }


def _net_r(t: dict[str, Any]) -> float:
    entry = float(t["entry"])
    risk = float(t["risk"])
    weighted_exit = entry + float(t["gross_r"]) * risk
    c = COMMISSION_BPS / 10000.0
    s = SLIPPAGE_BPS / 10000.0
    buy = entry * (1.0 + s)
    sell = weighted_exit * (1.0 - s)
    return (sell * (1.0 - c) - buy * (1.0 + c)) / risk


def metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "expectancy_r": 0.0, "profit_factor": 0.0, "win_rate_pct": 0.0}
    r = np.array([_net_r(t) for t in trades], dtype=float)
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
        "exit_reasons": dict(sorted(Counter(t["exit_reason"] for t in trades).items())),
    }


def score(m: dict[str, Any]) -> float:
    n = int(m.get("trades", 0))
    if n < 30:
        return -1e9
    exp = float(m.get("expectancy_r", -99.0))
    pf = float(m.get("profit_factor") or 0.01)
    return exp + 0.05 * math.log(max(pf, 0.01))


def collect(frames, p, start=None, end=None):
    trades = []
    for symbol, data, positions in frames:
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
    profiles = candidates(period)
    frames = []
    times = []
    with MarketDataStore(db, read_only=True) as store:
        symbols = store.list_symbols("BIST", period)
        for i, symbol in enumerate(symbols, 1):
            frame = store.load_dataframe(symbol, "BIST", period, limit=0)
            if frame is None or len(frame) <= WARMUP + 5:
                continue
            data = build_indicator_frame(frame)
            pos = [int(x) for x in np.flatnonzero(data["signal"].to_numpy(bool)) if x >= WARMUP and x + 1 < len(data)]
            if pos:
                frames.append((symbol, data, pos))
                times.extend(pd.Timestamp(data.index[x]) for x in pos)
            if i % 100 == 0 or i == len(symbols):
                print(f"[{period}] {i}/{len(symbols)} loaded", flush=True)
    times.sort()
    if len(times) < 100:
        raise RuntimeError(f"{period}: only {len(times)} signals")

    bounds = [pd.Timestamp(times[min(len(times)-1, int(len(times)*q))]) for q in TEST_BOUNDARIES[:-1]]
    bounds.append(pd.Timestamp(times[-1]) + pd.Timedelta(days=370))
    folds = []
    stitched = []
    selected_names = []

    for fold_idx in range(4):
        test_start = bounds[fold_idx]
        test_end = bounds[fold_idx + 1]
        train_rows = []
        for p in profiles:
            tr = collect(frames, p, start=None, end=test_start)
            m = metrics(tr)
            train_rows.append({"profile": p, "metrics": m, "score": score(m)})
        train_rows.sort(key=lambda r: r["score"], reverse=True)
        chosen = train_rows[0]["profile"]
        te = collect(frames, chosen, start=test_start, end=test_end)
        tm = metrics(te)
        stitched.extend(te)
        selected_names.append(chosen["name"])
        folds.append({
            "fold": fold_idx + 1,
            "test_start": test_start.isoformat(),
            "test_end": test_end.isoformat(),
            "selected_profile": chosen,
            "train_metrics": train_rows[0]["metrics"],
            "test_metrics": tm,
            "train_top5": train_rows[:5],
        })

    stitched_metrics = metrics(stitched)
    fold_expectancies = [float(f["test_metrics"].get("expectancy_r", 0.0)) for f in folds]
    fold_pfs = [float(f["test_metrics"].get("profit_factor") or 0.0) for f in folds]
    positive_folds = sum(x > 0 for x in fold_expectancies)
    pf_above1_folds = sum(x > 1 for x in fold_pfs)

    # Latest fold's selected profile is the only profile that has a directly
    # subsequent recent walk-forward test. Treat it as the next live candidate.
    current_candidate = folds[-1]["selected_profile"]
    classification = "REJECT"
    if stitched_metrics["trades"] >= 100 and stitched_metrics["expectancy_r"] > 0 and (stitched_metrics["profit_factor"] or 0) > 1.10 and positive_folds >= 3:
        classification = "PROMISING"
    if stitched_metrics["trades"] >= 100 and stitched_metrics["expectancy_r"] > 0.08 and (stitched_metrics["profit_factor"] or 0) > 1.20 and positive_folds == 4:
        classification = "STRONG_WALK_FORWARD"

    return {
        "version": "ne-ararsan-var-timeframe-exits-wf-v1",
        "period": period,
        "method": "4 expanding walk-forward next-window folds; 10bps commission + 10bps slippage per side",
        "signals": len(times),
        "candidate_count": len(profiles),
        "folds": folds,
        "stitched_walk_forward_oos": stitched_metrics,
        "stability": {
            "positive_expectancy_folds": positive_folds,
            "pf_above_1_folds": pf_above1_folds,
            "median_fold_expectancy_r": float(np.median(fold_expectancies)),
            "selected_profile_names": selected_names,
        },
        "current_forward_candidate": current_candidate,
        "classification": classification,
        "note": "Previous holdout was already observed; this is walk-forward research, not a new untouched holdout.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--period", choices=("15m", "30m", "45m", "1H", "2H"), required=True)
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
