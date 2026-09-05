from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from multi_timeframe_context import alignment, analyze_frame, closed_frame

TZ = ZoneInfo("Europe/Istanbul")


def _frame(index):
    n = len(index)
    close = pd.Series([100 + i * 0.2 for i in range(n)], index=index)
    return pd.DataFrame({
        "open": close - 0.1,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": 1000,
    }, index=index)


def test_intraday_forming_bar_is_dropped():
    idx = pd.date_range("2026-09-04 10:00", periods=4, freq="2h")
    frame = _frame(idx)
    out = closed_frame(frame, "2H", datetime(2026, 9, 4, 16, 30, tzinfo=TZ))
    assert list(out.index) == list(idx[:3])


def test_intraday_bar_kept_after_completion():
    idx = pd.date_range("2026-09-04 10:00", periods=4, freq="2h")
    frame = _frame(idx)
    out = closed_frame(frame, "2H", datetime(2026, 9, 4, 18, 5, tzinfo=TZ))
    assert len(out) == 4


def test_daily_forming_bar_is_dropped_before_close():
    idx = pd.date_range("2026-09-01", periods=4, freq="D")
    frame = _frame(idx)
    out = closed_frame(frame, "1D", datetime(2026, 9, 4, 17, 0, tzinfo=TZ))
    assert len(out) == 3


def test_alignment_never_becomes_a_signal():
    contexts = {
        "45m": {"status": "HAZIR", "trend": "YUKSELEN YAPI", "bos": "YUKARI BOS", "ema_regime": "POZITIF"},
        "2H": {"status": "HAZIR", "trend": "YUKSELEN YAPI", "bos": "YOK", "ema_regime": "POZITIF"},
        "4H": {"status": "HAZIR", "trend": "YUKSELEN YAPI", "bos": "YUKARI BOS", "ema_regime": "POZITIF"},
        "1D": {"status": "HAZIR", "trend": "YUKSELEN YAPI", "bos": "YOK", "ema_regime": "POZITIF"},
        "1W": {"status": "HAZIR", "trend": "YUKSELEN YAPI", "bos": "YUKARI BOS", "ema_regime": "POZITIF"},
    }
    result = alignment(contexts)
    assert result["label"] == "YUKARI UYUMLU"
    assert result["score"] > 45
    assert "AL" not in result["label"]


def test_analysis_handles_short_data():
    frame = _frame(pd.date_range("2026-01-01", periods=10, freq="D"))
    assert analyze_frame(frame, "1D")["status"] == "VERI YETERSIZ"
