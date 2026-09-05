"""Visual/structure layer for validated 4H production candidates.

The goal is explanation, not signal generation.  Candidate eligibility remains
owned by production_4h.py.  This module adds causal market-structure context,
conservative Elliott-style phase labels, risk levels and PNG cards.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from market_data_store import MarketDataStore
from production_4h import PERIOD, closed_4h_frame


def confirmed_pivots(frame: pd.DataFrame, left: int = 3, right: int = 3) -> list[dict[str, Any]]:
    """Return pivots confirmed only after `right` bars; no future-looking live pivot."""
    if frame is None or len(frame) < left + right + 1:
        return []
    hi = frame["high"].astype(float).to_numpy()
    lo = frame["low"].astype(float).to_numpy()
    out: list[dict[str, Any]] = []
    for i in range(left, len(frame) - right):
        hwin = hi[i-left:i+right+1]
        lwin = lo[i-left:i+right+1]
        if hi[i] == np.max(hwin) and np.sum(hwin == hi[i]) == 1:
            out.append({"i": i, "time": frame.index[i], "kind": "H", "price": float(hi[i]), "confirmed_at": frame.index[i+right]})
        if lo[i] == np.min(lwin) and np.sum(lwin == lo[i]) == 1:
            out.append({"i": i, "time": frame.index[i], "kind": "L", "price": float(lo[i]), "confirmed_at": frame.index[i+right]})
    out.sort(key=lambda x: x["i"])
    # Collapse consecutive same-side pivots to the more extreme point.
    clean: list[dict[str, Any]] = []
    for p in out:
        if clean and clean[-1]["kind"] == p["kind"]:
            better = p["price"] > clean[-1]["price"] if p["kind"] == "H" else p["price"] < clean[-1]["price"]
            if better:
                clean[-1] = p
        else:
            clean.append(p)
    return clean


def label_structure(pivots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last_h = last_l = None
    labelled = []
    for p in pivots:
        q = dict(p)
        if p["kind"] == "H":
            q["label"] = "H" if last_h is None else ("HH" if p["price"] > last_h else "LH")
            last_h = p["price"]
        else:
            q["label"] = "L" if last_l is None else ("HL" if p["price"] > last_l else "LL")
            last_l = p["price"]
        labelled.append(q)
    return labelled


def structure_context(frame: pd.DataFrame) -> dict[str, Any]:
    pivots = label_structure(confirmed_pivots(frame))
    recent = pivots[-8:]
    highs = [p for p in pivots if p["kind"] == "H"]
    lows = [p for p in pivots if p["kind"] == "L"]
    close = float(frame["close"].iloc[-1])
    last_h = highs[-1] if highs else None
    last_l = lows[-1] if lows else None
    bos = "YOK"
    if last_h and close > last_h["price"]:
        bos = "YUKARI BOS"
    elif last_l and close < last_l["price"]:
        bos = "ASAGI BOS"
    labels = [p["label"] for p in recent]
    if len(labels) >= 4 and labels[-4:] == ["HH", "HL", "HH", "HL"]:
        trend = "YUKSELEN YAPI"
    elif len(labels) >= 4 and labels[-4:] == ["LL", "LH", "LL", "LH"]:
        trend = "DUSEN YAPI"
    else:
        trend = "KARMA / GECIS"
    return {
        "trend": trend,
        "bos": bos,
        "last_swing_high": last_h["price"] if last_h else None,
        "last_swing_low": last_l["price"] if last_l else None,
        "pivots": [{**p, "time": str(p["time"]), "confirmed_at": str(p["confirmed_at"])} for p in recent],
    }


def elliott_context(structure: dict[str, Any]) -> dict[str, str]:
    """Conservative context only; deliberately does not claim exact wave counts."""
    trend = structure["trend"]
    bos = structure["bos"]
    if trend == "YUKSELEN YAPI" and bos == "YUKARI BOS":
        return {"phase": "ITKI ADAYI", "confidence": "ORTA", "note": "HH/HL yapisi ve yukari kirilim impulsif faz ile uyumlu; kesin Elliott dalga numarasi atanmaz."}
    if trend == "YUKSELEN YAPI":
        return {"phase": "YUKSELIS / DUZELTME", "confidence": "DUSUK-ORTA", "note": "Ana yapi yukari; son hareket impuls veya ABC duzeltmesi olabilir. Ek teyit olmadan 1-5 sayimi yapilmaz."}
    if trend == "DUSEN YAPI":
        return {"phase": "DUSUS / DUZELTME", "confidence": "DUSUK-ORTA", "note": "LH/LL yapisi baskin; yukari hareketler duzeltme olabilir."}
    return {"phase": "BELIRSIZ", "confidence": "DUSUK", "note": "Pivot dizisi temiz bir impuls/duzeltme ayrimi vermiyor; Elliott sayimi zorlanmadi."}


def _risk_plan(candidate: dict[str, Any]) -> dict[str, Any]:
    for key in ("system_j", "smi_macd"):
        plan = candidate.get(key, {}).get("risk_plan") or {}
        if plan and not plan.get("error"):
            return plan
    return {}


def enrich(candidate: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    structure = structure_context(frame)
    return {**candidate, "structure": structure, "elliott": elliott_context(structure)}


def render_card(candidate: dict[str, Any], frame: pd.DataFrame, destination: Path, bars: int = 90) -> None:
    df = frame.tail(bars).copy()
    x = np.arange(len(df))
    fig = plt.figure(figsize=(14, 8), constrained_layout=True)
    gs = fig.add_gridspec(4, 1, height_ratios=[3.4, 0.9, 0.8, 0.9])
    ax = fig.add_subplot(gs[0])
    axv = fig.add_subplot(gs[1], sharex=ax)
    axt = fig.add_subplot(gs[2]); axt.axis("off")
    axn = fig.add_subplot(gs[3]); axn.axis("off")

    width = 0.62
    for j, (_, row) in enumerate(df.iterrows()):
        o, h, l, c = map(float, (row.open, row.high, row.low, row.close))
        up = c >= o
        ax.vlines(j, l, h, linewidth=0.8)
        body_y = min(o, c); body_h = max(abs(c-o), max(c*0.0005, 1e-6))
        ax.add_patch(Rectangle((j-width/2, body_y), width, body_h, fill=up, linewidth=1.0))
    ax.plot(x, df["close"].ewm(span=20, adjust=False).mean(), linewidth=1.1, label="EMA20")
    ax.plot(x, df["close"].ewm(span=50, adjust=False).mean(), linewidth=1.1, label="EMA50")

    ctx = candidate["structure"]
    time_to_x = {pd.Timestamp(t): i for i, t in enumerate(df.index)}
    for p in ctx["pivots"]:
        t = pd.Timestamp(p["time"])
        if t in time_to_x:
            j = time_to_x[t]
            ax.annotate(p["label"], (j, p["price"]), xytext=(0, 8 if p["kind"] == "H" else -14), textcoords="offset points", ha="center", fontsize=8)

    plan = _risk_plan(candidate)
    for key, label, ls in (("stop", "STOP", "--"), ("tp1", "TP1", ":"), ("tp2", "TP2", ":"), ("tp3", "TP3", ":")):
        value = plan.get(key)
        if isinstance(value, (int, float)) and np.isfinite(value):
            ax.axhline(value, linewidth=0.9, linestyle=ls)
            ax.text(len(df)-1, value, f" {label} {value:.2f}", va="bottom", fontsize=8)

    ax.set_title(f"{candidate['symbol']} · 4H · {candidate['tier']} / {candidate['priority']} · {candidate['price']:.2f}", loc="left", fontweight="bold")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.15)
    axv.bar(x, df["volume"].astype(float).fillna(0).to_numpy(), width=0.7)
    axv.set_ylabel("Hacim", fontsize=8); axv.grid(alpha=0.12)
    ticks = np.linspace(0, len(df)-1, min(7, len(df)), dtype=int)
    axv.set_xticks(ticks, [pd.Timestamp(df.index[i]).strftime("%d.%m\n%H:%M") for i in ticks], fontsize=8)

    models = []
    if candidate["models"].get("system_j_pullback"): models.append("System J PULLBACK")
    if candidate["models"].get("smi_macd_full"): models.append("SMI/MACD Full")
    axt.text(0.01, 0.75, f"TEYIT: {', '.join(models)}", fontsize=10, fontweight="bold")
    axt.text(0.01, 0.35, f"YAPI: {ctx['trend']} · {ctx['bos']} · Elliott: {candidate['elliott']['phase']} ({candidate['elliott']['confidence']})", fontsize=10)
    axn.text(0.01, 0.65, candidate["reason"], fontsize=9, wrap=True)
    axn.text(0.01, 0.20, candidate["elliott"]["note"], fontsize=8, wrap=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=150)
    plt.close(fig)


def build_visual_report(db_path: str, candidates_path: str, output_dir: str) -> dict[str, Any]:
    report = json.loads(Path(candidates_path).read_text(encoding="utf-8"))
    out = Path(output_dir); charts = out / "charts"; charts.mkdir(parents=True, exist_ok=True)
    enriched = []
    with MarketDataStore(db_path, read_only=True) as store:
        for candidate in report.get("candidates", []):
            frame = closed_4h_frame(store.load_dataframe(candidate["symbol"], "BIST", PERIOD))
            if frame is None or frame.empty:
                continue
            item = enrich(candidate, frame)
            item["chart"] = f"charts/{candidate['symbol']}.png"
            render_card(item, frame, out / item["chart"])
            enriched.append(item)
    payload = {**report, "visual_engine": "production-4h-visual-v1", "candidates": enriched}
    (out / "visual_candidates.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
    lines = ["# 4H Profesyonel Aday Raporu", "", "Bu rapor analiz/izleme amaclidir; otomatik AL/SAT emri degildir.", ""]
    for c in enriched:
        plan = _risk_plan(c)
        lines += [f"## {c['tier']} · {c['symbol']} · {c['priority']}", f"- Fiyat: **{c['price']:.2f}** · Yapı: **{c['structure']['trend']}** · BOS: **{c['structure']['bos']}**", f"- Elliott bağlamı: **{c['elliott']['phase']}** / {c['elliott']['confidence']}", f"- Gerekçe: {c['reason']}"]
        if plan:
            lines.append(f"- Risk haritası: stop **{plan.get('stop', '-')}** · TP1 **{plan.get('tp1', '-')}** · TP2 **{plan.get('tp2', '-')}** · TP3 **{plan.get('tp3', '-')}**")
        lines += [f"- Grafik: `{c['chart']}`", ""]
    (out / "visual_report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    return payload


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/market_data.sqlite")
    p.add_argument("--candidates", default="data/production-4h/candidates.json")
    p.add_argument("--output-dir", default="data/production-4h")
    a = p.parse_args()
    payload = build_visual_report(a.db, a.candidates, a.output_dir)
    print(f"Visual report: {len(payload['candidates'])} candidate(s)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
