"""Build a broad BIST peer fundamental snapshot from borsapy/İş Yatırım.

Important provider behaviour: the İş Yatırım screener effectively intersects
criteria supplied in one request. Therefore every metric is queried separately
and left-merged onto a baseline BIST universe. A company with no P/E must not be
removed from the peer universe merely because another metric exists.

Sector/industry metadata is requested in one bulk TradingView scanner query.
When sector metadata is unavailable the row remains GENERIC and downstream
reports must describe the comparison scope honestly as BIST-wide.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

CRITERIA = {
    "market_cap": "8",
    "pe": "28",
    "ev_ebitda": "29",
    "pb": "30",
    "ev_sales": "31",
    "dividend_yield": "33",
    "roe": "422",
    "roa": "423",
    "net_margin": "119",
    "ebitda_margin": "120",
}

BOUNDS = {
    "market_cap": (0, 5_000_000),
    "pe": (-1000, 10000),
    "ev_ebitda": (-100, 1000),
    "pb": (-100, 1000),
    "ev_sales": (-100, 1000),
    "dividend_yield": (0, 100),
    "roe": (-200, 500),
    "roa": (-200, 500),
    "net_margin": (-200, 500),
    "ebitda_margin": (-200, 500),
}


def _clean_symbol(value: object) -> str:
    text = str(value or "").upper().strip()
    for prefix in ("BIST:", "BIST-"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.replace(".IS", "").replace(".E", "").strip()


def fetch_universe() -> pd.DataFrame:
    import borsapy as bp

    raw = bp.Screener().run()
    if raw is None or raw.empty or "symbol" not in raw.columns:
        raise RuntimeError("İş Yatırım screener returned no baseline BIST universe")
    out = raw[[c for c in ("symbol", "name") if c in raw.columns]].copy()
    out["symbol"] = out["symbol"].map(_clean_symbol)
    out = out[out["symbol"].str.len() > 0]
    return out.drop_duplicates("symbol", keep="first").reset_index(drop=True)


def fetch_metric(name: str) -> pd.DataFrame:
    import borsapy as bp

    lo, hi = BOUNDS[name]
    raw = bp.Screener().add_filter(name, min=lo, max=hi, required=False).run()
    value_col = f"criteria_{CRITERIA[name]}"
    if raw is None or raw.empty or "symbol" not in raw.columns or value_col not in raw.columns:
        return pd.DataFrame(columns=["symbol", name])
    out = raw[["symbol", value_col]].rename(columns={value_col: name}).copy()
    out["symbol"] = out["symbol"].map(_clean_symbol)
    out[name] = pd.to_numeric(out[name], errors="coerce")
    return out.drop_duplicates("symbol", keep="first")


def fetch_metrics() -> tuple[pd.DataFrame, dict[str, int]]:
    frame = fetch_universe()
    response_rows: dict[str, int] = {}
    for name in CRITERIA:
        metric = fetch_metric(name)
        response_rows[name] = int(len(metric))
        frame = frame.merge(metric, on="symbol", how="left")
    return frame, response_rows


def fetch_sector_metadata() -> pd.DataFrame:
    """Best-effort bulk sector mapping via TradingView's Turkey scanner."""
    try:
        from tradingview_screener import Query

        query = (
            Query()
            .set_markets("turkey")
            .select("name", "sector", "industry")
            .limit(2000)
        )
        _, raw = query.get_scanner_data()
    except Exception:
        return pd.DataFrame(columns=["symbol", "sector", "industry"])
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["symbol", "sector", "industry"])

    out = raw.copy()
    symbol_col = next((c for c in ("ticker", "name", "symbol") if c in out.columns), None)
    if symbol_col is None:
        return pd.DataFrame(columns=["symbol", "sector", "industry"])
    out["symbol"] = out[symbol_col].map(_clean_symbol)
    if "sector" not in out.columns:
        out["sector"] = None
    if "industry" not in out.columns:
        out["industry"] = None
    return out[["symbol", "sector", "industry"]].drop_duplicates("symbol", keep="first")


def build_snapshot() -> tuple[pd.DataFrame, dict[str, int]]:
    frame, response_rows = fetch_metrics()
    sectors = fetch_sector_metadata()
    if not sectors.empty:
        frame = frame.merge(sectors, on="symbol", how="left")
    else:
        frame["sector"] = None
        frame["industry"] = None
    frame["sector"] = frame["sector"].fillna("GENERIC").replace("", "GENERIC")
    frame["industry"] = frame["industry"].fillna("")
    cols = ["symbol", "sector", "industry"] + [c for c in CRITERIA if c in frame.columns]
    return frame[cols], response_rows


def validate_snapshot(frame: pd.DataFrame) -> dict[str, Any]:
    metric_cols = [c for c in CRITERIA if c in frame.columns]
    populated = {c: int(pd.to_numeric(frame[c], errors="coerce").notna().sum()) for c in metric_cols}
    if len(frame) < 400:
        raise RuntimeError(f"Fundamental peer universe unexpectedly small: {len(frame)}")
    if not metric_cols or max(populated.values(), default=0) < 100:
        raise RuntimeError(f"Fundamental snapshot has insufficient populated metrics: {populated}")
    return {
        "metric_non_null": populated,
        "sector_non_generic": int((frame["sector"] != "GENERIC").sum()),
        "sector_unique": int(frame.loc[frame["sector"] != "GENERIC", "sector"].nunique()),
    }


def refresh(output_dir: str) -> dict[str, object]:
    frame, response_rows = build_snapshot()
    quality = validate_snapshot(frame)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "latest.csv", index=False)
    manifest: dict[str, object] = {
        "provider": "borsapy/IsYatirim Screener + TradingView sector metadata",
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "metric_response_rows": response_rows,
        **quality,
        "peer_scope": "sector-relative where metadata is available; BIST-wide fallback otherwise",
        "missing_policy": "missing company metrics remain NaN; rows are never removed by another metric",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", default="data/fundamentals")
    a = p.parse_args()
    result = refresh(a.output_dir)
    print(f"Fundamental peer snapshot: {result['rows']} rows")
    print("Columns:", ", ".join(result["columns"]))
    print("Metric responses:", result["metric_response_rows"])
    print("Non-null metrics:", result["metric_non_null"])
    print("Sector mapped:", result["sector_non_generic"], "unique:", result["sector_unique"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
