"""Validated 4H production candidate engine.

This module intentionally uses only the two 4H profiles promoted by the
chronological/cost-aware research gate:

* Karar Paneli v6.4.5 PULLBACK (System J family)
* SMI/MACD Full

It is an analysis/ranking layer, not an order engine. It fails closed when the
4H snapshot is missing and evaluates only completed BIST 4H session buckets.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from decision_panel_v645_signal import latest_decision
from exit_engine import build_exit_plan
from filters import passes_strategy_filter
from indicators import check_smi_macd_signal
from market_data_store import MarketDataStore

ISTANBUL = ZoneInfo("Europe/Istanbul")
PERIOD = "4H"
MIN_BARS = 200


def _to_istanbul(ts: pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        return stamp.tz_localize(ISTANBUL)
    return stamp.tz_convert(ISTANBUL)


def expected_bucket_close(ts: pd.Timestamp) -> pd.Timestamp:
    """Return the expected BIST close time for a stored 4H bucket.

    Intraday resampling uses 10:00, 14:00 and 18:00 session buckets. The final
    18:00 bucket is deliberately treated as the closing-session bucket and is
    complete at 18:10 rather than pretending the exchange trades until 22:00.
    """
    stamp = _to_istanbul(ts)
    local_time = stamp.timetz().replace(tzinfo=None)
    if local_time >= time(18, 0):
        return stamp.normalize() + pd.Timedelta(hours=18, minutes=10)
    if local_time >= time(14, 0):
        return stamp.normalize() + pd.Timedelta(hours=18)
    return stamp.normalize() + pd.Timedelta(hours=14)


def closed_4h_frame(frame: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Drop only the currently forming 4H bucket; historical buckets stay intact."""
    if frame is None or frame.empty:
        return frame
    current = pd.Timestamp(now or datetime.now(ISTANBUL))
    if current.tzinfo is None:
        current = current.tz_localize(ISTANBUL)
    else:
        current = current.tz_convert(ISTANBUL)
    last = _to_istanbul(frame.index[-1])
    if last.date() == current.date() and current < expected_bucket_close(last):
        return frame.iloc[:-1].copy()
    return frame.copy()


def classify_candidate(has_pullback: bool, has_smi_full: bool) -> tuple[str, str, str]:
    if has_pullback and has_smi_full:
        return (
            "A",
            "YUKSEK",
            "Iki bagimsiz, OOS-promote 4H model ayni hissede teyit verdi.",
        )
    if has_pullback:
        return (
            "B+",
            "ONCELIKLI IZLEME",
            "4H System J PULLBACK dogrulandi; ikinci promote model teyidi yok.",
        )
    if has_smi_full:
        return (
            "B",
            "IZLEME",
            "4H SMI/MACD Full dogrulandi; System J PULLBACK teyidi yok.",
        )
    return ("-", "YOK", "Promote edilmis 4H modellerde adaylik yok.")


def _safe_plan(frame: pd.DataFrame, strategy: str, setup: str | None = None) -> dict[str, Any]:
    try:
        plan = build_exit_plan(frame, strategy, setup=setup)
        keys = (
            "entry", "stop", "stop_pct", "tp1", "tp2", "tp3",
            "tp1_pct", "tp2_pct", "tp3_pct", "risk_atr", "stop_reason",
            "profile_key", "family",
        )
        return {key: plan.get(key) for key in keys}
    except Exception as exc:
        return {"error": str(exc)}


def evaluate_symbol(
    symbol: str,
    frame: pd.DataFrame,
    *,
    daily_frame: pd.DataFrame | None = None,
    min_score: int = 70,
) -> dict[str, Any] | None:
    frame = closed_4h_frame(frame)
    if frame is None or len(frame) < MIN_BARS:
        return None

    decision = latest_decision(frame, min_score=min_score)
    has_pullback = bool(decision.get("entry")) and decision.get("new_setup") == "PULLBACK"

    smi = check_smi_macd_signal(frame)
    has_smi_full = bool(smi.get("full_buy_signal")) and passes_strategy_filter(
        frame,
        "smi_macd",
        signal_kind="full",
        daily_df=daily_frame,
    )

    if not (has_pullback or has_smi_full):
        return None

    tier, priority, reason = classify_candidate(has_pullback, has_smi_full)
    price = float(frame["close"].iloc[-1])
    result: dict[str, Any] = {
        "symbol": symbol,
        "period": PERIOD,
        "bar_time": pd.Timestamp(frame.index[-1]).isoformat(),
        "price": price,
        "tier": tier,
        "priority": priority,
        "reason": reason,
        "models": {
            "system_j_pullback": has_pullback,
            "smi_macd_full": has_smi_full,
        },
        "system_j": {
            "score": int(decision.get("score", 0)),
            "rvol": round(float(decision.get("rvol", 0.0)), 3),
            "pct20": round(float(decision.get("pct20", 0.0)), 2),
            "adx": round(float(decision.get("adx", 0.0)), 2),
            "setup": decision.get("new_setup", ""),
        },
        "smi_macd": {
            "details": smi.get("details", {}),
            "daily_limit_filter_source": daily_frame is not None and not daily_frame.empty,
        },
    }
    if has_pullback:
        result["system_j"]["risk_plan"] = _safe_plan(frame, "decision_panel", "PULLBACK")
    if has_smi_full:
        result["smi_macd"]["risk_plan"] = _safe_plan(frame, "smi_macd_full")
    return result


