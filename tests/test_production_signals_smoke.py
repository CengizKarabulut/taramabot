import numpy as np
import pandas as pd

from production_scanners.signals import evaluate_timeframe


def _sample_frame(n: int = 320) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 100.0 + np.cumsum(rng.normal(0.0, 1.0, n))
    open_ = close + rng.normal(0.0, 0.30, n)
    high = np.maximum(open_, close) + rng.uniform(0.10, 1.00, n)
    low = np.minimum(open_, close) - rng.uniform(0.10, 1.00, n)
    volume = rng.lognormal(12.0, 0.50, n)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=pd.date_range("2020-01-01", periods=n, freq="D"),
    )


def test_every_routable_model_has_a_working_evaluator():
    frame = _sample_frame()
    assert len(evaluate_timeframe(frame, "2H", only_triggered=False)) == 1
    assert len(evaluate_timeframe(frame, "4H", only_triggered=False)) == 7
    assert len(evaluate_timeframe(frame, "1D", only_triggered=False)) == 10
    assert len(evaluate_timeframe(frame, "1W", only_triggered=False)) == 10
