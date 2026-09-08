"""Compare frozen deneme research entries with taramabot production entries.

The comparison uses the same historical SQLite artifacts as the research jobs.
Only completed-bar entry timestamps are compared; exit management is outside
this parity gate because the registry integration first needs exact signal
identity.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from market_data_store import MarketDataStore
from production_scanners.history import event_series
from production_scanners.registry import ProductionRecord, ProductionRegistry


REFERENCE_VARIANTS: dict[tuple[str, str], str] = {
    ("StochRSI / Momentum / Hacim", "1D"): "B_stoch_trigger",
    ("StochRSI / Momentum / Hacim", "1W"): "C_momentum_trigger",
    ("BB & SMA", "2H"): "D_slope_volume",
    ("BB & SMA", "4H"): "C_volume",
    ("BB & SMA", "1D"): "D_slope_volume",
    ("BB & SMA", "1W"): "C_volume",
    ("RSI & MACD - RVOL", "4H"): "r55_70__A_cross",
    ("RSI & MACD - RVOL", "1D"): "r50_65__B_hist_rise2",
    ("RSI & MACD - RVOL", "1W"): "r50_65__A_cross",
    ("7 - BB Daralması", "4H"): "relative20__cross",
    ("7 - BB Daralması", "1D"): "abs10__hist_rise2",
    ("7 - BB Daralması", "1W"): "release20__hist_rise2",
    ("11 - Stoc.RSI & RSI & BB & MACD", "4H"): "B_bb_reclaim_with_momentum_confirm",
    ("11 - Stoc.RSI & RSI & BB & MACD", "1D"): "B_bb_reclaim_with_momentum_confirm",
    ("11 - Stoc.RSI & RSI & BB & MACD", "1W"): "B_bb_reclaim_with_momentum_confirm",
    ("13 - MACD YenidenHareket", "4H"): "C_positive_hist_reacceleration",
    ("13 - MACD YenidenHareket", "1D"): "C_positive_hist_reacceleration",
    ("13 - MACD YenidenHareket", "1W"): "A_original_positive_macd_cross",
    ("14 - MACD DipDönüşü", "4H"): "C_strong_rvol_1_50",
    ("14 - MACD DipDönüşü", "1D"): "A_no_rvol",
    ("14 - MACD DipDönüşü", "1W"): "A_no_rvol",
}


class ResearchReference:
    def __init__(self, deneme_root: Path) -> None:
        self.root = deneme_root.resolve()
        experiment_dirs = [
            "ne_ararsan_var",
            "stoch_rsi_momentum_volume",
            "bb_sma",
            "rsi_macd_rvol",
            "bb_squeeze",
            "tavan_tarama",
            "bulut_keser",
            "stoc_rsi_rsi_bb_macd",
            "macd_yeniden_hareket",
            "macd_dip_donusu",
        ]
        # Append rather than prepend so taramabot's already-imported
        # market_data_store remains authoritative for the shared SQLite API.
        for name in experiment_dirs:
            path = str(self.root / "experiments" / name)
            if path not in sys.path:
                sys.path.append(path)

        self.ne = importlib.import_module("ne_ararsan_var_histogram_wf")
        self.stoch = importlib.import_module("stoch_momentum_volume_wf")
        self.bb_sma = importlib.import_module("bb_sma_wf")
        self.rsi_macd = importlib.import_module("rsi_macd_rvol_wf")
        self.bb_sq = importlib.import_module("bb_squeeze_entry_stage")
        self.tavan = importlib.import_module("tavan_entry_stage")
        self.bulut = importlib.import_module("bulut_keser_filter_stage")
        self.scan11 = importlib.import_module("scan11_entry_ab")
        self.scan13 = importlib.import_module("scan13_entry_abc")
        self.scan14 = importlib.import_module("scan14_entry_abc")

    @staticmethod
    def _by_name(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
        for row in rows:
            if row.get("name") == name:
                return row
        raise KeyError(f"Research entry variant not found: {name}")

    def warmup(self, family: str) -> int:
        module = {
            "NE ARARSAN VAR": self.ne,
            "StochRSI / Momentum / Hacim": self.stoch,
            "BB & SMA": self.bb_sma,
            "RSI & MACD - RVOL": self.rsi_macd,
            "7 - BB Daralması": self.bb_sq,
            "8 - TavanTarama": self.tavan,
            "9 - BulutKeser": self.bulut,
            "11 - Stoc.RSI & RSI & BB & MACD": self.scan11,
            "13 - MACD YenidenHareket": self.scan13,
            "14 - MACD DipDönüşü": self.scan14,
        }[family]
        return int(getattr(module, "WARMUP", 260))

    def events(self, frame: pd.DataFrame, record: ProductionRecord) -> pd.Series:
        family, tf = record.family, record.timeframe

        if family == "NE ARARSAN VAR":
            data = self.ne.add_histogram(self.ne.build_indicator_frame(frame))
            return self.ne.build_entry_events(data, {"name": "hist_rise_any_nocci", "sign": "any", "cci": False})

        if family == "StochRSI / Momentum / Hacim":
            data = self.stoch.indicators(frame)
            return self.stoch.entry_events(data, REFERENCE_VARIANTS[(family, tf)])

        if family == "BB & SMA":
            data = self.bb_sma.indicators(frame)
            return self.bb_sma.entry_events(data, REFERENCE_VARIANTS[(family, tf)])

        if family == "RSI & MACD - RVOL":
            data = self.rsi_macd.indicators(frame)
            variant = self._by_name(self.rsi_macd.entry_variants(), REFERENCE_VARIANTS[(family, tf)])
            return self.rsi_macd.entry_events(data, variant)

        if family == "7 - BB Daralması":
            data = self.bb_sq.indicators(frame)
            variant = self._by_name(self.bb_sq.entry_variants(), REFERENCE_VARIANTS[(family, tf)])
            return self.bb_sq.entry_events(data, variant)

        if family == "8 - TavanTarama":
            data = self.tavan.indicators(frame)
            wanted = {
                "1D": dict(dmi_mode="orig_pdi_cross_adx", rsi_low=50, rsi_high=70, stoch_mode="cross", rvol=1.5),
                "1W": dict(dmi_mode="orig_pdi_cross_adx", rsi_low=40, rsi_high=70, stoch_mode="confirm", rvol=1.5),
            }[tf]
            variant = next(
                row for row in self.tavan.entry_variants()
                if all(row.get(k) == v for k, v in wanted.items())
            )
            return self.tavan.entry_events(data, variant)

        if family == "9 - BulutKeser":
            data = self.bulut.indicators(frame)
            wanted = {
                "4H": dict(cloud="inside", adx_mode="gt20", bb_mode="inside", rvol=1.2),
                "1D": dict(cloud="below", adx_mode="20_35", bb_mode="positive_half", rvol=1.5),
                "1W": dict(cloud="above", adx_mode="20_35", bb_mode="none", rvol=1.5),
            }[tf]
            variant = next(
                row for row in self.bulut.variants(tf)
                if all(row.get(k) == v for k, v in wanted.items())
            )
            return self.bulut.entry_events(data, variant)

        if family == "11 - Stoc.RSI & RSI & BB & MACD":
            data = self.scan11.indicators(frame)
            variant = self._by_name(self.scan11.entry_variants(), REFERENCE_VARIANTS[(family, tf)])
            return self.scan11.entry_events(data, variant)

        if family == "13 - MACD YenidenHareket":
            data = self.scan13.indicators(frame)
            variant = self._by_name(self.scan13.entry_variants(), REFERENCE_VARIANTS[(family, tf)])
            return self.scan13.entry_events(data, variant)

        if family == "14 - MACD DipDönüşü":
            data = self.scan14.indicators(frame)
            variant = self._by_name(self.scan14.entry_variants(), REFERENCE_VARIANTS[(family, tf)])
            return self.scan14.entry_events(data, variant)

        raise KeyError(f"No deneme research adapter for {family}/{tf}")


def _timestamps(series: pd.Series, eligible: pd.Series) -> set[pd.Timestamp]:
    values = series.reindex(eligible.index).fillna(False).astype(bool) & eligible
    return {pd.Timestamp(x) for x in values.index[values.to_numpy(bool)]}


def compare_period(db: str, period: str, deneme_root: str, sample_limit: int = 30) -> dict[str, Any]:
    registry = ProductionRegistry()
    reference = ResearchReference(Path(deneme_root))
    records = registry.production_records(timeframe=period)
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        rows[record.key] = {
            "family": record.family,
            "timeframe": record.timeframe,
            "tier": record.tier,
            "quality_score": record.quality_score,
            "symbols_compared": 0,
            "bars_compared": 0,
            "research_signals": 0,
            "production_signals": 0,
            "true_positive": 0,
            "false_positive": 0,
            "false_negative": 0,
            "mismatch_samples": [],
        }

    with MarketDataStore(db, read_only=True) as store:
        symbols = store.list_symbols("BIST", period)
        for i, symbol in enumerate(symbols, 1):
            frame = store.load_dataframe(symbol, "BIST", period, limit=0)
            if frame is None or frame.empty:
                continue
            for record in records:
                warmup = reference.warmup(record.family)
                if len(frame) <= warmup + 1:
                    continue
                eligible = pd.Series(False, index=frame.index)
                eligible.iloc[warmup:-1] = True
                research = reference.events(frame, record).reindex(frame.index).fillna(False).astype(bool)
                production = event_series(frame, record).reindex(frame.index).fillna(False).astype(bool)
                rset = _timestamps(research, eligible)
                pset = _timestamps(production, eligible)
                tp = rset & pset
                fp = pset - rset
                fn = rset - pset
                row = rows[record.key]
                row["symbols_compared"] += 1
                row["bars_compared"] += int(eligible.sum())
                row["research_signals"] += len(rset)
                row["production_signals"] += len(pset)
                row["true_positive"] += len(tp)
                row["false_positive"] += len(fp)
                row["false_negative"] += len(fn)
                samples = row["mismatch_samples"]
                room = max(0, sample_limit - len(samples))
                if room:
                    combined = [(x, "production_only") for x in sorted(fp)] + [(x, "research_only") for x in sorted(fn)]
                    for ts, kind in combined[:room]:
                        samples.append({"symbol": symbol, "time": ts.isoformat(), "kind": kind})
            if i % 100 == 0 or i == len(symbols):
                print(f"[{period}] {i}/{len(symbols)} symbols compared", flush=True)

    model_rows = []
    for record in records:
        row = rows[record.key]
        row["exact_match"] = row["false_positive"] == 0 and row["false_negative"] == 0
        denominator = row["true_positive"] + row["false_positive"] + row["false_negative"]
        row["event_agreement_pct"] = 100.0 if denominator == 0 else round(100.0 * row["true_positive"] / denominator, 6)
        model_rows.append(row)

    passed = sum(1 for row in model_rows if row["exact_match"])
    return {
        "version": "scanner-production-parity-v1",
        "period": period,
        "database": str(db),
        "registry_version": registry.version,
        "models": len(model_rows),
        "models_exact_match": passed,
        "status": "PASS" if passed == len(model_rows) else "FAIL",
        "rows": model_rows,
    }


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        f"# Scanner Production Parity v1 — {result['period']}",
        "",
        f"**Status:** {result['status']}",
        f"**Exact models:** {result['models_exact_match']}/{result['models']}",
        "",
        "| Family | Tier | Research | Production | FP | FN | Agreement | Result |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['family']} | {row['tier']} | {row['research_signals']} | {row['production_signals']} | "
            f"{row['false_positive']} | {row['false_negative']} | {row['event_agreement_pct']:.3f}% | "
            f"{'PASS' if row['exact_match'] else 'FAIL'} |"
        )
    failed = [row for row in result["rows"] if not row["exact_match"]]
    if failed:
        lines.extend(["", "## Mismatch samples", ""])
        for row in failed:
            lines.append(f"### {row['family']} / {row['timeframe']}")
            for sample in row["mismatch_samples"]:
                lines.append(f"- `{sample['symbol']}` · `{sample['time']}` · {sample['kind']}")
            lines.append("")
    lines.extend([
        "",
        "Parity compares completed-bar entry event timestamps after each research module's own warm-up and excludes the final bar because historical execution requires a next-bar open.",
        "A PASS requires zero production-only and zero research-only events.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--period", choices=("2H", "4H", "1D", "1W"), required=True)
    ap.add_argument("--deneme-root", required=True)
    ap.add_argument("--output-json", required=True)
    ap.add_argument("--output-md", required=True)
    args = ap.parse_args()

    result = compare_period(args.db, args.period, args.deneme_root)
    json_path = Path(args.output_json)
    md_path = Path(args.output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(result), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("version", "period", "models", "models_exact_match", "status")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
