"""Concise institutional-style stock report composer.

Combines validated technical candidates with optional sector-relative fundamental
scores. Missing fundamental data is shown as missing; it is never invented or
silently converted to a neutral score.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from fundamental_score import score_label, score_peer_group

FACTOR_LABELS = {
    "valuation": "Değerleme", "profitability": "Kârlılık", "growth": "Büyüme",
    "balance_sheet": "Bilanço", "cash_quality": "Nakit/Kâr Kalitesi", "efficiency": "Verimlilik",
}


def _num(v: Any) -> float | None:
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def load_fundamentals(path: str | None) -> dict[str, dict[str, Any]]:
    if not path or not Path(path).exists():
        return {}
    frame = pd.read_csv(path)
    if "symbol" not in frame.columns:
        raise ValueError("fundamentals CSV requires symbol column")
    scored = score_peer_group(frame)
    return {str(r["symbol"]).upper(): r.to_dict() for _, r in scored.iterrows()}


def fundamental_summary(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"status": "VERI YOK", "score": None, "label": "VERI YETERSIZ", "coverage": 0.0, "confidence": "DUSUK", "factors": {}}
    factors = {}
    for key, label in FACTOR_LABELS.items():
        value = _num(row.get(f"factor__{key}"))
        if value is not None:
            factors[label] = round(value, 1)
    score = _num(row.get("fundamental_score"))
    coverage = _num(row.get("fundamental_coverage")) or 0.0
    return {"status": "HAZIR", "score": round(score, 1) if score is not None else None, "label": score_label(score), "coverage": round(coverage, 1), "confidence": str(row.get("fundamental_confidence", "DUSUK")), "sector": row.get("sector"), "factors": factors}


def verdict(candidate: dict[str, Any], fundamental: dict[str, Any]) -> dict[str, str]:
    tech = candidate.get("tier", "B")
    fs = fundamental.get("score")
    if fs is None:
        return {"title": "TEKNIK ADAY · TEMEL VERI BEKLENIYOR", "note": "Teknik yapı doğrulandı; temel kalite hakkında veri olmadan birleşik hüküm verilmedi."}
    if tech == "A" and fs >= 65:
        return {"title": "TEKNIK + TEMEL UYUM GUCLU", "note": "Teknik teyit ile sektör-göreli temel kalite aynı yönde güçlü görünüyor."}
    if tech == "A" and fs < 50:
        return {"title": "TEKNIK GUCLU · TEMEL AYRISMA VAR", "note": "Teknik yapı güçlü olsa da temel kalite puanı aynı ölçüde desteklemiyor; ayrışma raporda özellikle korunmalı."}
    if fs >= 65:
        return {"title": "TEMEL GUCLU · TEKNIK TEYIT SINIRLI", "note": "Şirket kalitesi destekleyici; teknik model birleşimi henüz en yüksek sınıfta değil."}
    return {"title": "IZLEME / SECICI YAKLASIM", "note": "Teknik ve temel kanıtlar birlikte güçlü bir kümelenme oluşturmuyor."}


def build_report(candidate: dict[str, Any], fundamental_row: dict[str, Any] | None) -> dict[str, Any]:
    f = fundamental_summary(fundamental_row)
    s = candidate.get("structure", {})
    e = candidate.get("elliott", {})
    return {
        "symbol": candidate["symbol"], "price": candidate.get("price"), "timeframe": "4H",
        "candidate_class": candidate.get("tier"), "priority": candidate.get("priority"),
        "executive_view": verdict(candidate, f),
        "technical": {
            "structure": s.get("trend"), "bos": s.get("bos"), "swing_high": s.get("last_swing_high"), "swing_low": s.get("last_swing_low"),
            "elliott_phase": e.get("phase"), "elliott_confidence": e.get("confidence"), "reason": candidate.get("reason"),
            "models": candidate.get("models", {}),
        },
        "fundamental": f,
        "risk": _risk(candidate),
        "chart": candidate.get("chart"),
        "disclaimer": "Analitik özet; otomatik AL/SAT emri veya yatırım tavsiyesi değildir.",
    }


def _risk(candidate: dict[str, Any]) -> dict[str, Any]:
    for key in ("system_j", "smi_macd"):
        p = candidate.get(key, {}).get("risk_plan") or {}
        if p and not p.get("error"):
            return {k: p.get(k) for k in ("stop", "tp1", "tp2", "tp3")}
    return {}


def render_scorecard(report: dict[str, Any], destination: Path) -> None:
    factors = report["fundamental"]["factors"]
    labels = list(factors) if factors else ["Temel veri"]
    values = list(factors.values()) if factors else [0]
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    y = np.arange(len(labels))
    ax.barh(y, values)
    ax.set_yticks(y, labels)
    ax.set_xlim(0, 100)
    ax.axvline(50, linewidth=0.8, linestyle="--")
    ax.set_xlabel("Sektör içi puan · 0–100")
    ax.set_title(f"{report['symbol']} · Temel Faktör Karnesi", loc="left", fontweight="bold")
    for i, v in enumerate(values): ax.text(min(v + 1, 97), i, f"{v:.0f}", va="center", fontsize=9)
    ax.grid(axis="x", alpha=0.15)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(); fig.savefig(destination, dpi=150); plt.close(fig)


def to_markdown(r: dict[str, Any]) -> str:
    f, t, risk = r["fundamental"], r["technical"], r["risk"]
    factor_line = " · ".join(f"{k} {v:.0f}" for k, v in f["factors"].items()) or "Temel veri henüz bağlı değil"
    return "\n".join([
        f"# {r['symbol']} · Kısa Hisse Analiz Raporu",
        "",
        f"**{r['executive_view']['title']}** — {r['executive_view']['note']}",
        "",
        "## 1. Hızlı görünüm",
        f"- Fiyat: **{r['price']}** · Periyot: **4H** · Teknik sınıf: **{r['candidate_class']} / {r['priority']}**",
        f"- Piyasa yapısı: **{t['structure']}** · BOS: **{t['bos']}** · Elliott: **{t['elliott_phase']} ({t['elliott_confidence']})**",
        f"- Temel skor: **{f['score'] if f['score'] is not None else '-'} / 100 · {f['label']}** · veri kapsamı %{f['coverage']} / güven {f['confidence']}",
        "",
        "## 2. Teknik okuma",
        f"- {t['reason']}",
        f"- Son swing: destek **{t['swing_low']}** · direnç **{t['swing_high']}**",
        "",
        "## 3. Temel analiz özeti",
        f"- {factor_line}",
        "- Puanlar sektör içi benzer şirketlere göredir; eksik veri nötr kabul edilmez.",
        "",
        "## 4. Risk haritası",
        f"- Stop referansı: **{risk.get('stop', '-')}** · TP1 **{risk.get('tp1', '-')}** · TP2 **{risk.get('tp2', '-')}** · TP3 **{risk.get('tp3', '-')}**",
        "",
        "## 5. Sonuç",
        f"- {r['executive_view']['note']}",
        "- Raporun amacı tek cümlelik AL/SAT üretmek değil; teknik yapı, şirket kalitesi ve riski aynı çerçevede özetlemektir.",
        "",
        f"_{r['disclaimer']}_",
    ]) + "\n"


def compose(visual_json: str, fundamentals_csv: str | None, output_dir: str) -> dict[str, Any]:
    payload = json.loads(Path(visual_json).read_text(encoding="utf-8"))
    fundamentals = load_fundamentals(fundamentals_csv)
    out = Path(output_dir); reports = []
    for c in payload.get("candidates", []):
        r = build_report(c, fundamentals.get(c["symbol"].upper()))
        symbol_dir = out / c["symbol"]
        symbol_dir.mkdir(parents=True, exist_ok=True)
        (symbol_dir / "report.md").write_text(to_markdown(r), encoding="utf-8")
        (symbol_dir / "report.json").write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
        render_scorecard(r, symbol_dir / "fundamental_scorecard.png")
        reports.append(r)
    result = {"report_engine": "institutional-summary-v1", "count": len(reports), "reports": reports}
    (out / "index.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--visual-json", default="data/production-4h/visual_candidates.json")
    p.add_argument("--fundamentals-csv", default="data/fundamentals/latest.csv")
    p.add_argument("--output-dir", default="data/production-4h/reports")
    a = p.parse_args()
    result = compose(a.visual_json, a.fundamentals_csv, a.output_dir)
    print(f"Institutional reports: {result['count']}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
