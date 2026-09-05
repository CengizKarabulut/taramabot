"""Risk-aware compatibility layer for main.py.

Internal A-I strategy keys remain stable for scanner/backtest parity.  The
Pine-compatible Karar v6.4.5 signal is calculated from the same SQLite snapshot
and exposed as a tenth outward-facing scan family named KARAR.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import main_legacy as legacy
from main_legacy import *  # noqa: F401,F403

from decision_panel_v645_signal import latest_decision
from exit_engine import build_exit_plan
from market_data_store import MarketDataStore
from scanner_legacy import ScannerState


ISTANBUL = ZoneInfo("Europe/Istanbul")
DECISION_GROUP = "decision"
DECISION_MIN_SCORE = int(os.getenv("DECISION_MIN_SCORE", "70"))

PLAN_KEY_BY_GROUP = {
    "full": "smi_macd_full",
    "smi": "smi_macd",
    "rsi": "rsi",
    "new": "new_scan",
    "rsi_macd": "rsi_macd",
    "ema": "ema",
    "macd_cross": "macd_cross",
    "h8": "h8",
    "i9": "i9",
}

# Stable external aliases.  These match the TradingView private panel.
PRIVATE_DISPLAY_CODE = {
    "macd_cross": "A",
    "h8": "B",
    "i9": "C",
    "ema": "D",
    "rsi_macd": "E",
    "new": "F",
    "full": "G",
    "smi": "H",
    "rsi": "I",
    DECISION_GROUP: "KARAR",
}


def _compact_item(item: dict, strategy_key: str) -> dict:
    plan_key = PLAN_KEY_BY_GROUP[strategy_key]
    plan = (item.get("exit_plans") or {}).get(plan_key) or {}
    compact = {
        "symbol": item.get("symbol"),
        "change": float(item.get("change", 0) or 0),
        "daily_change": float(item.get("daily_change", item.get("change", 0)) or 0),
        "current_price": float(item.get("current_price", item.get("close", 0)) or 0),
        "close": float(item.get("close", item.get("current_price", 0)) or 0),
        "bar_time": item.get("bar_time"),
        "stale_bar": bool(item.get("stale_bar", False)),
    }
    if plan:
        # Do not serialize family/profile internals into outward-facing snapshots.
        compact["stop"] = plan.get("stop")
        compact["tp1"] = plan.get("tp1")
        compact["tp2"] = plan.get("tp2")
        compact["tp3"] = plan.get("tp3")
        compact["stop_pct"] = plan.get("stop_pct")
        compact["tp1_pct"] = plan.get("tp1_pct")
        compact["tp2_pct"] = plan.get("tp2_pct")
        compact["tp3_pct"] = plan.get("tp3_pct")
        compact["risk_atr"] = plan.get("risk_atr")
    return compact


def _percent_change(current, previous) -> float:
    try:
        current_value = float(current)
        previous_value = float(previous)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(current_value) or not math.isfinite(previous_value) or previous_value == 0:
        return 0.0
    return (current_value / previous_value - 1.0) * 100.0


def _daily_change(store: MarketDataStore, symbol: str, current_close: float, fallback: float) -> float:
    try:
        daily = store.load_dataframe(symbol, "BIST", "1D", limit=6)
        if daily is None or daily.empty:
            return fallback
        daily = daily.copy().sort_index()
        dates = [getattr(index, "date", lambda: None)() for index in daily.index]
        last_date = dates[-1]
        # If the latest daily row belongs to the same current session, compare
        # current price with the previous distinct session.  Otherwise the last
        # daily close is the previous session reference.
        reference = None
        for pos in range(len(daily) - 2, -1, -1):
            if dates[pos] is not None and dates[pos] != last_date:
                reference = float(daily["close"].iloc[pos])
                break
        if reference is None and len(daily) >= 2:
            reference = float(daily["close"].iloc[-2])
        return _percent_change(current_close, reference) if reference else fallback
    except Exception:
        return fallback


def _decision_state(use_state: bool) -> ScannerState | None:
    if not use_state:
        return None
    try:
        return ScannerState()
    except Exception as exc:
        legacy.logger.warning("KARAR state yuklenemedi; state filtresiz devam: %s", exc)
        return None


def _decision_sent(state: ScannerState | None, symbol: str, period: str, bar_time: str) -> bool:
    if state is None:
        return False
    entry = (state.state.get("last_sent_decision") or {}).get(f"{symbol}_{period}")
    if isinstance(entry, dict):
        return str(entry.get("time", "")) == str(bar_time)
    return str(entry or "") == str(bar_time)


def _mark_decision_sent(
    state: ScannerState | None,
    *,
    symbol: str,
    period: str,
    bar_time: str,
    price: float,
    setup: str,
    score: int,
) -> None:
    if state is None:
        return
    detected_at = datetime.now(ISTANBUL).isoformat(timespec="seconds")
    key = f"{symbol}_{period}"
    state.state.setdefault("last_sent_decision", {})[key] = {
        "time": bar_time,
        "price": price,
        "detected_at": detected_at,
        "setup": setup,
        "score": score,
    }
    state.state.setdefault("signal_history", []).append(
        {
            "symbol": symbol,
            "period": period,
            "strategy": "decision",
            "bar_time": bar_time,
            "detected_at": detected_at,
            "price": price,
            "setup": setup,
            "score": score,
        }
    )


def _decision_signals_for_period(period: str, market_type: str) -> list[dict]:
    """Calculate Pine-compatible fresh KARAR entries from the same snapshot."""
    if str(market_type or "").lower() != "bist":
        return []
    database_value = os.getenv("MARKET_DATA_DB", "").strip()
    if not database_value:
        return []
    database = Path(database_value)
    if not database.is_file() or database.stat().st_size == 0:
        return []

    use_state = "--nostate" not in sys.argv
    state = _decision_state(use_state)
    signals: list[dict] = []

    try:
        with MarketDataStore(database, read_only=True) as store:
            symbols = store.list_symbols("BIST", period)
            for symbol in symbols:
                try:
                    frame = store.load_dataframe(symbol, "BIST", period, limit=400)
                    if frame is None or len(frame) < 60:
                        continue
                    pine = latest_decision(frame, min_score=DECISION_MIN_SCORE)
                    if not bool(pine.get("entry")):
                        continue

                    setup = str(pine.get("new_setup") or "").strip()
                    if not setup:
                        continue
                    bar_time = frame.index[-1].isoformat()
                    if _decision_sent(state, symbol, period, bar_time):
                        continue

                    close = float(frame["close"].iloc[-1])
                    previous = float(frame["close"].iloc[-2])
                    change = _percent_change(close, previous)
                    daily_change = _daily_change(store, symbol, close, change)
                    try:
                        plan = build_exit_plan(
                            frame,
                            "decision_panel",
                            setup=setup,
                            entry_price=close,
                        )
                    except Exception as exc:
                        legacy.logger.debug("KARAR exit plan uretilmedi (%s/%s): %s", symbol, period, exc)
                        plan = None

                    item = {
                        "symbol": symbol,
                        "change": change,
                        "daily_change": daily_change,
                        "current_price": close,
                        "close": close,
                        "bar_time": bar_time,
                        "stale_bar": False,
                        "setup": setup,
                        "decision_score": int(pine.get("score", 0) or 0),
                    }
                    if plan:
                        for key in ("stop", "tp1", "tp2", "tp3", "stop_pct", "tp1_pct", "tp2_pct", "tp3_pct", "risk_atr"):
                            if key in plan:
                                item[key] = plan.get(key)
                    signals.append(item)
                    _mark_decision_sent(
                        state,
                        symbol=symbol,
                        period=period,
                        bar_time=bar_time,
                        price=close,
                        setup=setup,
                        score=int(pine.get("score", 0) or 0),
                    )
                except Exception as exc:
                    legacy.logger.debug("KARAR hesaplanamadi (%s/%s): %s", symbol, period, exc)
    finally:
        if state is not None:
            state.save()

    legacy.logger.info("KARAR %s: %s yeni Pine-compatible sinyal", period, len(signals))
    return signals


def _write_scan_result(result: dict | None) -> None:
    output_path = os.getenv("SCAN_RESULT_FILE", "").strip()
    if not output_path:
        return
    payload = result or {}
    period = payload.get("period")
    market_type = payload.get("market_type", "bist")
    compact = {
        "period": period,
        "market_type": market_type,
        "total_scanned": payload.get("total_scanned", 0),
    }
    for strategy_key in legacy.STRATEGY_KEYS:
        compact[strategy_key] = [
            _compact_item(item, strategy_key)
            for item in payload.get(strategy_key, [])
        ]
    compact[DECISION_GROUP] = _decision_signals_for_period(str(period), str(market_type)) if period else []

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(compact, ensure_ascii=False), encoding="utf-8")
    temporary.replace(destination)


def _build_common_signal_map(all_results: list[dict]) -> dict:
    symbol_map = {}
    group_keys = tuple(legacy.STRATEGY_KEYS) + (DECISION_GROUP,)
    for result in all_results:
        period = result["period"]
        for strategy_key in group_keys:
            for item in result.get(strategy_key, []):
                symbol = item.get("symbol")
                if not symbol:
                    continue
                symbol_map.setdefault(symbol, {}).setdefault(period, []).append({
                    "code": PRIVATE_DISPLAY_CODE[strategy_key],
                    "change": item.get("change"),
                    "daily_change": item.get("daily_change", item.get("change")),
                    "current_price": item.get("current_price", item.get("close")),
                    "stop": item.get("stop"),
                    "tp1": item.get("tp1"),
                    "tp2": item.get("tp2"),
                    "tp3": item.get("tp3"),
                })
    return {
        symbol: periods
        for symbol, periods in symbol_map.items()
        if sum(len({entry["code"] for entry in entries}) for entries in periods.values()) > 1
    }


def send_saved_results(paths: list[str]) -> None:
    """Send A-I + KARAR as one chronological scan package."""
    if not legacy.TELEGRAM_ENABLED:
        return

    results = []
    for path in paths:
        try:
            result = json.loads(Path(path).read_text(encoding="utf-8"))
            if result.get("period"):
                results.append(result)
        except (OSError, json.JSONDecodeError) as exc:
            legacy.logger.error("Paralel tarama sonucu okunamadi (%s): %s", path, exc)

    order = {period: index for index, period in enumerate(legacy.PERIOD_ORDER)}
    results.sort(key=lambda item: order.get(item.get("period"), len(order)))
    telegram_sender = legacy.get_telegram_sender()
    enabled_codes = list("ABCDEFGHI") + ["KARAR"]

    for result in results:
        telegram_sender.send_grouped_summary(
            period=result["period"],
            full_signals=result.get("full", []),
            smi_signals=result.get("smi", []),
            rsi_signals=result.get("rsi", []),
            new_scan_signals=result.get("new", []),
            rsi_macd_signals=result.get("rsi_macd", []),
            ema_signals=result.get("ema", []),
            macd_cross_signals=result.get("macd_cross", []),
            h8_signals=result.get("h8", []),
            i9_signals=result.get("i9", []),
            decision_signals=result.get(DECISION_GROUP, []),
            market_type=result.get("market_type", "bist"),
            total_scanned=result.get("total_scanned"),
            enabled_strategy_codes=enabled_codes,
        )
        period_signal_map = _build_common_signal_map([result])
        if period_signal_map:
            telegram_sender.send_common_signal_visuals(
                period_signal_map,
                title=f"{result['period']} Coklu Sinyal Ozeti",
            )

    legacy.send_final_summary(results)


# Patch the functions referenced from inside legacy.main_scan_logic / run_bot.
legacy._write_scan_result = _write_scan_result
legacy._build_common_signal_map = _build_common_signal_map
legacy.send_saved_results = send_saved_results

# Public aliases expected by helper scripts.
main_scan_logic = legacy.main_scan_logic
run_multi_scan = legacy.run_multi_scan
send_final_summary = legacy.send_final_summary
run_bot = legacy.run_bot


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_bot())
