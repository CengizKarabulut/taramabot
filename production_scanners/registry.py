"""Frozen production-promotion registry for the researched scanner families.

The common registry decides *whether* a family/timeframe can be routed and at
what tier. Entry/exit implementation details live in ``signals.py`` and are
derived from the locked research specs in CengizKarabulut/deneme.

This module deliberately does not fetch another repository at runtime. The
vendored JSON is a reproducible snapshot and must be updated explicitly when a
new research registry version is promoted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TIMEFRAMES = ("15m", "30m", "45m", "1H", "2H", "4H", "1D", "1W", "1M")
ROUTABLE_TIERS = frozenset({"CORE", "ACTIVE", "SECONDARY"})
KNOWN_TIERS = frozenset(
    {"CORE", "ACTIVE", "SECONDARY", "FORWARD_WATCH", "RESEARCH", "REJECT"}
)


@dataclass(frozen=True, slots=True)
class ProductionRecord:
    family: str
    timeframe: str
    tier: str
    quality_score: float
    management_dependent: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return self.family, self.timeframe

    @property
    def routable(self) -> bool:
        return self.tier in ROUTABLE_TIERS and self.timeframe != "1M"


class ProductionRegistry:
    """Load and validate the frozen production registry snapshot."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else self.default_path()
        self.payload = json.loads(self.path.read_text(encoding="utf-8"))
        self._families = {
            str(item["family"]): dict(item)
            for item in self.payload.get("family_decisions", [])
        }
        self._records = {
            (str(item["family"]), str(item["timeframe"])): ProductionRecord(
                family=str(item["family"]),
                timeframe=str(item["timeframe"]),
                tier=str(item["tier"]),
                quality_score=float(item["quality_score"]),
                management_dependent=bool(item.get("management_dependent", False)),
            )
            for item in self.payload.get("production_records", [])
        }
        self.validate()

    @staticmethod
    def default_path() -> Path:
        return Path(__file__).resolve().parents[1] / "config" / "production_scanner_registry_v1.json"

    @property
    def version(self) -> str:
        return str(self.payload.get("version", ""))

    @property
    def source(self) -> dict:
        return dict(self.payload.get("source", {}))

    @property
    def families(self) -> tuple[str, ...]:
        return tuple(self._families)

    def decision(self, family: str, timeframe: str) -> str:
        family_item = self._families.get(family)
        if family_item is None:
            raise KeyError(f"Unknown production scanner family: {family}")
        if timeframe not in TIMEFRAMES:
            raise KeyError(f"Unknown timeframe: {timeframe}")
        return str(family_item[timeframe])

    def record(self, family: str, timeframe: str) -> ProductionRecord | None:
        return self._records.get((family, timeframe))

    def is_routable(self, family: str, timeframe: str) -> bool:
        record = self.record(family, timeframe)
        return bool(record and record.routable)

    def production_records(
        self,
        *,
        timeframe: str | None = None,
        tiers: Iterable[str] | None = None,
    ) -> tuple[ProductionRecord, ...]:
        selected_tiers = set(tiers) if tiers is not None else set(ROUTABLE_TIERS)
        records = [
            record
            for record in self._records.values()
            if record.tier in selected_tiers
            and record.routable
            and (timeframe is None or record.timeframe == timeframe)
        ]
        return tuple(
            sorted(
                records,
                key=lambda item: (
                    TIMEFRAMES.index(item.timeframe),
                    -item.quality_score,
                    item.family,
                ),
            )
        )

    def validate(self) -> None:
        if not self.version:
            raise ValueError("Production registry version is missing")
        if not self._families:
            raise ValueError("Production registry contains no family decisions")

        for family, item in self._families.items():
            for timeframe in TIMEFRAMES:
                if timeframe not in item:
                    raise ValueError(f"{family}: missing timeframe decision {timeframe}")
                tier = str(item[timeframe])
                if tier not in KNOWN_TIERS:
                    raise ValueError(f"{family}/{timeframe}: unknown tier {tier}")
                if timeframe == "1M" and tier in ROUTABLE_TIERS:
                    raise ValueError(f"{family}/1M cannot be routed as normal production")

        for key, record in self._records.items():
            decision = self.decision(*key)
            if decision != record.tier:
                raise ValueError(
                    f"{record.family}/{record.timeframe}: record tier {record.tier} "
                    f"does not match family decision {decision}"
                )
            if not 0.0 <= record.quality_score <= 100.0:
                raise ValueError(f"{record.family}/{record.timeframe}: invalid quality score")
            if record.tier == "SECONDARY" and not record.management_dependent:
                raise ValueError(
                    f"{record.family}/{record.timeframe}: SECONDARY must be marked "
                    "management_dependent"
                )
            if record.tier != "SECONDARY" and record.management_dependent:
                raise ValueError(
                    f"{record.family}/{record.timeframe}: management dependency only "
                    "belongs to SECONDARY in v1"
                )

        expected_records = {
            (family, timeframe)
            for family, item in self._families.items()
            for timeframe in TIMEFRAMES
            if str(item[timeframe]) in ROUTABLE_TIERS and timeframe != "1M"
        }
        missing = expected_records.difference(self._records)
        extra = set(self._records).difference(expected_records)
        if missing:
            raise ValueError(f"Missing production records: {sorted(missing)!r}")
        if extra:
            raise ValueError(f"Unexpected production records: {sorted(extra)!r}")


_DEFAULT_REGISTRY: ProductionRegistry | None = None


def get_production_registry() -> ProductionRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ProductionRegistry()
    return _DEFAULT_REGISTRY
