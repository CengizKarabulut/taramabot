from production_scanners.fingerprint import build_technical_fingerprint


def _evidence(family, timeframe, features):
    return {
        "family": family,
        "timeframe": timeframe,
        "features": features,
        "triggered": True,
        "tier": "CORE",
        "quality_score": 100,
    }


def test_registry_is_authoritative_over_caller_values():
    result = build_technical_fingerprint(
        [_evidence("7 - BB Daralması", "4H", ["bollinger", "macd", "volume"])]
    )
    item = result["evidence"][0]
    assert item["tier"] == "SECONDARY"
    assert item["quality_score"] == 48.0
    assert item["management_dependent"] is True


def test_redundant_signals_do_not_sum_naively():
    result = build_technical_fingerprint([
        _evidence("13 - MACD YenidenHareket", "1W", ["macd"]),
        _evidence("13 - MACD YenidenHareket", "1D", ["macd"]),
    ])
    assert result["evidence_count"] == 2
    assert result["raw_naive_sum"] > result["score"]
    assert result["overlap_penalty"] > 0
    assert result["evidence"][1]["max_overlap_with_stronger"] == 0.9


def test_independent_confirmation_adds_more_than_overlapping_confirmation():
    anchor = _evidence(
        "11 - Stoc.RSI & RSI & BB & MACD",
        "1W",
        ["rsi", "stoch_rsi", "bollinger", "macd", "volume"],
    )
    overlapping = _evidence("RSI & MACD - RVOL", "1W", ["rsi", "macd", "volume"])
    independent = _evidence(
        "StochRSI / Momentum / Hacim",
        "1W",
        ["stoch_rsi", "momentum", "volume"],
    )
    overlap_result = build_technical_fingerprint([anchor, overlapping])
    independent_result = build_technical_fingerprint([anchor, independent])
    assert (
        independent_result["evidence"][1]["score_contribution"]
        > overlap_result["evidence"][1]["score_contribution"]
    )


def test_non_routable_evidence_is_ignored():
    result = build_technical_fingerprint([
        _evidence("NE ARARSAN VAR", "4H", ["trend_ema", "rsi", "macd", "volume"])
    ])
    assert result["score"] == 0.0
    assert result["evidence_count"] == 0
