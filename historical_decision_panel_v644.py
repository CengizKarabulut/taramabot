"""No-lookahead historical replay for Karar Paneli Pine v6.4.4.

This module mirrors the entry logic in the user's latest TradingView file:
BIST Trend-Momentum Karar Paneli v6.4.4 — POC Only / Format Fix.
POC is intentionally not recomputed because v6.4.4 does not use POC in the
entry decision. Exit simulation uses the shared setup-aware decision_panel
profiles so BREAKOUT / PULLBACK / TREND DEVAMI can be evaluated separately.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from market_data_store import MarketDataStore
from strategy_optimizer import robust_metrics
from trade_backtester import simulate_trade


SETUPS = ("BREAKOUT", "PULLBACK", "TREND DEVAMI")


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    lookup = {str(c).lower(): c for c in frame.columns}
    required = ("open", "high", "low", "close", "volume")
    if any(name not in lookup for name in required):
        missing = [name for name in required if name not in lookup]
        raise ValueError(f"Eksik OHLCV sütunları: {missing}")
    out = frame[[lookup[name] for name in required]].copy()
    out.columns = list(required)
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["open", "high", "low", "close"]).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out["volume"] = out["volume"].fillna(0.0)
    return out


def _rma(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def _atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return _rma(tr, length)


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _rma(gain, length)
    avg_loss = _rma(loss, length)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))
    return result.where(avg_loss != 0, 100.0)


def _adx(df: pd.DataFrame, di_len: int = 14, adx_smooth: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_di = _rma(tr, di_len)
    plus_di = 100.0 * _rma(plus_dm, di_len) / atr_di.replace(0, np.nan)
    minus_di = 100.0 * _rma(minus_dm, di_len) / atr_di.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = _rma(dx, adx_smooth)
    return plus_di, minus_di, adx


def _cross_up(left: pd.Series, right: pd.Series) -> pd.Series:
    return (left > right) & (left.shift(1) <= right.shift(1))


def decision_series(frame: pd.DataFrame, *, min_score: int = 75) -> pd.DataFrame:
    """Return causal v6.4.4 decision state for every daily bar."""
    df = _normalize(frame)
    o, h, l, c, v = (df[name] for name in ("open", "high", "low", "close", "volume"))

    sma20 = c.rolling(20).mean()
    sma50 = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    ema5 = c.ewm(span=5, adjust=False, min_periods=5).mean()
    ema8 = c.ewm(span=8, adjust=False, min_periods=8).mean()
    ema13 = c.ewm(span=13, adjust=False, min_periods=13).mean()

    slope20_pct = (sma20 / sma20.shift(5) - 1.0) * 100.0
    slope50_pct = (sma50 / sma50.shift(10) - 1.0) * 100.0
    slope200_pct = (sma200 / sma200.shift(20) - 1.0) * 100.0

    price_above20 = c > sma20
    sma20_above50 = sma20 > sma50
    sma50_above200 = sma50 > sma200
    slope20_ok = slope20_pct >= 0.0
    slope50_ok = slope50_pct >= 0.0
    slope200_ok = slope200_pct >= -0.5
    trend_core = price_above20 & sma20_above50 & sma50_above200 & slope20_ok & slope50_ok & slope200_ok

    ema5_slope_up = ema5 > ema5.shift(1)
    ema8_slope_up = ema8 > ema8.shift(1)
    ema13_slope_up = ema13 > ema13.shift(1)
    short_bull_aligned = (ema5 > ema8) & (ema8 > ema13)
    short_slope_up = ema5_slope_up & ema8_slope_up & ema13_slope_up
    short_trend_core = short_bull_aligned & short_slope_up
    short_recovery = _cross_up(ema5, ema8) | ((ema5 > ema8) & ema5_slope_up & ema8_slope_up)

    macd = c.ewm(span=12, adjust=False, min_periods=12).mean() - c.ewm(span=26, adjust=False, min_periods=26).mean()
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    histogram = macd - macd_signal
    macd_above_signal = macd > macd_signal
    macd_above_zero = macd > 0
    hist_positive = histogram > 0
    hist_negative = histogram < 0
    hist_rising = histogram > histogram.shift(1)
    hist_falling = histogram < histogram.shift(1)
    hist_cross_up = hist_positive & (histogram.shift(1) <= 0)
    hist_bull_strengthening = hist_positive & hist_rising
    hist_bull_weakening = hist_positive & hist_falling
    hist_bear_recovering = hist_negative & hist_rising

    rsi = _rsi(c, 14)
    rsi_trend_ok = (rsi >= 55.0) & (rsi <= 72.0)
    rsi_pullback_ok = (rsi >= 50.0) & (rsi <= 72.0)
    rsi_ideal = (rsi >= 55.0) & (rsi <= 68.0)

    plus_di, minus_di, adx = _adx(df, 14, 14)
    adx_ok = adx > 20.0
    di_ok = plus_di > minus_di
    strength_core = adx_ok & di_ok

    atr = _atr(df, 14)
    atr_pct = atr / c.replace(0, np.nan) * 100.0
    atr_pct_ok = (atr_pct >= 1.5) & (atr_pct <= 7.0)

    avg_volume = v.rolling(10).mean().shift(1)
    rvol = v / avg_volume.replace(0, np.nan)
    turnover = c * v
    avg_turnover = turnover.rolling(20).mean().shift(1)
    relative_turnover = turnover / avg_turnover.replace(0, np.nan)

    high52 = h.rolling(252).max()
    high20 = h.rolling(20).max()
    previous20_high = high20.shift(1)
    pct_52 = c / high52.replace(0, np.nan) * 100.0
    pct_20 = c / high20.replace(0, np.nan) * 100.0
    near52 = pct_52 >= 85.0
    near20 = pct_20 >= 95.0
    breakout20 = c > previous20_high
    required_rvol = pd.Series(np.where(breakout20, 1.50, 1.20), index=df.index)
    participation_ok = rvol > required_rvol

    dist_sma20_pct = (c / sma20.replace(0, np.nan) - 1.0) * 100.0
    dist_sma20_atr = (c - sma20) / atr.replace(0, np.nan)
    distance_core = (dist_sma20_pct >= 0.0) & (dist_sma20_pct <= 7.0) & (dist_sma20_atr >= 0.0) & (dist_sma20_atr <= 1.50)

    bb_basis = c.rolling(20).mean()
    bb_dev = 2.0 * c.rolling(20).std(ddof=0)
    bb_upper = bb_basis + bb_dev
    bb_lower = bb_basis - bb_dev
    bb_width = (bb_upper - bb_lower) / bb_basis.replace(0, np.nan) * 100.0
    bb_width_avg = bb_width.rolling(20).mean()
    bb_width_rising = bb_width > bb_width.shift(1)
    bb_width_above_avg = bb_width > bb_width_avg

    candle_range = (h - l).replace(0, np.nan)
    clv = ((c - l) / candle_range).fillna(0.5)
    clv_ok = clv >= 0.60
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    upper_wick_atr = upper_wick / atr.replace(0, np.nan)
    upper_wick_ok = upper_wick_atr <= 0.50

    prior_dist = (c - sma20) / atr.replace(0, np.nan)
    was_above_sma20 = prior_dist.shift(1).rolling(10).max() >= 0.75
    sma20_touched = (l <= sma20 + 0.25 * atr) & (l >= sma20 - 0.50 * atr)
    reclaimed_sma20 = c > sma20
    pullback_reversal = (c > o) | (clv >= 0.60)
    pullback_setup = (~breakout20.fillna(False)) & was_above_sma20 & sma20_touched & reclaimed_sma20 & (dist_sma20_atr <= 1.0) & pullback_reversal

    pullback_momentum_ok = macd_above_zero & rsi_pullback_ok & (hist_bear_recovering | hist_cross_up | hist_bull_strengthening)
    pullback_short_ok = short_recovery | short_trend_core
    breakout_momentum_ok = macd_above_signal & macd_above_zero & rsi_trend_ok & (hist_bull_strengthening | hist_cross_up)
    trend_momentum_ok = breakout_momentum_ok
    breakout_quality_ok = bb_width_rising & clv_ok & upper_wick_ok

    trend_score = (
        4 * price_above20.astype(int)
        + 4 * sma20_above50.astype(int)
        + 4 * sma50_above200.astype(int)
        + 3 * slope20_ok.astype(int)
        + 3 * slope50_ok.astype(int)
        + 2 * (slope200_pct > 0).astype(int)
        + 3 * short_bull_aligned.astype(int)
        + 2 * short_slope_up.astype(int)
    )
    hist_regime_score = pd.Series(
        np.select(
            [hist_cross_up, hist_bull_strengthening, hist_bear_recovering, hist_bull_weakening],
            [6, 6, 4, 2],
            default=0,
        ),
        index=df.index,
    )
    momentum_score = 4 * macd_above_zero.astype(int) + 5 * macd_above_signal.astype(int) + hist_regime_score + 5 * rsi_ideal.astype(int)
    rvol_score = pd.Series(
        np.select(
            [rvol >= 3.0, rvol >= 2.0, rvol >= 1.5, rvol >= 1.2, rvol >= 1.0],
            [18, 16, 12, 7, 3],
            default=0,
        ),
        index=df.index,
    )
    volume_score = rvol_score + 2 * (relative_turnover >= 1.0).astype(int)
    adx_score = pd.Series(
        np.select([adx >= 40, adx >= 30, adx >= 25, adx > 20], [7, 6, 5, 3], default=0),
        index=df.index,
    )
    strength_score = adx_score + 3 * di_ok.astype(int)
    high52_score = pd.Series(
        np.select([pct_52 >= 95, pct_52 >= 90, pct_52 >= 85], [7, 5, 3], default=0),
        index=df.index,
    )
    location_score = high52_score + 3 * near20.astype(int)
    dist_pct_score = pd.Series(
        np.select(
            [
                (dist_sma20_pct >= 0) & (dist_sma20_pct <= 3),
                (dist_sma20_pct > 3) & (dist_sma20_pct <= 5),
                (dist_sma20_pct > 5) & (dist_sma20_pct <= 7),
            ],
            [5, 4, 2],
            default=0,
        ),
        index=df.index,
    )
    dist_atr_score = pd.Series(
        np.select(
            [
                (dist_sma20_atr >= 0) & (dist_sma20_atr < 1.0),
                (dist_sma20_atr >= 1.0) & (dist_sma20_atr <= 1.5),
                (dist_sma20_atr > 1.5) & (dist_sma20_atr <= 2.0),
            ],
            [5, 4, 2],
            default=0,
        ),
        index=df.index,
    )
    entry_score = dist_pct_score + dist_atr_score
    volatility_score = 2 * bb_width_rising.astype(int) + bb_width_above_avg.astype(int) + 2 * atr_pct_ok.astype(int)
    total_score = (trend_score + momentum_score + volume_score + strength_score + location_score + entry_score + volatility_score).astype(int)

    positions = pd.Series(np.arange(len(df)), index=df.index)
    history_ready = (positions >= 251) & sma200.shift(20).notna() & rsi.notna() & atr.notna() & high52.notna()
    structure_core = trend_core & strength_core & near52
    score_ok = total_score >= int(min_score)
    common = history_ready & structure_core & participation_ok & distance_core & score_ok

    breakout = common & breakout20 & short_trend_core & breakout_momentum_ok & breakout_quality_ok
    pullback = history_ready & structure_core & participation_ok & distance_core & score_ok & pullback_setup & pullback_momentum_ok & pullback_short_ok
    trend = common & (~breakout20.fillna(False)) & (~pullback_setup.fillna(False)) & short_trend_core & trend_momentum_ok

    new_breakout = breakout & (~breakout.shift(1).fillna(False))
    new_pullback = pullback & (~pullback.shift(1).fillna(False))
    new_trend = trend & (~trend.shift(1).fillna(False))

    out = pd.DataFrame(index=df.index)
    out["score"] = total_score
    out["rvol"] = rvol
    out["adx"] = adx
    out["breakout"] = breakout.fillna(False)
    out["pullback"] = pullback.fillna(False)
    out["trend"] = trend.fillna(False)
    out["new_breakout"] = new_breakout.fillna(False)
    out["new_pullback"] = new_pullback.fillna(False)
    out["new_trend"] = new_trend.fillna(False)
    out["entry"] = (new_breakout | new_pullback | new_trend).fillna(False)
    return out


def replay_symbol(symbol: str, frame: pd.DataFrame, *, min_score: int = 75) -> tuple[list[dict[str, Any]], int]:
    clean = _normalize(frame)
    state = decision_series(clean, min_score=min_score)
    signals: list[tuple[int, str]] = []
    for pos in np.flatnonzero(state["entry"].to_numpy(dtype=bool)):
        setup = "BREAKOUT" if bool(state["new_breakout"].iloc[pos]) else "PULLBACK" if bool(state["new_pullback"].iloc[pos]) else "TREND DEVAMI"
        signals.append((int(pos), setup))

    trades: list[dict[str, Any]] = []
    last_exit_position = -1
    index_lookup = {pd.Timestamp(value): pos for pos, value in enumerate(clean.index)}
    for signal_pos, setup in signals:
        if signal_pos <= last_exit_position:
            continue
        result = simulate_trade(clean, signal_pos, "decision_panel", setup=setup)
        if result is None:
            continue
        result.update(
            {
                "symbol": symbol,
                "period": "1D",
                "signal_time": pd.Timestamp(clean.index[signal_pos]).isoformat(),
                "signal_index": signal_pos,
                "signal_score": int(state["score"].iloc[signal_pos]),
                "signal_rvol": float(state["rvol"].iloc[signal_pos]) if pd.notna(state["rvol"].iloc[signal_pos]) else None,
                "signal_adx": float(state["adx"].iloc[signal_pos]) if pd.notna(state["adx"].iloc[signal_pos]) else None,
            }
        )
        trades.append(result)
        exit_pos = index_lookup.get(pd.Timestamp(result["exit_time"]))
        if exit_pos is not None:
            last_exit_position = exit_pos
    return trades, len(signals)


def replay_database(database: str | Path, *, max_symbols: int = 0, min_score: int = 75) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    raw_signals = 0
    processed = 0
    errors = 0
    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", "1D")
        if max_symbols > 0:
            symbols = symbols[:max_symbols]
        for number, symbol in enumerate(symbols, start=1):
            frame = store.load_dataframe(symbol, "BIST", "1D", limit=0)
            if frame is None or frame.empty:
                continue
            try:
                symbol_trades, signal_count = replay_symbol(symbol, frame, min_score=min_score)
                trades.extend(symbol_trades)
                raw_signals += signal_count
                processed += 1
                print(f"[{number}/{len(symbols)}] {symbol}: {signal_count} yeni sinyal, {len(symbol_trades)} işlem")
            except Exception as exc:
                errors += 1
                print(f"[{number}/{len(symbols)}] {symbol}: HATA {exc}")

    combined = robust_metrics(trades)
    by_setup: dict[str, Any] = {}
    for setup in SETUPS:
        subset = [trade for trade in trades if trade.get("setup") == setup]
        by_setup[setup] = robust_metrics(subset)
    metrics = {
        "strategy": "decision_panel_v6.4.4",
        "period": "1D",
        "min_score": min_score,
        "symbols_processed": processed,
        "errors": errors,
        "raw_new_signals": raw_signals,
        "combined": combined,
        "by_setup": by_setup,
    }
    return trades, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Karar Paneli Pine v6.4.4 historical replay")
    parser.add_argument("--db", required=True)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--min-score", type=int, default=75)
    parser.add_argument("--output-dir", default="reports/decision_panel_backtest")
    args = parser.parse_args()

    trades, metrics = replay_database(args.db, max_symbols=args.max_symbols, min_score=args.min_score)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "decision_panel_v644_trades.json").write_text(
        json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (output / "decision_panel_v644_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
