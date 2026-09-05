"""Top-down multi-timeframe context for concise stock research reports.

This module is explanatory only. It does not create buy/sell orders and it does
not promote an unvalidated timeframe into a production signal. The validated 4H
candidate engine remains the eligibility gate; these views add tactical,
intermediate and primary-trend context around a candidate or an on-demand stock.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from market_data_store import (
    MarketDataStore,
    resample_bist_intraday,
    resample_calendar,
    resample_daily,
)
from production_4h_visual import elliott_context, structure_context

TZ = ZoneInfo("Europe/Istanbul")
PERIODS = ("45m", "2H", "4H", "1D", "1W")
WEIGHTS = {"45m": 0.05, "2H": 0.15, "4H": 0.25, "1D": 0.30, "1W": 0.25}
INTRADAY_MINUTES = {"45m": 45, "2H": 120, "4H": 240}


def _now() -> datetime:
    return datetime.now(TZ)


def _market_close(ts: pd.Timestamp) -> pd.Timestamp:
    day = pd.Timestamp(ts).tz_localize(None).normalize()
    return day + pd.Timedelta(hours=18, minutes=10)


def closed_frame(frame: pd.DataFrame | None, period: str, now: datetime | None = None) -> pd.DataFrame:
    """Drop a still-forming last candle without touching historical bars."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = frame.copy()
    ts = pd.Timestamp(out.index[-1]).tz_localize(None)
    current = pd.Timestamp((now or _now()).replace(tzinfo=None))

    if period in INTRADAY_MINUTES:
        if ts.date() == current.date():
            theoretical = ts + pd.Timedelta(minutes=INTRADAY_MINUTES[period])
            completed_at = min(theoretical, _market_close(ts))
            if current < completed_at:
                out = out.iloc[:-1]
    elif period == "1D":
        if ts.date() == current.date() and current < _market_close(ts):
            out = out.iloc[:-1]
    elif period == "1W":
        week_end = ts.normalize() + pd.Timedelta(days=(4 - ts.weekday()) % 7, hours=18, minutes=10)
        if current < week_end:
            out = out.iloc[:-1]
    return out


def load_period(store: MarketDataStore, symbol: str, period: str, exchange: str = "BIST", limit: int = 400) -> pd.DataFrame:
    """Prefer stored period data; otherwise build it causally from lower periods."""
    direct = store.load_dataframe(symbol, exchange, period, limit=limit)
    if direct is not None and not direct.empty:
        return direct

    if period in INTRADAY_MINUTES:
        base = store.load_dataframe(symbol, exchange, "15m", limit=max(limit * 16, 800))
        return resample_bist_intraday(base, period) if base is not None else pd.DataFrame()
    if period == "1D":
        base = store.load_dataframe(symbol, exchange, "15m", limit=max(limit * 32, 1600))
        return resample_daily(base) if base is not None else pd.DataFrame()
    if period == "1W":
        daily = store.load_dataframe(symbol, exchange, "1D", limit=max(limit * 6, 800))
        if daily is None or daily.empty:
            base = store.load_dataframe(symbol, exchange, "15m", limit=5000)
            daily = resample_daily(base) if base is not None else pd.DataFrame()
        return resample_calendar(daily, "1W") if daily is not None and not daily.empty else pd.DataFrame()
    return pd.DataFrame()


def _atr(frame: pd.DataFrame, length: int = 14) -> float | None:
    if frame is None or len(frame) < 2:
        return None
    h = frame["high"].astype(float)
    l = frame["low"].astype(float)
    c = frame["close"].astype(float)
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    val = tr.rolling(length, min_periods=min(5, length)).mean().iloc[-1]
    return float(val) if np.isfinite(val) else None


