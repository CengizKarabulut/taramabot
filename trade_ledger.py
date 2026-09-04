"""Persistent JSONL trade ledger for simulated and live scanner trades.

The ledger is deliberately append-only.  Events are reconstructed into the
latest state so historical changes to stop/trailing levels remain auditable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ISTANBUL = ZoneInfo("Europe/Istanbul")
DEFAULT_LEDGER = Path("data/trade_ledger.jsonl")


@dataclass(frozen=True)
class TradeEvent:
    trade_id: str
    event: str
    timestamp: str
    payload: dict[str, Any]


def now_iso() -> str:
    return datetime.now(ISTANBUL).isoformat(timespec="seconds")


def make_trade_id(symbol: str, period: str, strategy: str, signal_time: str) -> str:
    stamp = str(signal_time).replace(":", "").replace("-", "").replace("+", "_").replace(" ", "T")
    return f"{symbol.upper()}__{period}__{strategy}__{stamp}"


class TradeLedger:
    def __init__(self, path: str | Path = DEFAULT_LEDGER):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, trade_id: str, event: str, payload: dict[str, Any] | None = None, timestamp: str | None = None) -> TradeEvent:
        record = TradeEvent(
            trade_id=str(trade_id),
            event=str(event).upper(),
            timestamp=timestamp or now_iso(),
            payload=dict(payload or {}),
        )
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(record), ensure_ascii=False, default=str) + "\n")
        return record

    def signal(self, *, symbol: str, period: str, strategy: str, signal_time: str, price: float, plan: dict[str, Any], extra: dict[str, Any] | None = None) -> str:
        trade_id = make_trade_id(symbol, period, strategy, signal_time)
        payload = {
            "symbol": symbol.upper(),
            "period": period,
            "strategy": strategy,
            "signal_time": signal_time,
            "signal_price": float(price),
            "plan": plan,
            **(extra or {}),
        }
        self.append(trade_id, "SIGNAL", payload, timestamp=signal_time)
        return trade_id

    def enter(self, trade_id: str, *, price: float, timestamp: str, quantity: float = 1.0) -> None:
        self.append(trade_id, "ENTRY", {"price": float(price), "quantity": float(quantity)}, timestamp)

    def target(self, trade_id: str, *, target: str, price: float, allocation: float, timestamp: str) -> None:
        self.append(trade_id, str(target).upper(), {"price": float(price), "allocation": float(allocation)}, timestamp)

    def stop_update(self, trade_id: str, *, old_stop: float, new_stop: float, reason: str, timestamp: str) -> None:
        self.append(trade_id, "STOP_UPDATE", {"old_stop": float(old_stop), "new_stop": float(new_stop), "reason": reason}, timestamp)

    def close(self, trade_id: str, *, price: float, reason: str, realized_r: float, return_pct: float, timestamp: str, metrics: dict[str, Any] | None = None) -> None:
        self.append(
            trade_id,
            "CLOSE",
            {
                "price": float(price),
                "reason": str(reason),
                "realized_r": float(realized_r),
                "return_pct": float(return_pct),
                **(metrics or {}),
            },
            timestamp,
        )

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("trade_id"):
                records.append(item)
        return records

    def states(self) -> dict[str, dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for record in self.events():
            trade_id = record["trade_id"]
            state = states.setdefault(trade_id, {"trade_id": trade_id, "events": []})
            state["events"].append(record)
            event = record.get("event")
            payload = record.get("payload") or {}
            state["last_event"] = event
            state["last_timestamp"] = record.get("timestamp")
            if event == "SIGNAL":
                state.update(payload)
                state["status"] = "SIGNALLED"
            elif event == "ENTRY":
                state["entry"] = payload
                state["status"] = "OPEN"
            elif event in {"TP1", "TP2", "TP3"}:
                state.setdefault("targets", []).append({"event": event, "timestamp": record.get("timestamp"), **payload})
            elif event == "STOP_UPDATE":
                state["active_stop"] = payload.get("new_stop")
                state.setdefault("stop_history", []).append({"timestamp": record.get("timestamp"), **payload})
            elif event == "CLOSE":
                state["close"] = {"timestamp": record.get("timestamp"), **payload}
                state["status"] = "CLOSED"
        return states

    def closed_trades(self) -> list[dict[str, Any]]:
        return [state for state in self.states().values() if state.get("status") == "CLOSED"]

    def open_trades(self) -> list[dict[str, Any]]:
        return [state for state in self.states().values() if state.get("status") == "OPEN"]


def append_backtest_result(
    ledger: TradeLedger,
    *,
    symbol: str,
    period: str,
    strategy: str,
    signal_time: str,
    result: dict[str, Any],
    setup: str = "",
) -> str:
    """Persist a compact synthetic lifecycle from one backtest result."""
    plan = result.get("plan") or {}
    trade_id = ledger.signal(
        symbol=symbol,
        period=period,
        strategy=strategy,
        signal_time=signal_time,
        price=float(plan.get("entry", result.get("entry", 0)) or 0),
        plan=plan,
        extra={"mode": "BACKTEST", "setup": setup},
    )
    ledger.enter(trade_id, price=result["entry"], timestamp=result["entry_time"])
    allocations = list(plan.get("tp_allocations") or [0.0, 0.0, 0.0])
    for index, target in enumerate(("TP1", "TP2", "TP3")):
        if result.get(f"tp{index+1}_hit"):
            ledger.target(
                trade_id,
                target=target,
                price=float(result[target.lower()]),
                allocation=float(allocations[index] if index < len(allocations) else 0.0),
                timestamp=result["exit_time"],
            )
    ledger.close(
        trade_id,
        price=float(result.get("final_stop") if result.get("stop_hit") or result.get("trailing_exit") else plan.get("entry", result["entry"])),
        reason=result["exit_reason"],
        realized_r=result["realized_r"],
        return_pct=result["return_pct"],
        timestamp=result["exit_time"],
        metrics={
            "mfe_r": result.get("mfe_r"), "mae_r": result.get("mae_r"),
            "mfe_pct": result.get("mfe_pct"), "mae_pct": result.get("mae_pct"),
            "bars_held": result.get("bars_held"),
        },
    )
    return trade_id
