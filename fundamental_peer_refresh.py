"""Build a full-BIST peer fundamental snapshot from borsapy/İş Yatırım.

The screener API only returns criteria explicitly requested. This adapter asks
for a broad non-required range for each metric, maps İş Yatırım criterion IDs
back to canonical names, and resolves sector membership separately.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

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

# Intentionally broad bounds. required=False means missing values are not
# supposed to disqualify a stock; they remain missing in the output.
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
    return str(value or "").upper().replace(".IS", "").replace(".E", "").strip()


def fetch_metrics() -> pd.DataFrame:
    import borsapy as bp

    screener = bp.Screener()
    for name, (lo, hi) in BOUNDS.items():
        screener.add_filter(name, min=lo, max=hi, required=False)
    raw = screener.run()
    if raw is None or raw.empty:
        raise RuntimeError("İş Yatırım screener returned no metric rows")

    rename = {f"criteria_{criterion_id}": name for name, criterion_id in CRITERIA.items()}
    frame = raw.rename(columns=rename).copy()
    if "symbol" not in frame.columns:
        raise RuntimeError(f"Screener output has no symbol column: {list(frame.columns)}")
    frame["symbol"] = frame["symbol"].map(_clean_symbol)
    keep = ["symbol", "name"] + [name for name in CRITERIA if name in frame.columns]
    return frame[keep].drop_duplicates("symbol", keep="first").reset_index(drop=True)


def fetch_sector_membership() -> dict[str, str]:
    """Resolve each symbol's İş Yatırım sector without per-company calls."""
    import borsapy as bp

    mapping: dict[str, str] = {}
    for sector in bp.sectors():
        try:
            rows = bp.Screener().set_sector(sector).run()
        except Exception:
            continue
        if rows is None or rows.empty or "symbol" not in rows.columns:
            continue
        for symbol in rows["symbol"]:
            key = _clean_symbol(symbol)
            # First assignment wins if İş Yatırım hierarchy contains overlapping
            # parent/child groups; this keeps output deterministic.
            mapping.setdefault(key, str(sector))
    return mapping


def build_snapshot() -> pd.DataFrame:
    frame = fetch_metrics()
    sectors = fetch_sector_membership()
    frame["sector"] = frame["symbol"].map(sectors).fillna("GENERIC")
    cols = ["symbol", "sector"] + [c for c in CRITERIA if c in frame.columns]
    return frame[cols]


def refresh(output_dir: str) -> dict[str, object]:
    frame = build_snapshot()
    metric_cols = [c for c in CRITERIA if c in frame.columns]
    populated = {c: int(pd.to_numeric(frame[c], errors="coerce").notna().sum()) for c in metric_cols}
    if not metric_cols or max(populated.values(), default=0) == 0:
        raise RuntimeError(f"Fundamental snapshot contains no populated metrics: {list(frame.columns)}")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "latest.csv", index=False)
    manifest = {
        "provider": "borsapy/IsYatirim Screener",
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "metric_non_null": populated,
        "sector_non_generic": int((frame["sector"] != "GENERIC").sum()),
        "peer_scope": "BIST bulk screener with İş Yatırım sector membership",
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
    print("Non-null:", result["metric_non_null"])
    print("Sector mapped:", result["sector_non_generic"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