def analyze_frame(frame: pd.DataFrame, period: str) -> dict[str, Any]:
    if frame is None or len(frame) < 25:
        return {"period": period, "status": "VERI YETERSIZ"}
    ctx = structure_context(frame)
    ell = elliott_context(ctx)
    close = float(frame["close"].iloc[-1])
    ema20 = float(frame["close"].ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(frame["close"].ewm(span=50, adjust=False).mean().iloc[-1]) if len(frame) >= 50 else None
    swing_h = ctx.get("last_swing_high")
    swing_l = ctx.get("last_swing_low")
    return {
        "period": period,
        "status": "HAZIR",
        "last_bar": str(frame.index[-1]),
        "close": round(close, 4),
        "ema20": round(ema20, 4),
        "ema50": round(ema50, 4) if ema50 is not None else None,
        "price_vs_ema20": "USTUNDE" if close > ema20 else "ALTINDA",
        "ema_regime": None if ema50 is None else ("POZITIF" if ema20 > ema50 else "NEGATIF"),
        "atr14": round(_atr(frame) or 0.0, 4),
        "trend": ctx.get("trend"),
        "bos": ctx.get("bos"),
        "last_swing_high": swing_h,
        "last_swing_low": swing_l,
        "distance_to_resistance_pct": None if not swing_h else round((swing_h / close - 1.0) * 100.0, 2),
        "distance_to_support_pct": None if not swing_l else round((close / swing_l - 1.0) * 100.0, 2),
        "elliott": ell,
        "pivots": ctx.get("pivots", []),
    }


def _direction_score(item: dict[str, Any]) -> float:
    if item.get("status") != "HAZIR":
        return 0.0
    score = 0.0
    if item.get("trend") == "YUKSELEN YAPI": score += 2.0
    elif item.get("trend") == "DUSEN YAPI": score -= 2.0
    if item.get("bos") == "YUKARI BOS": score += 1.0
    elif item.get("bos") == "ASAGI BOS": score -= 1.0
    if item.get("ema_regime") == "POZITIF": score += 0.5
    elif item.get("ema_regime") == "NEGATIF": score -= 0.5
    return max(-3.5, min(3.5, score)) / 3.5


def alignment(contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    used = [(p, WEIGHTS[p], _direction_score(contexts.get(p, {}))) for p in PERIODS if contexts.get(p, {}).get("status") == "HAZIR"]
    if not used:
        return {"score": None, "label": "VERI YETERSIZ", "agreement": 0}
    weight = sum(w for _, w, _ in used)
    raw = sum(w * s for _, w, s in used) / weight
    score = round(raw * 100.0, 1)
    if score >= 45: label = "YUKARI UYUMLU"
    elif score <= -45: label = "ASAGI UYUMLU"
    else: label = "KARMA / GECIS"
    signs = [1 if s > 0.15 else -1 if s < -0.15 else 0 for _, _, s in used]
    agreement = round(max(signs.count(1), signs.count(-1), signs.count(0)) / len(signs) * 100.0, 1)
    return {"score": score, "label": label, "agreement": agreement}


def analyze_symbol(store: MarketDataStore, symbol: str, exchange: str = "BIST", now: datetime | None = None) -> dict[str, Any]:
    contexts: dict[str, dict[str, Any]] = {}
    frames: dict[str, pd.DataFrame] = {}
    for period in PERIODS:
        frame = closed_frame(load_period(store, symbol, period, exchange), period, now)
        frames[period] = frame
        contexts[period] = analyze_frame(frame, period)
    ready = [v for v in contexts.values() if v.get("status") == "HAZIR"]
    price = next((v.get("close") for p in ("4H", "2H", "1D", "45m", "1W") for v in [contexts[p]] if v.get("close") is not None), None)
    return {
        "symbol": symbol.upper(),
        "price": price,
        "generated_at": (now or _now()).isoformat(timespec="seconds"),
        "role": "CONTEXT_ONLY_NOT_A_SIGNAL",
        "timeframes": contexts,
        "alignment": alignment(contexts),
        "coverage": f"{len(ready)}/{len(PERIODS)}",
    }, frames


def render(symbol_report: dict[str, Any], frames: dict[str, pd.DataFrame], destination: Path) -> None:
    periods = ("1W", "1D", "4H", "2H")
    fig, axes = plt.subplots(len(periods), 1, figsize=(14, 12), constrained_layout=True)
    for ax, period in zip(axes, periods):
        frame = frames.get(period, pd.DataFrame()).tail(90)
        ctx = symbol_report["timeframes"].get(period, {})
        if frame.empty:
            ax.text(0.5, 0.5, f"{period}: veri yok", ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off(); continue
        x = np.arange(len(frame))
        close = frame["close"].astype(float).to_numpy()
        ax.plot(x, close, linewidth=1.2, label="Kapanış")
        ax.plot(x, frame["close"].ewm(span=20, adjust=False).mean(), linewidth=1.0, label="EMA20")
        if len(frame) >= 50:
            ax.plot(x, frame["close"].ewm(span=50, adjust=False).mean(), linewidth=1.0, label="EMA50")
        sh, sl = ctx.get("last_swing_high"), ctx.get("last_swing_low")
        if isinstance(sh, (int, float)): ax.axhline(sh, linestyle="--", linewidth=0.8)
        if isinstance(sl, (int, float)): ax.axhline(sl, linestyle="--", linewidth=0.8)
        ax.set_title(f"{period} · {ctx.get('trend','-')} · {ctx.get('bos','-')} · Elliott {ctx.get('elliott',{}).get('phase','-')}", loc="left", fontsize=10, fontweight="bold")
        ax.grid(alpha=0.15); ax.legend(loc="upper left", fontsize=7)
        ticks = np.linspace(0, len(frame)-1, min(6, len(frame)), dtype=int)
        ax.set_xticks(ticks, [pd.Timestamp(frame.index[i]).strftime("%d.%m.%y") for i in ticks], fontsize=7)
    a = symbol_report["alignment"]
    fig.suptitle(f"{symbol_report['symbol']} · Çoklu Zaman Dilimi Yapı Haritası · {a['label']} ({a['score']})", fontsize=14, fontweight="bold")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=150)
    plt.close(fig)


def markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['symbol']} · Çoklu Zaman Dilimi Teknik Bağlam",
        "",
        f"- Uyum: **{report['alignment']['label']}** · skor **{report['alignment']['score']}** · kapsama **{report['coverage']}**",
        "- Bu bölüm sinyal üretmez; doğrulanmış production modelinin etrafındaki üstten-aşağı bağlamı açıklar.",
        "",
        "| Periyot | Yapı | BOS | EMA rejimi | Elliott | Destek | Direnç |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for p in PERIODS:
        x = report["timeframes"].get(p, {})
        if x.get("status") != "HAZIR":
            lines.append(f"| {p} | Veri yetersiz | - | - | - | - | - |")
        else:
            lines.append(f"| {p} | {x.get('trend')} | {x.get('bos')} | {x.get('ema_regime')} | {x.get('elliott',{}).get('phase')} | {x.get('last_swing_low') or '-'} | {x.get('last_swing_high') or '-'} |")
    return "\n".join(lines) + "\n"


def run(db: str, symbols: list[str], output_dir: str, exchange: str = "BIST") -> dict[str, Any]:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    reports = []
    with MarketDataStore(db, read_only=True) as store:
        for symbol in symbols:
            report, frames = analyze_symbol(store, symbol, exchange)
            symbol_dir = out / symbol.upper(); symbol_dir.mkdir(parents=True, exist_ok=True)
            (symbol_dir / "mtf_context.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
            (symbol_dir / "mtf_context.md").write_text(markdown(report), encoding="utf-8")
            render(report, frames, symbol_dir / "mtf_structure.png")
            reports.append(report)
    index = {"engine": "multi-timeframe-context-v1", "count": len(reports), "symbols": [r["symbol"] for r in reports], "reports": reports}
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
    return index


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/market_data.sqlite")
    p.add_argument("--symbols", default="ZGYO")
    p.add_argument("--exchange", default="BIST")
    p.add_argument("--output-dir", default="data/multi-timeframe")
    a = p.parse_args()
    symbols = [s.strip().upper() for s in a.symbols.replace(",", " ").split() if s.strip()]
    result = run(a.db, symbols, a.output_dir, a.exchange)
    print(f"Multi-timeframe context: {result['count']} symbol(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
