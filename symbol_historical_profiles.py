"""Build per-stock historical A-I profiles from canonical BIST history.

The profile is descriptive, not a trading recommendation.  Signals are generated
with the same vector parity engine used by production research.  Entry is the
next bar open.  Only signals with a fully observed forward horizon are scored,
so the latest live signal can never leak future information into its own card.

For intraday targets, 30m/45m/1H/2H/4H bars are derived from the persistent 15m
archive with the exact production resampler.  1W/1M are derived from 1D.  This
avoids ranking stocks on direct higher-timeframe artifacts whose volume/bucket
semantics may differ from the live cache.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from bist_timeframes import resample_bist_intraday, resample_calendar
from market_data_store import MarketDataStore, normalize_period
from signal_parity import CODE_TO_STRATEGY, build_signal_frame, fresh_signal_indexes


PERIODS = ("15m", "30m", "45m", "1H", "2H", "4H", "1D", "1W", "1M")
HORIZONS = (1, 2, 4, 8, 16, 32)
PRIMARY_HORIZON = {
    "15m": 16,
    "30m": 8,
    "45m": 8,
    "1H": 8,
    "2H": 4,
    "4H": 4,
    "1D": 8,
    "1W": 4,
    "1M": 2,
}
ROUND_TRIP_COST_PCT = 0.20
MIN_PROFILE_EVENTS = 5
MIN_RANK_EVENTS = 8


def _clip(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return float(max(lower, min(upper, value)))


def _safe_mean(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def _safe_median(values: list[float]) -> float | None:
    return float(np.median(values)) if values else None


def _metric_summary(rows: list[dict[str, float]]) -> dict[str, Any]:
    if not rows:
        return {"events": 0}
    returns = np.asarray([row["return_pct"] for row in rows], dtype=float)
    mfes = np.asarray([row["mfe_pct"] for row in rows], dtype=float)
    maes = np.asarray([row["mae_pct"] for row in rows], dtype=float)
    net = returns - ROUND_TRIP_COST_PCT
    winners = net[net > 0]
    losers = net[net <= 0]
    payoff = None
    if len(winners) and len(losers):
        avg_loss = abs(float(np.mean(losers)))
        if avg_loss > 0:
            payoff = float(np.mean(winners) / avg_loss)
    avg_mae = float(np.mean(maes))
    avg_mfe = float(np.mean(mfes))
    return {
        "events": int(len(rows)),
        "positive_rate_pct": float(np.mean(returns > 0) * 100.0),
        "net_positive_rate_pct": float(np.mean(net > 0) * 100.0),
        "avg_return_pct": float(np.mean(returns)),
        "median_return_pct": float(np.median(returns)),
        "avg_net_pct": float(np.mean(net)),
        "median_net_pct": float(np.median(net)),
        "avg_mfe_pct": avg_mfe,
        "avg_mae_pct": avg_mae,
        "mfe_mae_ratio": float(avg_mfe / abs(avg_mae)) if avg_mae != 0 else None,
        "payoff_ratio": payoff,
    }


def _confidence_label(events: int) -> str:
    if events < 5:
        return "Yetersiz"
    if events < 10:
        return "Dusuk"
    if events < 20:
        return "Orta"
    if events < 40:
        return "Iyi"
    return "Yuksek"


def _quality_label(score: float, events: int) -> str:
    if events < MIN_PROFILE_EVENTS:
        return "Yetersiz veri"
    if events < MIN_RANK_EVENTS:
        return "Dusuk guven"
    if score >= 75:
        return "Cok guclu"
    if score >= 65:
        return "Guclu"
    if score >= 55:
        return "Orta"
    if score >= 45:
        return "Notr"
    return "Zayif"


def _quality_score(primary_rows: list[dict[str, float]]) -> tuple[float, dict[str, Any]]:
    summary = _metric_summary(primary_rows)
    n = int(summary.get("events", 0))
    if n == 0:
        return 0.0, summary

    net_values = [row["return_pct"] - ROUND_TRIP_COST_PCT for row in primary_rows]
    last_ten = net_values[-10:]
    recent_success = float(np.mean(np.asarray(last_ten) > 0) * 100.0) if last_ten else 50.0
    win_score = float(summary.get("net_positive_rate_pct", 50.0))

    payoff = summary.get("payoff_ratio")
    payoff_score = 50.0 if payoff is None else 100.0 * float(payoff) / (1.0 + float(payoff))
    mfe_mae = summary.get("mfe_mae_ratio")
    mfe_score = 50.0 if mfe_mae is None else 100.0 * float(mfe_mae) / (1.0 + float(mfe_mae))

    avg_net = float(summary.get("avg_net_pct", 0.0))
    avg_mae = abs(float(summary.get("avg_mae_pct", 0.0)))
    scale = max(avg_mae, 0.25)
    edge_score = _clip(50.0 + 30.0 * avg_net / scale)

    raw = (
        0.25 * win_score
        + 0.15 * recent_success
        + 0.15 * payoff_score
        + 0.15 * mfe_score
        + 0.30 * edge_score
    )
    confidence = min(1.0, math.sqrt(n / 30.0))
    score = 50.0 + confidence * (raw - 50.0)
    score = _clip(score)
    summary["last10_events"] = int(len(last_ten))
    summary["last10_success"] = int(sum(value > 0 for value in last_ten))
    summary["last10_success_pct"] = recent_success
    return float(round(score, 1)), summary


def _derive_frame(period: str, intraday: pd.DataFrame | None, daily: pd.DataFrame | None) -> pd.DataFrame:
    period = normalize_period(period)
    if period == "15m":
        return intraday.copy() if intraday is not None else pd.DataFrame()
    if period in {"30m", "45m", "1H", "2H", "4H"}:
        return resample_bist_intraday(intraday, period) if intraday is not None else pd.DataFrame()
    if period == "1D":
        return daily.copy() if daily is not None else pd.DataFrame()
    if period in {"1W", "1M"}:
        return resample_calendar(daily, period) if daily is not None else pd.DataFrame()
    raise ValueError(f"Desteklenmeyen periyot: {period}")


def _event_rows(frame: pd.DataFrame, signal_positions: list[int]) -> tuple[dict[int, list[dict[str, float]]], list[pd.Timestamp]]:
    by_horizon: dict[int, list[dict[str, float]]] = {h: [] for h in HORIZONS}
    signal_times: list[pd.Timestamp] = []
    for signal_pos in signal_positions:
        entry_pos = signal_pos + 1
        if entry_pos >= len(frame):
            continue
        entry = float(frame["open"].iloc[entry_pos])
        if not np.isfinite(entry) or entry <= 0:
            continue
        signal_times.append(pd.Timestamp(frame.index[signal_pos]))
        for horizon in HORIZONS:
            end_pos = entry_pos + horizon - 1
            if end_pos >= len(frame):
                continue
            window = frame.iloc[entry_pos : end_pos + 1]
            end_close = float(window["close"].iloc[-1])
            high = float(window["high"].max())
            low = float(window["low"].min())
            if not all(np.isfinite(value) for value in (end_close, high, low)):
                continue
            by_horizon[horizon].append(
                {
                    "return_pct": (end_close / entry - 1.0) * 100.0,
                    "mfe_pct": (high / entry - 1.0) * 100.0,
                    "mae_pct": (low / entry - 1.0) * 100.0,
                }
            )
    return by_horizon, signal_times


def _build_one_profile(period: str, frame: pd.DataFrame, signal_frame: pd.DataFrame, strategy: str) -> dict[str, Any] | None:
    positions = fresh_signal_indexes(signal_frame, strategy)
    if not positions:
        return None
    by_horizon, signal_times = _event_rows(frame, positions)
    primary = PRIMARY_HORIZON[period]
    primary_rows = by_horizon[primary]
    score, primary_summary = _quality_score(primary_rows)
    latest_time = pd.Timestamp(frame.index[-1]) if len(frame) else None
    cutoff = latest_time - pd.Timedelta(days=365 * 3) if latest_time is not None else None
    recent_3y = int(sum(stamp >= cutoff for stamp in signal_times)) if cutoff is not None else 0
    horizons = {str(h): _metric_summary(rows) for h, rows in by_horizon.items()}
    mature_events = int(primary_summary.get("events", 0))
    return {
        "period": period,
        "code": next(code for code, key in CODE_TO_STRATEGY.items() if key == strategy),
        "status": "early_warning" if period == "15m" else "historical_profile",
        "primary_horizon": primary,
        "events": mature_events,
        "signal_events_total": int(len(signal_times)),
        "recent_3y_signals": recent_3y,
        "last_signal_time": signal_times[-1].isoformat() if signal_times else None,
        "quality_score": score,
        "quality_label": _quality_label(score, mature_events),
        "confidence": _confidence_label(mature_events),
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "primary": primary_summary,
        "horizons": horizons,
    }


def build_profiles(
    intraday_db: str,
    daily_db: str,
    periods: list[str],
    *,
    warmup: int = 240,
    max_symbols: int = 0,
) -> dict[str, Any]:
    requested = [normalize_period(value) for value in periods]
    for period in requested:
        if period not in PERIODS:
            raise ValueError(f"Desteklenmeyen periyot: {period}")

    need_intraday = any(period in {"15m", "30m", "45m", "1H", "2H", "4H"} for period in requested)
    need_daily = True

    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "periods": {period: {} for period in requested},
        "source": {
            "intraday": "historical-live-15m; upper intraday derived with production resampler",
            "daily": "historical-live-1D; weekly/monthly derived with production resampler",
        },
    }

    with MarketDataStore(intraday_db, read_only=True) as intraday_store, MarketDataStore(daily_db, read_only=True) as daily_store:
        symbol_sets = []
        if need_intraday:
            symbol_sets.append(set(intraday_store.list_symbols("BIST", "15m")))
        if need_daily:
            symbol_sets.append(set(daily_store.list_symbols("BIST", "1D")))
        symbols = sorted(set.union(*symbol_sets)) if symbol_sets else []
        if max_symbols > 0:
            symbols = symbols[:max_symbols]

        for number, symbol in enumerate(symbols, start=1):
            intraday = intraday_store.load_dataframe(symbol, "BIST", "15m", limit=0) if need_intraday else None
            daily = daily_store.load_dataframe(symbol, "BIST", "1D", limit=0)

            for period in requested:
                frame = _derive_frame(period, intraday, daily)
                if frame is None or len(frame) <= warmup + 2:
                    continue
                signals = build_signal_frame(frame, daily=daily, warmup=warmup, period=period)
                symbol_profiles: dict[str, Any] = {}
                for code, strategy in CODE_TO_STRATEGY.items():
                    profile = _build_one_profile(period, frame, signals, strategy)
                    if profile is not None:
                        symbol_profiles[code] = profile
                if symbol_profiles:
                    result["periods"][period][symbol] = symbol_profiles

            if number % 50 == 0 or number == len(symbols):
                profile_count = sum(
                    len(codes)
                    for period_data in result["periods"].values()
                    for codes in period_data.values()
                )
                print(f"[{number}/{len(symbols)}] profil={profile_count}")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intraday-db", required=True)
    parser.add_argument("--daily-db", required=True)
    parser.add_argument("--periods", default=",".join(PERIODS))
    parser.add_argument("--warmup", type=int, default=240)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    periods = [value.strip() for value in args.periods.split(",") if value.strip()]
    payload = build_profiles(
        args.intraday_db,
        args.daily_db,
        periods,
        warmup=max(50, int(args.warmup)),
        max_symbols=max(0, int(args.max_symbols)),
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "generated_at": payload["generated_at"],
        "periods": {period: len(symbols) for period, symbols in payload["periods"].items()},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
