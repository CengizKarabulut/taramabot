"""Live/shadow evaluator backed by the parity-safe locked event semantics.

`signals.py` still provides rich diagnostic components and indicator values.
The actual `triggered` flag, however, is taken from the vectorized locked-event
engine in `history.py`. This prevents a diagnostic decomposition from subtly
changing the research-approved False->True event semantics.
"""
from __future__ import annotations

from dataclasses import replace

import pandas as pd

from .history import event_series
from .registry import ProductionRecord, ProductionRegistry, get_production_registry
from .signals import SignalEvidence, evaluate_record as _diagnostic_record


def evaluate_record(df: pd.DataFrame, record: ProductionRecord) -> SignalEvidence:
    diagnostic = _diagnostic_record(df, record)
    locked = bool(event_series(df, record).iloc[-1])
    components = dict(diagnostic.components)
    components["locked_entry_event"] = locked
    return replace(diagnostic, triggered=locked, components=components)


def evaluate_timeframe(
    df: pd.DataFrame,
    timeframe: str,
    *,
    registry: ProductionRegistry | None = None,
    only_triggered: bool = True,
) -> list[SignalEvidence]:
    selected = registry or get_production_registry()
    evidence = [evaluate_record(df, record) for record in selected.production_records(timeframe=timeframe)]
    return [item for item in evidence if item.triggered] if only_triggered else evidence
