"""Historical parity check for the Karar Paneli v6.4.5 production adapter.

For each setup, find a real long-history signal produced by the Pine-compatible
signal layer, truncate history exactly at that signal bar, and verify that the
production adapter returns the same new setup plus a monotonic setup-specific
risk plan.  This is a wiring/parity check, not another optimizer.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from decision_panel_v2 import Settings
from decision_panel_v645_live import analyze_symbol
from decision_panel_v645_signal import decision_v645_series
from historical_decision_panel_v644 import _normalize
from market_data_store import MarketDataStore
from strategy_exit_profiles import get_exit_profile


SETUPS = ("PULLBACK", "TREND DEVAMI", "BREAKOUT")


def _assert_plan(setup: str, result: dict[str, Any]) -> None:
    plan = result.get("exit_plan")
    if not isinstance(plan, dict):
        raise AssertionError(f"{setup}: exit_plan üretilmedi")
    entry = float(plan["entry"])
    stop = float(plan["stop"])
    tp1 = float(plan["tp1"])
    tp2 = float(plan["tp2"])
    tp3 = float(plan["tp3"])
    if not (stop < entry < tp1 < tp2 < tp3):
        raise AssertionError(f"{setup}: plan monotonik değil: {stop}, {entry}, {tp1}, {tp2}, {tp3}")

    expected = get_exit_profile("decision_panel", setup=setup)
    profile = plan.get("profile", {})
    checks = {
        "stop_mode": expected.stop_mode,
        "tp1_r": expected.tp1_r,
        "tp2_r": expected.tp2_r,
        "tp3_r": expected.tp3_r,
        "trailing_atr_mult": expected.trailing_atr_mult,
    }
    for key, value in checks.items():
        actual = profile.get(key)
        if isinstance(value, float):
            if actual is None or abs(float(actual) - value) > 1e-9:
                raise AssertionError(f"{setup}: {key} beklenen={value}, gerçek={actual}")
        elif actual != value:
            raise AssertionError(f"{setup}: {key} beklenen={value}, gerçek={actual}")


def check_database(database: str, *, min_score: int = 75) -> dict[str, Any]:
    found: dict[str, dict[str, Any]] = {}
    settings = Settings(min_score=min_score, min_turnover_mn=0.0)

    with MarketDataStore(database, read_only=True) as store:
        symbols = store.list_symbols("BIST", "1D")
        for number, symbol in enumerate(symbols, start=1):
            if len(found) == len(SETUPS):
                break
            frame = store.load_dataframe(symbol, "BIST", "1D", limit=0)
            if frame is None or len(frame) < 280:
                continue
            try:
                clean = _normalize(frame)
                state = decision_v645_series(clean, min_score=min_score)
            except Exception as exc:
                print(f"[{number}/{len(symbols)}] {symbol}: signal HATA {exc}")
                continue

            for setup in SETUPS:
                if setup in found:
                    continue
                positions = np.flatnonzero(state["new_setup"].eq(setup).to_numpy(dtype=bool))
                if not len(positions):
                    continue
                pos = int(positions[0])
                history = clean.iloc[: pos + 1].copy()
                try:
                    live = analyze_symbol(symbol, history, settings, False)
                except Exception as exc:
                    raise AssertionError(f"{symbol} {setup}: production adapter HATA {exc}") from exc

                if live.get("signal_model") != "pine-compatible-v6.4.5":
                    raise AssertionError(f"{symbol} {setup}: yanlış signal_model {live.get('signal_model')}")
                if live.get("new_setup") != setup:
                    raise AssertionError(
                        f"{symbol}: parity bozuk; signal={setup}, live={live.get('new_setup')}"
                    )
                if live.get("setup") != setup:
                    raise AssertionError(
                        f"{symbol}: aktif setup bozuk; signal={setup}, live={live.get('setup')}"
                    )
                if not bool(live.get("entry")):
                    raise AssertionError(f"{symbol} {setup}: min_turnover=0 iken entry False")
                _assert_plan(setup, live)

                plan = live["exit_plan"]
                found[setup] = {
                    "symbol": symbol,
                    "signal_time": pd.Timestamp(history.index[-1]).isoformat(),
                    "score": int(live["score"]),
                    "rvol": float(live.get("pine_rvol", 0.0)),
                    "pct_20_high": float(live.get("pct_20_high", 0.0)),
                    "entry": float(plan["entry"]),
                    "stop": float(plan["stop"]),
                    "tp1": float(plan["tp1"]),
                    "tp2": float(plan["tp2"]),
                    "tp3": float(plan["tp3"]),
                    "stop_mode": plan["profile"]["stop_mode"],
                    "tp_r": [float(plan["tp1_r"]), float(plan["tp2_r"]), float(plan["tp3_r"])],
                    "trailing_atr_mult": float(plan["trailing_atr_mult"]),
                    "parity": "PASS",
                }
                print(f"PASS {setup}: {symbol} {found[setup]['signal_time']}")

    missing = [setup for setup in SETUPS if setup not in found]
    if missing:
        raise AssertionError(f"Uzun tarih snapshot'ta parity örneği bulunamadı: {missing}")

    return {
        "engine": "decision_panel_v6.4.5",
        "dataset": "historical-daily-latest",
        "min_score": min_score,
        "status": "PASS",
        "setups": found,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--min-score", type=int, default=75)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = check_database(args.db, min_score=args.min_score)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
