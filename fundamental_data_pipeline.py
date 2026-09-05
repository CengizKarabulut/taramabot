"""Fundamental data pipeline for concise BIST research reports.

Two layers are deliberately separated:
1) peer snapshot: bulk İş Yatırım screener data for sector-relative scoring;
2) candidate history: 8-quarter statements for trend/quality commentary.

The module uses borsapy, already installed by the production requirements. Missing
fields are never fabricated. Detailed statement metrics are used for trend
analysis, while peer scoring stays based on the broad market snapshot so one
candidate is not unfairly ranked against a tiny subset of companies.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


PEER_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "ticker", "code", "kod", "hisse"),
    "sector": ("sector", "sector_name", "sektor", "sektör"),
    "market_cap": ("market_cap", "marketcap", "piyasa_degeri", "piyasa değeri"),
    "pe": ("pe", "pe_ratio", "fk", "f_k"),
    "pb": ("pb", "pb_ratio", "pddd", "pd_dd"),
    "ev_ebitda": ("ev_ebitda", "fd_favok", "fdebitda"),
    "ev_sales": ("ev_sales", "fd_satis", "fd_sales"),
    "dividend_yield": ("dividend_yield", "temettu_verimi", "div_yield"),
    "roe": ("roe", "ozsermaye_karliligi", "return_on_equity"),
    "roa": ("roa", "aktif_karliligi", "return_on_assets"),
    "net_margin": ("net_margin", "net_kar_marji", "net_profit_margin"),
    "ebitda_margin": ("ebitda_margin", "favok_marji", "ebitda_margin_pct"),
}

# Ordered aliases: the first clean match wins. Labels are normalized before use.
STATEMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": ("hasilat", "satis gelirleri", "net satislar", "revenue", "sales revenue"),
    "gross_profit": ("brut kar zarar", "brut kar", "gross profit loss", "gross profit"),
    "operating_profit": ("esas faaliyet kari zarari", "faaliyet kari zarari", "operating profit loss", "operating profit"),
    "net_income": ("donem kari zarari", "net donem kari", "profit loss", "net income"),
    "net_income_parent": ("ana ortaklik paylari", "owners of parent", "equity holders of parent"),
    "cfo": ("isletme faaliyetlerinden nakit akislari", "isletme faaliyetlerinden elde edilen nakit akislari", "net cash flows from operating activities", "cash flows from operating activities"),
    "capex": ("maddi ve maddi olmayan duran varliklarin alimindan kaynaklanan nakit cikislari", "maddi duran varlik alimlari", "purchase of property plant and equipment", "capital expenditures"),
    "cash": ("nakit ve nakit benzerleri", "cash and cash equivalents"),
    "assets": ("toplam varliklar", "varliklar toplam", "total assets"),
    "current_assets": ("donen varliklar", "current assets"),
    "equity": ("ozkaynaklar", "toplam ozkaynaklar", "total equity"),
    "current_liabilities": ("kisa vadeli yukumlulukler", "kisa vadeli borclar", "current liabilities"),
    "inventory": ("stoklar", "inventories"),
    "receivables": ("ticari alacaklar", "trade receivables"),
    "payables": ("ticari borclar", "trade payables"),
    "short_debt": ("kisa vadeli borclanmalar", "short term borrowings", "short-term borrowings"),
    "current_long_debt": ("uzun vadeli borclanmalarin kisa vadeli kisimlari", "current portion of long term borrowings", "current portion of long-term borrowings"),
    "long_debt": ("uzun vadeli borclanmalar", "long term borrowings", "long-term borrowings"),
    # Bank-oriented labels.
    "net_interest_income": ("net faiz geliri", "net interest income"),
    "loans": ("krediler", "loans and receivables", "loans"),
}


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _finite(v: Any) -> float | None:
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def normalize_peer_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
    """Map changing screener column names onto the report contract."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(PEER_ALIASES))
    out = frame.copy()
    normalized = {_norm(c): c for c in out.columns}
    rename: dict[str, str] = {}
    for canonical, aliases in PEER_ALIASES.items():
        for alias in aliases:
            source = normalized.get(_norm(alias))
            if source is not None:
                rename[source] = canonical
                break
    out = out.rename(columns=rename)
    if "symbol" not in out.columns:
        raise ValueError("borsapy screener output does not contain a recognizable symbol column")
    out["symbol"] = out["symbol"].astype(str).str.upper().str.replace(".IS", "", regex=False).str.replace(".E", "", regex=False)
    if "sector" not in out.columns:
        out["sector"] = "GENERIC"
    keep = [c for c in PEER_ALIASES if c in out.columns]
    return out[keep].drop_duplicates("symbol", keep="first").reset_index(drop=True)


