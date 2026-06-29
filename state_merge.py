"""Merge scanner state files without using Git's text conflict resolution."""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


TZ_TURKEY = ZoneInfo("Europe/Istanbul")
RETENTION_DAYS = int(os.getenv("SIGNAL_HISTORY_RETENTION_DAYS", "45"))
MAX_HISTORY = int(os.getenv("SIGNAL_HISTORY_MAX_ENTRIES", "25000"))


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            state = json.load(file)
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Ignoring invalid state file {path}: {exc}", file=sys.stderr)
        return {}


def parse_time(value) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=TZ_TURKEY)
        return parsed.astimezone(TZ_TURKEY)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=TZ_TURKEY)


def entry_time(entry) -> datetime:
    if isinstance(entry, dict):
        return parse_time(entry.get("detected_at") or entry.get("time"))
    return parse_time(entry)


def merge_latest_entries(base: dict, incoming: dict) -> dict:
    merged = dict(base or {})
    for key, value in (incoming or {}).items():
        if key not in merged or entry_time(value) >= entry_time(merged[key]):
            merged[key] = value
    return merged


def history_key(event: dict) -> tuple:
    return (
        str(event.get("symbol", "")),
        str(event.get("period", "")),
        str(event.get("strategy", "")),
        str(event.get("bar_time", "")),
        bool(event.get("is_full")),
    )


def merge_history(*histories) -> list[dict]:
    cutoff = datetime.now(TZ_TURKEY) - timedelta(days=RETENTION_DAYS)
    merged = {}
    for history in histories:
        if not isinstance(history, list):
            continue
        for event in history:
            if not isinstance(event, dict):
                continue
            detected_at = parse_time(event.get("detected_at") or event.get("bar_time"))
            if detected_at < cutoff:
                continue
            key = history_key(event)
            current = merged.get(key)
            if current is None or detected_at < parse_time(
                current.get("detected_at") or current.get("bar_time")
            ):
                merged[key] = event
    ordered = sorted(
        merged.values(),
        key=lambda event: parse_time(event.get("detected_at") or event.get("bar_time")),
    )
    return ordered[-MAX_HISTORY:]


def merge_states(base: dict, incoming: dict) -> dict:
    merged = dict(base or {})
    for key, value in (incoming or {}).items():
        if key == "signal_history":
            continue
        if key.startswith("last_sent_") and isinstance(value, dict):
            merged[key] = merge_latest_entries(merged.get(key, {}), value)
        elif key == "last_successful_scans" and isinstance(value, dict):
            combined = dict(merged.get(key, {}))
            combined.update(value)
            merged[key] = combined
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            combined = dict(merged[key])
            combined.update(value)
            merged[key] = combined
        else:
            merged[key] = value
    merged["signal_history"] = merge_history(
        base.get("signal_history", []) if isinstance(base, dict) else [],
        incoming.get("signal_history", []) if isinstance(incoming, dict) else [],
    )
    return merged


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--incoming", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    state = load_state(args.base)
    for incoming_path in args.incoming:
        state = merge_states(state, load_state(incoming_path))
    write_state(args.output, state)


if __name__ == "__main__":
    main()
