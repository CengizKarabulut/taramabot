"""Risk-aware compatibility layer for main.py.

Internal strategy keys remain stable for scanner/backtest parity. External
summaries use opaque display codes and compact result artifacts expose only
levels needed by the presentation layer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import main_legacy as legacy
from main_legacy import *  # noqa: F401,F403


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


def _write_scan_result(result: dict | None) -> None:
    output_path = os.getenv("SCAN_RESULT_FILE", "").strip()
    if not output_path:
        return
    payload = result or {}
    compact = {
        "period": payload.get("period"),
        "market_type": payload.get("market_type", "bist"),
        "total_scanned": payload.get("total_scanned", 0),
    }
    for strategy_key in legacy.STRATEGY_KEYS:
        compact[strategy_key] = [
            _compact_item(item, strategy_key)
            for item in payload.get(strategy_key, [])
        ]
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(compact, ensure_ascii=False), encoding="utf-8")
    temporary.replace(destination)


def _build_common_signal_map(all_results: list[dict]) -> dict:
    symbol_map = {}
    for result in all_results:
        period = result["period"]
        for strategy_key in legacy.STRATEGY_KEYS:
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


# Patch the functions referenced from inside legacy.main_scan_logic / final summary.
legacy._write_scan_result = _write_scan_result
legacy._build_common_signal_map = _build_common_signal_map

# Public aliases expected by helper scripts.
main_scan_logic = legacy.main_scan_logic
run_multi_scan = legacy.run_multi_scan
send_final_summary = legacy.send_final_summary
send_saved_results = legacy.send_saved_results
run_bot = legacy.run_bot


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_bot())