def refresh_peer_snapshot(output_dir: str) -> dict[str, Any]:
    """Fetch the broad BIST peer table in one bulk screener call."""
    import borsapy as bp

    raw = bp.Screener().run()
    frame = normalize_peer_snapshot(raw)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "latest.csv"
    frame.to_csv(path, index=False)
    manifest = {
        "provider": "borsapy/IsYatirim Screener",
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "peer_scope": "BIST bulk screener",
        "detail_policy": "8-quarter statements are fetched only for current technical candidates",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _statement_row(frame: pd.DataFrame, key: str) -> pd.Series | None:
    if frame is None or frame.empty or key not in STATEMENT_ALIASES:
        return None
    labels = [(_norm(idx), idx) for idx in frame.index]
    # Exact normalized match first, then substring match. Prefer shortest matching label
    # to avoid accidentally choosing a verbose subtotal with a similar name.
    for alias in STATEMENT_ALIASES[key]:
        a = _norm(alias)
        exact = [idx for label, idx in labels if label == a]
        if exact:
            return pd.to_numeric(frame.loc[exact[0]], errors="coerce")
    candidates: list[tuple[int, Any]] = []
    for alias in STATEMENT_ALIASES[key]:
        a = _norm(alias)
        for label, idx in labels:
            if a and a in label:
                candidates.append((len(label), idx))
    if not candidates:
        return None
    _, idx = sorted(candidates, key=lambda x: x[0])[0]
    return pd.to_numeric(frame.loc[idx], errors="coerce")


def _chronological(series: pd.Series | None) -> tuple[list[str], list[float | None]]:
    if series is None or series.empty:
        return [], []
    def key(col: Any) -> tuple[int, int]:
        m = re.match(r"(\d{4})(?:Q([1-4]))?$", str(col))
        return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    cols = sorted(series.index, key=key)
    return [str(c) for c in cols], [_finite(series[c]) for c in cols]


def _series_dict(frame: pd.DataFrame, key: str) -> dict[str, Any]:
    periods, values = _chronological(_statement_row(frame, key))
    return {"periods": periods, "values": values}


def _latest(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return vals[-1] if vals else None


def _yoy(values: list[float | None]) -> float | None:
    if len(values) < 5 or values[-1] is None or values[-5] in (None, 0):
        return None
    return (values[-1] / values[-5] - 1.0) * 100.0


def _safe_div(a: float | None, b: float | None, scale: float = 1.0) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b * scale


def _add_series(a: dict[str, Any], b: dict[str, Any], c: dict[str, Any] | None = None) -> dict[str, Any]:
    maps = []
    for item in (a, b, c):
        if item:
            maps.append(dict(zip(item.get("periods", []), item.get("values", []))))
    periods = sorted(set().union(*(m.keys() for m in maps))) if maps else []
    values = []
    for p in periods:
        vals = [m.get(p) for m in maps]
        if all(v is None for v in vals):
            values.append(None)
        else:
            values.append(sum(v or 0.0 for v in vals))
    return {"periods": periods, "values": values}


def _ratio_series(num: dict[str, Any], den: dict[str, Any], scale: float = 100.0) -> dict[str, Any]:
    n = dict(zip(num.get("periods", []), num.get("values", [])))
    d = dict(zip(den.get("periods", []), den.get("values", [])))
    periods = sorted(set(n) & set(d))
    return {"periods": periods, "values": [_safe_div(n[p], d[p], scale) for p in periods]}


def build_trend_payload(symbol: str, sector: str, balance: pd.DataFrame, income: pd.DataFrame, cashflow: pd.DataFrame | None, financial_group: str) -> dict[str, Any]:
    """Convert raw 8-quarter statements into auditable trend metrics."""
    metrics: dict[str, dict[str, Any]] = {}
    for key, frame in (
        ("revenue", income), ("gross_profit", income), ("operating_profit", income),
        ("net_income", income), ("net_income_parent", income), ("net_interest_income", income),
        ("cash", balance), ("assets", balance), ("current_assets", balance), ("equity", balance),
        ("current_liabilities", balance), ("inventory", balance), ("receivables", balance),
        ("payables", balance), ("short_debt", balance), ("current_long_debt", balance),
        ("long_debt", balance), ("loans", balance),
    ):
        metrics[key] = _series_dict(frame, key)
    if cashflow is not None and not cashflow.empty:
        metrics["cfo"] = _series_dict(cashflow, "cfo")
        metrics["capex"] = _series_dict(cashflow, "capex")
    else:
        metrics["cfo"] = {"periods": [], "values": []}
        metrics["capex"] = {"periods": [], "values": []}

    # Prefer parent-attributable profit when available, otherwise total period profit.
    net = metrics["net_income_parent"] if any(v is not None for v in metrics["net_income_parent"]["values"]) else metrics["net_income"]
    metrics["selected_net_income"] = net
    metrics["gross_margin"] = _ratio_series(metrics["gross_profit"], metrics["revenue"])
    metrics["operating_margin"] = _ratio_series(metrics["operating_profit"], metrics["revenue"])
    metrics["net_margin"] = _ratio_series(net, metrics["revenue"])
    debt = _add_series(metrics["short_debt"], metrics["current_long_debt"], metrics["long_debt"])
    metrics["gross_debt"] = debt
    # Net debt and debt/equity are aligned by period.
    debt_map = dict(zip(debt["periods"], debt["values"]))
    cash_map = dict(zip(metrics["cash"]["periods"], metrics["cash"]["values"]))
    equity_map = dict(zip(metrics["equity"]["periods"], metrics["equity"]["values"]))
    periods = sorted(set(debt_map) & set(cash_map))
    net_debt_vals = [(debt_map[p] - cash_map[p]) if debt_map[p] is not None and cash_map[p] is not None else None for p in periods]
    metrics["net_debt"] = {"periods": periods, "values": net_debt_vals}
    nd_map = dict(zip(periods, net_debt_vals))
    dte_periods = sorted(set(nd_map) & set(equity_map))
    metrics["net_debt_equity"] = {"periods": dte_periods, "values": [_safe_div(nd_map[p], equity_map[p], 100.0) for p in dte_periods]}
    metrics["cfo_net_income"] = _ratio_series(metrics["cfo"], net, 1.0)

    revenue_yoy = _yoy(metrics["revenue"]["values"])
    op_yoy = _yoy(metrics["operating_profit"]["values"])
    net_yoy = _yoy(net["values"])
    latest_nm = _latest(metrics["net_margin"]["values"])
    prev_nm = metrics["net_margin"]["values"][-5] if len(metrics["net_margin"]["values"]) >= 5 else None
    margin_change = (latest_nm - prev_nm) if latest_nm is not None and prev_nm is not None else None
    latest_nde = _latest(metrics["net_debt_equity"]["values"])
    prev_nde = metrics["net_debt_equity"]["values"][-5] if len(metrics["net_debt_equity"]["values"]) >= 5 else None
    leverage_change = (latest_nde - prev_nde) if latest_nde is not None and prev_nde is not None else None
    cfo_ni = _latest(metrics["cfo_net_income"]["values"])

    components: list[tuple[str, float, bool | None]] = [
        ("revenue_growth", 15.0, None if revenue_yoy is None else revenue_yoy > 0),
        ("operating_growth", 15.0, None if op_yoy is None else op_yoy > 0),
        ("earnings_growth", 20.0, None if net_yoy is None else net_yoy > 0),
        ("margin_direction", 15.0, None if margin_change is None else margin_change >= 0),
        ("leverage_direction", 15.0, None if leverage_change is None else leverage_change <= 0),
        ("cash_conversion", 20.0, None if cfo_ni is None else cfo_ni >= 0.80),
    ]
    available = sum(w for _, w, state in components if state is not None)
    earned = sum(w for _, w, state in components if state is True)
    score = round(earned / available * 100.0, 1) if available else None
    coverage = round(available, 1)

    strengths: list[str] = []
    risks: list[str] = []
    if revenue_yoy is not None: (strengths if revenue_yoy > 0 else risks).append(f"Satış/gelir yıllık değişimi %{revenue_yoy:+.1f}")
    if net_yoy is not None: (strengths if net_yoy > 0 else risks).append(f"Net kâr yıllık değişimi %{net_yoy:+.1f}")
    if margin_change is not None: (strengths if margin_change >= 0 else risks).append(f"Net marj yıllık değişimi {margin_change:+.1f} puan")
    if leverage_change is not None: (strengths if leverage_change <= 0 else risks).append(f"Net borç/özkaynak yıllık değişimi {leverage_change:+.1f} puan")
    if cfo_ni is not None: (strengths if cfo_ni >= 0.80 else risks).append(f"Faaliyet nakdi/net kâr {cfo_ni:.2f}x")
    if financial_group == "UFRS" and not strengths and not risks:
        risks.append("Banka formatında nakit akış verisi sınırlı; trend puanı yalnız mevcut UFRS kalemlerinden hesaplandı")

    if score is None:
        label = "VERI YETERSIZ"
    elif score >= 75:
        label = "GUCLU IYILESME"
    elif score >= 55:
        label = "OLUMLU / KARISIK"
    elif score >= 35:
        label = "KARISIK / ZAYIF"
    else:
        label = "ZAYIFLAMA"

    return {
        "symbol": symbol.upper(),
        "sector": sector,
        "financial_group": financial_group,
        "quarters_requested": 8,
        "trend_score": score,
        "trend_label": label,
        "trend_coverage": coverage,
        "snapshot": {
            "revenue_yoy": revenue_yoy, "operating_profit_yoy": op_yoy, "net_income_yoy": net_yoy,
            "net_margin": latest_nm, "net_margin_yoy_change_pp": margin_change,
            "net_debt_equity": latest_nde, "net_debt_equity_yoy_change_pp": leverage_change,
            "cfo_net_income": cfo_ni,
        },
        "strengths": strengths[:4],
        "risks": risks[:4],
        "metrics": metrics,
    }


def _peer_sector(peer: pd.DataFrame, symbol: str) -> str:
    if peer is None or peer.empty or "symbol" not in peer.columns:
        return "GENERIC"
    rows = peer[peer["symbol"].astype(str).str.upper() == symbol.upper()]
    return str(rows.iloc[0].get("sector") or "GENERIC") if not rows.empty else "GENERIC"


def _financial_group(sector: str) -> str:
    key = _norm(sector)
    return "UFRS" if "bank" in key or "banka" in key or "katilim finans" in key else "XI_29"


def fetch_candidate_history(symbol: str, sector: str) -> dict[str, Any]:
    import borsapy as bp

    group = _financial_group(sector)
    ticker = bp.Ticker(symbol)
    balance = ticker.get_balance_sheet(quarterly=True, financial_group=group, last_n=8)
    income = ticker.get_income_stmt(quarterly=True, financial_group=group, last_n=8)
    cashflow: pd.DataFrame | None = None
    if group != "UFRS":
        try:
            cashflow = ticker.get_cashflow(quarterly=True, financial_group=group, last_n=8)
        except Exception:
            cashflow = None
    return build_trend_payload(symbol, sector, balance, income, cashflow, group)


def refresh_candidate_histories(visual_json: str, peer_csv: str | None, output_dir: str) -> dict[str, Any]:
    payload = json.loads(Path(visual_json).read_text(encoding="utf-8"))
    peer = pd.read_csv(peer_csv) if peer_csv and Path(peer_csv).exists() else pd.DataFrame()
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for candidate in payload.get("candidates", []):
        symbol = str(candidate.get("symbol", "")).upper()
        if not symbol:
            continue
        sector = _peer_sector(peer, symbol)
        try:
            item = fetch_candidate_history(symbol, sector)
            (out / f"{symbol}.json").write_text(json.dumps(item, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            results.append({"symbol": symbol, "trend_score": item.get("trend_score"), "trend_label": item.get("trend_label")})
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
    manifest = {"provider": "borsapy/IsYatirim financial statements", "success": results, "errors": errors, "requested": len(payload.get("candidates", []))}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    peer = sub.add_parser("peer")
    peer.add_argument("--output-dir", default="data/fundamentals")
    cand = sub.add_parser("candidates")
    cand.add_argument("--visual-json", default="data/production-4h/visual_candidates.json")
    cand.add_argument("--peer-csv", default="data/fundamentals/latest.csv")
    cand.add_argument("--output-dir", default="data/fundamentals/quarterly")
    a = p.parse_args()
    if a.command == "peer":
        result = refresh_peer_snapshot(a.output_dir)
        print(f"Fundamental peer snapshot: {result['rows']} rows")
    else:
        result = refresh_candidate_histories(a.visual_json, a.peer_csv, a.output_dir)
        print(f"Candidate fundamentals: {len(result['success'])}/{result['requested']} success; {len(result['errors'])} error(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
