from production_scanners.registry import ProductionRegistry


def test_registry_validates_and_routes_expected_models():
    registry = ProductionRegistry()
    assert registry.decision("11 - Stoc.RSI & RSI & BB & MACD", "1W") == "CORE"
    assert registry.decision("14 - MACD DipDönüşü", "1D") == "SECONDARY"
    assert registry.decision("10 - HO/Volatilite", "1W") == "REJECT"
    assert registry.is_routable("BB & SMA", "2H")
    assert not registry.is_routable("NE ARARSAN VAR", "4H")
    assert not registry.is_routable("BB & SMA", "1M")


def test_registry_has_one_record_for_every_routable_family_timeframe():
    registry = ProductionRegistry()
    records = registry.production_records()
    assert len(records) == 28
    assert all(record.timeframe != "1M" for record in records)
    assert all(record.tier in {"CORE", "ACTIVE", "SECONDARY"} for record in records)


def test_secondary_records_are_management_dependent():
    registry = ProductionRegistry()
    secondary = registry.production_records(tiers={"SECONDARY"})
    assert len(secondary) == 7
    assert all(record.management_dependent for record in secondary)