def build_report(database: str, *, exchange: str = "BIST", min_score: int = 70, max_symbols: int = 0) -> dict[str, Any]:
    db = Path(database)
    if not db.is_file() or db.stat().st_size == 0:
        raise RuntimeError(f"4H production snapshot bulunamadi: {db}")

    candidates: list[dict[str, Any]] = []
    skipped = 0
    with MarketDataStore(str(db), read_only=True) as store:
        symbols = store.list_symbols(exchange, PERIOD)
        if not symbols:
            raise RuntimeError("Snapshot icinde BIST/4H veri yok; 1D veriye fallback yasaktir.")
        if max_symbols > 0:
            symbols = symbols[:max_symbols]
        for symbol in symbols:
            frame = store.load_dataframe(symbol, exchange, PERIOD)
            daily_frame = store.load_dataframe(symbol, exchange, "1D", limit=60)
            try:
                item = evaluate_symbol(
                    symbol,
                    frame,
                    daily_frame=daily_frame,
                    min_score=min_score,
                )
            except Exception:
                skipped += 1
                continue
            if item:
                candidates.append(item)

    candidates.sort(
        key=lambda row: (
            {"A": 0, "B+": 1, "B": 2}.get(row["tier"], 9),
            -int(row.get("system_j", {}).get("score", 0)),
            row["symbol"],
        )
    )
    return {
        "engine": "production-4h-v1",
        "period": PERIOD,
        "generated_at": datetime.now(ISTANBUL).isoformat(timespec="seconds"),
        "policy": "analysis-only; promoted models only; closed 4H bars; no timeframe fallback",
        "counts": {
            "symbols": len(symbols),
            "candidates": len(candidates),
            "tier_a": sum(row["tier"] == "A" for row in candidates),
            "tier_b_plus": sum(row["tier"] == "B+" for row in candidates),
            "tier_b": sum(row["tier"] == "B" for row in candidates),
            "skipped": skipped,
        },
        "candidates": candidates,
    }


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# 4H Production Aday Raporu",
        "",
        f"Uretilme: `{report['generated_at']}`",
        f"Kapsam: **{counts['symbols']}** sembol | Aday: **{counts['candidates']}** | A: **{counts['tier_a']}** | B+: **{counts['tier_b_plus']}** | B: **{counts['tier_b']}**",
        "",
        "> Bu rapor emir/AL-SAT motoru degildir. Yalnizca OOS ve maliyet testinden gecerek promote edilen 4H modelleri siralar.",
        "",
    ]
    for row in report["candidates"]:
        models = []
        if row["models"]["system_j_pullback"]:
            models.append("System J PULLBACK")
        if row["models"]["smi_macd_full"]:
            models.append("SMI/MACD Full")
        lines.extend([
            f"## {row['tier']} · {row['symbol']} · {row['priority']}",
            f"- Fiyat: **{row['price']:.2f}** | 4H mum: `{row['bar_time']}`",
            f"- Teyit: {', '.join(models)}",
            f"- Neden: {row['reason']}",
        ])
        if row["models"]["system_j_pullback"]:
            sj = row["system_j"]
            lines.append(f"- System J: skor {sj['score']} · RVOL {sj['rvol']} · 20-bar tepe konumu %{sj['pct20']}")
        lines.append("")
    if not report["candidates"]:
        lines.append("Bu kapanmis 4H mumunda promote modellerden yeni aday yok.\n")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/market_data.sqlite")
    parser.add_argument("--exchange", default="BIST")
    parser.add_argument("--min-score", type=int, default=70)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--output-dir", default="data/production-4h")
    args = parser.parse_args()

    report = build_report(
        args.db,
        exchange=args.exchange,
        min_score=args.min_score,
        max_symbols=args.max_symbols,
    )
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidates.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    markdown = render_markdown(report)
    (out / "report.md").write_text(markdown + "\n", encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
