"""Stock-level technical fingerprint with explicit overlap penalties.

The output is an evidence score, not a predicted win probability. The v1
formula is deliberately transparent and conservative so forward data can later
be used to calibrate it instead of silently overfitting historical results.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .registry import ProductionRegistry, get_production_registry


TIER_WEIGHT = {
    "CORE": 1.00,
    "ACTIVE": 0.85,
    "SECONDARY": 0.60,
}

TIMEFRAME_WEIGHT = {
    "1W": 1.00,
    "1D": 0.88,
    "4H": 0.72,
    "2H": 0.62,
}

FEATURE_BUCKETS = {
    "trend": frozenset({"trend_ema", "trend_sma", "ichimoku", "macd"}),
    "momentum": frozenset({"rsi", "stoch_rsi", "momentum", "macd", "dmi_adx"}),
    "volume": frozenset({"volume"}),
    "volatility": frozenset({"bollinger"}),
    "structure": frozenset({"ichimoku", "dmi_adx", "trend_ema", "trend_sma"}),
}


def _as_mapping(item) -> dict:
    if hasattr(item, "as_dict"):
        return item.as_dict()
    if isinstance(item, dict):
        return dict(item)
    raise TypeError("Signal evidence must be a dict or expose as_dict()")


def _feature_set(item: dict) -> frozenset[str]:
    return frozenset(str(value) for value in item.get("features", ()) if value)


def _signal_overlap(left: dict, right: dict) -> float:
    """Heuristic dependence proxy in [0, 1].

    Same-family signals across timeframes are treated as highly correlated even
    if their exact trigger changes. Different families use feature Jaccard
    overlap. This is a penalty proxy, not an empirical correlation estimate.
    """
    if left.get("family") == right.get("family"):
        return 0.90

    left_features = _feature_set(left)
    right_features = _feature_set(right)
    if not left_features or not right_features:
        return 0.0
    union = left_features | right_features
    return len(left_features & right_features) / len(union)


def _strength(item: dict) -> float:
    tier = str(item["tier"])
    timeframe = str(item["timeframe"])
    quality = float(item["quality_score"])
    return quality * TIER_WEIGHT[tier] * TIMEFRAME_WEIGHT[timeframe]


def _band(score: float) -> str:
    if score >= 85.0:
        return "VERY_STRONG"
    if score >= 70.0:
        return "STRONG"
    if score >= 55.0:
        return "MODERATE"
    if score >= 40.0:
        return "EARLY"
    return "LOW"


def _bucket_scores(items: list[dict]) -> dict[str, float]:
    buckets: dict[str, float] = {}
    for bucket, tags in FEATURE_BUCKETS.items():
        supporting = [
            _strength(item)
            for item in items
            if _feature_set(item).intersection(tags)
        ]
        buckets[bucket] = round(max(supporting, default=0.0), 1)
    return buckets


def build_technical_fingerprint(
    evidence: Iterable,
    *,
    registry: ProductionRegistry | None = None,
) -> dict:
    """Combine triggered production signals without naively summing scores.

    Scoring v1:
    - strongest routed signal contributes its full tier/timeframe-adjusted score;
    - every extra signal contributes at most 35% of its adjusted strength;
    - its extra contribution is reduced according to maximum overlap with
      stronger evidence;
    - even highly redundant confirmation may contribute only 7% of its adjusted
      strength (35% * 20% floor), so multi-timeframe confirmation is visible but
      cannot dominate by repetition.

    The result is capped at 100 and must not be interpreted as a probability.
    """
    selected_registry = registry or get_production_registry()
    items: list[dict] = []
    for raw in evidence:
        item = _as_mapping(raw)
        if not bool(item.get("triggered", True)):
            continue
        family = str(item.get("family", ""))
        timeframe = str(item.get("timeframe", ""))
        record = selected_registry.record(family, timeframe)
        if record is None or not record.routable:
            continue

        # Registry is authoritative for tier/quality; caller-provided values
        # cannot accidentally upgrade a model.
        item["tier"] = record.tier
        item["quality_score"] = record.quality_score
        item["management_dependent"] = record.management_dependent
        item["_strength"] = _strength(item)
        items.append(item)

    items.sort(key=lambda item: (-float(item["_strength"]), str(item["family"])))

    if not items:
        return {
            "score": 0.0,
            "band": "LOW",
            "is_probability": False,
            "evidence_count": 0,
            "independent_feature_groups": 0,
            "raw_naive_sum": 0.0,
            "overlap_penalty": 0.0,
            "timeframes": [],
            "tiers": {},
            "fingerprint": {key: 0.0 for key in FEATURE_BUCKETS},
            "evidence": [],
        }

    naive_sum = sum(float(item["_strength"]) for item in items)
    adjusted_total = 0.0
    accepted: list[dict] = []
    output_evidence: list[dict] = []

    for index, item in enumerate(items):
        strength = float(item["_strength"])
        if index == 0:
            max_overlap = 0.0
            contribution = strength
        else:
            max_overlap = max(_signal_overlap(item, prior) for prior in accepted)
            novelty = 1.0 - max_overlap
            incremental_factor = 0.35 * (0.20 + 0.80 * novelty)
            contribution = strength * incremental_factor

        adjusted_total += contribution
        accepted.append(item)
        output_evidence.append(
            {
                "family": item["family"],
                "timeframe": item["timeframe"],
                "tier": item["tier"],
                "quality_score": round(float(item["quality_score"]), 1),
                "management_dependent": bool(item.get("management_dependent", False)),
                "features": sorted(_feature_set(item)),
                "adjusted_strength": round(strength, 2),
                "max_overlap_with_stronger": round(max_overlap, 3),
                "score_contribution": round(contribution, 2),
            }
        )

    score = min(100.0, adjusted_total)
    tier_counts: dict[str, int] = defaultdict(int)
    for item in items:
        tier_counts[str(item["tier"])] += 1

    feature_signatures = {_feature_set(item) for item in items if _feature_set(item)}
    return {
        "score": round(score, 1),
        "band": _band(score),
        "is_probability": False,
        "evidence_count": len(items),
        "independent_feature_groups": len(feature_signatures),
        "raw_naive_sum": round(naive_sum, 1),
        "overlap_penalty": round(max(0.0, naive_sum - adjusted_total), 1),
        "timeframes": sorted(
            {str(item["timeframe"]) for item in items},
            key=lambda value: -TIMEFRAME_WEIGHT[value],
        ),
        "tiers": dict(sorted(tier_counts.items())),
        "fingerprint": _bucket_scores(items),
        "evidence": output_evidence,
    }
