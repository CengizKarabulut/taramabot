"""Sector-aware fundamental factor scoring for BIST research/reporting.

This module is provider-agnostic: upstream code supplies a table of company
metrics, and this layer converts them into peer-relative 0-100 factor scores.
It is an analytical scorecard, not an investment recommendation engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MetricSpec:
    name: str
    factor: str
    higher_is_better: bool
    weight: float = 1.0


GENERIC_SPECS = (
    # Valuation
    MetricSpec("pe", "valuation", False, 1.2),
    MetricSpec("pb", "valuation", False, 0.8),
    MetricSpec("ev_ebitda", "valuation", False, 1.2),
    MetricSpec("ev_sales", "valuation", False, 0.6),
    MetricSpec("p_fcf", "valuation", False, 1.0),
    MetricSpec("peg", "valuation", False, 0.5),
    MetricSpec("dividend_yield", "valuation", True, 0.5),
    # Profitability / returns
    MetricSpec("roe", "profitability", True, 1.0),
    MetricSpec("roa", "profitability", True, 0.7),
    MetricSpec("roic", "profitability", True, 1.3),
    MetricSpec("gross_margin", "profitability", True, 0.5),
    MetricSpec("ebitda_margin", "profitability", True, 0.9),
    MetricSpec("net_margin", "profitability", True, 0.8),
    # Growth
    MetricSpec("revenue_growth_yoy", "growth", True, 0.9),
    MetricSpec("revenue_cagr_3y", "growth", True, 1.0),
    MetricSpec("ebitda_growth_yoy", "growth", True, 0.8),
    MetricSpec("net_income_growth_yoy", "growth", True, 0.7),
    MetricSpec("eps_growth_yoy", "growth", True, 0.8),
    MetricSpec("book_value_growth_yoy", "growth", True, 0.5),
    # Balance-sheet health
    MetricSpec("net_debt_ebitda", "balance_sheet", False, 1.2),
    MetricSpec("debt_equity", "balance_sheet", False, 0.8),
    MetricSpec("current_ratio", "balance_sheet", True, 0.7),
    MetricSpec("quick_ratio", "balance_sheet", True, 0.5),
    MetricSpec("interest_coverage", "balance_sheet", True, 1.0),
    MetricSpec("cash_debt", "balance_sheet", True, 0.8),
    # Cash / earnings quality
    MetricSpec("cfo_net_income", "cash_quality", True, 1.2),
    MetricSpec("fcf_margin", "cash_quality", True, 1.0),
    MetricSpec("accrual_ratio", "cash_quality", False, 0.8),
    MetricSpec("cfo_growth_yoy", "cash_quality", True, 0.6),
    # Efficiency
    MetricSpec("asset_turnover", "efficiency", True, 0.7),
    MetricSpec("inventory_days", "efficiency", False, 0.5),
    MetricSpec("receivable_days", "efficiency", False, 0.5),
    MetricSpec("cash_conversion_cycle", "efficiency", False, 0.7),
)

BANK_SPECS = (
    MetricSpec("pe", "valuation", False, 0.9),
    MetricSpec("pb", "valuation", False, 1.4),
    MetricSpec("roe", "profitability", True, 1.3),
    MetricSpec("roa", "profitability", True, 0.8),
    MetricSpec("net_interest_margin", "profitability", True, 1.0),
    MetricSpec("fee_income_growth", "growth", True, 0.6),
    MetricSpec("loan_growth", "growth", True, 0.5),
    MetricSpec("capital_adequacy", "balance_sheet", True, 1.2),
    MetricSpec("npl_ratio", "balance_sheet", False, 1.2),
    MetricSpec("coverage_ratio", "balance_sheet", True, 0.8),
    MetricSpec("cost_income", "efficiency", False, 1.0),
)

REIT_SPECS = (
    MetricSpec("p_nav", "valuation", False, 1.5),
    MetricSpec("pb", "valuation", False, 0.8),
    MetricSpec("ffo_multiple", "valuation", False, 1.2),
    MetricSpec("dividend_yield", "valuation", True, 0.5),
    MetricSpec("occupancy_rate", "profitability", True, 1.0),
    MetricSpec("rental_margin", "profitability", True, 0.8),
    MetricSpec("nav_growth_yoy", "growth", True, 1.0),
    MetricSpec("rental_income_growth_yoy", "growth", True, 0.8),
    MetricSpec("net_debt_asset", "balance_sheet", False, 1.2),
    MetricSpec("interest_coverage", "balance_sheet", True, 0.8),
    MetricSpec("cfo_net_income", "cash_quality", True, 0.8),
)

FACTOR_WEIGHTS = {
    "valuation": 0.20,
    "profitability": 0.22,
    "growth": 0.18,
    "balance_sheet": 0.18,
    "cash_quality": 0.14,
    "efficiency": 0.08,
}


def specs_for_sector(sector: str | None) -> tuple[MetricSpec, ...]:
    key = (sector or "").strip().lower()
    if any(token in key for token in ("banka", "bank", "katilim finans")):
        return BANK_SPECS
    if any(token in key for token in ("gyo", "reit", "gayrimenkul yatirim")):
        return REIT_SPECS
    return GENERIC_SPECS


def _winsorized_percentile(series: pd.Series, higher_is_better: bool) -> pd.Series:
    x = pd.to_numeric(series, errors="coerce")
    valid = x.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=series.index, dtype=float)
    if len(valid) >= 5:
        lo, hi = valid.quantile([0.05, 0.95])
        x = x.clip(lower=lo, upper=hi)
    pct = x.rank(pct=True, method="average") * 100.0
    if not higher_is_better:
        pct = 100.0 - pct
    return pct


def score_peer_group(frame: pd.DataFrame, sector_col: str = "sector") -> pd.DataFrame:
    """Score each company versus its own sector peers.

    Required: one row per company. Metric columns may be missing; missing metric
    weights are automatically redistributed within the available factor only.
    """
    if frame.empty:
        return frame.copy()
    data = frame.copy()
    if sector_col not in data.columns:
        data[sector_col] = "GENERIC"

    outputs = []
    for _, group in data.groupby(sector_col, dropna=False, sort=False):
        scored = group.copy()
        sector = str(group[sector_col].iloc[0])
        specs = specs_for_sector(sector)
        factor_metric_scores: dict[str, list[tuple[str, float]]] = {}

        for spec in specs:
            if spec.name not in group.columns:
                continue
            col = f"metric_score__{spec.name}"
            scored[col] = _winsorized_percentile(group[spec.name], spec.higher_is_better)
            factor_metric_scores.setdefault(spec.factor, []).append((col, spec.weight))

        coverage_weight = 0.0
        total_possible = sum(s.weight for s in specs)
        for factor, pairs in factor_metric_scores.items():
            numerator = pd.Series(0.0, index=scored.index)
            denominator = pd.Series(0.0, index=scored.index)
            for col, weight in pairs:
                mask = scored[col].notna()
                numerator = numerator.add(scored[col].fillna(0) * weight, fill_value=0)
                denominator = denominator.add(mask.astype(float) * weight, fill_value=0)
            scored[f"factor__{factor}"] = numerator.div(denominator.replace(0, np.nan))

        factor_num = pd.Series(0.0, index=scored.index)
        factor_den = pd.Series(0.0, index=scored.index)
        for factor, master_weight in FACTOR_WEIGHTS.items():
            col = f"factor__{factor}"
            if col not in scored:
                continue
            mask = scored[col].notna()
            factor_num += scored[col].fillna(0) * master_weight
            factor_den += mask.astype(float) * master_weight
        scored["fundamental_score"] = factor_num.div(factor_den.replace(0, np.nan))

        available_weights = pd.Series(0.0, index=scored.index)
        for spec in specs:
            if spec.name in scored.columns:
                available_weights += scored[spec.name].notna().astype(float) * spec.weight
        scored["fundamental_coverage"] = (available_weights / total_possible * 100.0).clip(0, 100)
        scored["fundamental_confidence"] = pd.cut(
            scored["fundamental_coverage"],
            bins=[-1, 40, 70, 100],
            labels=["DUSUK", "ORTA", "YUKSEK"],
        ).astype(str)
        outputs.append(scored)
    return pd.concat(outputs).sort_index()


def score_label(score: float | None) -> str:
    if score is None or not np.isfinite(score):
        return "VERI YETERSIZ"
    if score >= 80:
        return "COK GUCLU"
    if score >= 65:
        return "GUCLU"
    if score >= 50:
        return "NOTR / ORTA"
    if score >= 35:
        return "ZAYIF"
    return "COK ZAYIF"
