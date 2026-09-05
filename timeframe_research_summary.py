"""Aggregate timeframe research artifacts into a production-candidate matrix.

The script is intentionally dependency-free. It understands both classic scanner
cost-replay JSON files and System J period-optimizer JSON files, then applies one
transparent promotion policy across timeframes.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable


PERIOD_ORDER = {
    "15m": 0,
    "30m": 1,
    "45m": 2,
    "1H": 3,
    "2H": 4,
    "4H": 5,
    "1D": 6,
    "1W": 7,
    "1M": 8,
}

PROMOTE_MIN_TRADES = 50
PROMOTE_MIN_MODERATE_PF = 1.20
PROMOTE_MIN_MODERATE_EXPECTANCY = 0.0
WATCH_MIN_LIGHT_PF = 1.05
WATCH_MIN_GROSS_PF = 1.10


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _metrics(data: dict[str, Any], scenario: str) -> dict[str, Any]:
    return (
        data.get("scenarios", {})
        .get(scenario, {})
        .get("metrics", {})
        or {}
    )


def _classify(
    *,
    trades: int,
    gross_pf: float,
    light_pf: float,
    moderate_pf: float,
    moderate_expectancy: float,
    explicit_pass: bool | None,
    robust_sample: bool,
) -> tuple[str, list[str]]:
    reasons: list[str] = []

    if trades < PROMOTE_MIN_TRADES:
        reasons.append(f"sample<{PROMOTE_MIN_TRADES}")
    if moderate_pf < PROMOTE_MIN_MODERATE_PF:
        reasons.append(f"moderate_pf<{PROMOTE_MIN_MODERATE_PF:.2f}")
    if moderate_expectancy <= PROMOTE_MIN_MODERATE_EXPECTANCY:
        reasons.append("moderate_expectancy<=0")
    if not robust_sample:
        reasons.append("sample_not_robust")
    if explicit_pass is False:
        reasons.append("native_cost_gate_failed")

    promote = (
        trades >= PROMOTE_MIN_TRADES
        and moderate_pf >= PROMOTE_MIN_MODERATE_PF
        and moderate_expectancy > PROMOTE_MIN_MODERATE_EXPECTANCY
        and robust_sample
        and explicit_pass is not False
    )
    if promote:
        return "PROMOTE", ["moderate-cost OOS gate passed"]

    watch = (
        trades >= max(25, PROMOTE_MIN_TRADES // 2)
        and gross_pf >= WATCH_MIN_GROSS_PF
        and light_pf >= WATCH_MIN_LIGHT_PF
        and moderate_expectancy > -0.05
    )
    if watch:
        return "WATCH", reasons or ["borderline cost robustness"]

    return "REJECT", reasons or ["OOS/cost gate failed"]


def _score(row: dict[str, Any]) -> float:
    """Ranking score; classification remains the hard production gate."""
    trades = max(0.0, _num(row.get("trades")))
    moderate_pf = _num(row.get("moderate_pf"))
    light_pf = _num(row.get("light_pf"))
    gross_pf = _num(row.get("gross_pf"))
    expectancy = _num(row.get("moderate_expectancy"))
    drawdown = max(0.0, _num(row.get("moderate_drawdown_r")))
    sample_bonus = min(10.0, math.log10(max(trades, 1.0)) * 3.0)
    dd_penalty = min(15.0, drawdown / 12.0)
    score = (
        35.0 * max(0.0, moderate_pf - 0.75)
        + 15.0 * max(0.0, light_pf - 0.90)
        + 8.0 * max(0.0, gross_pf - 1.0)
        + 45.0 * expectancy
        + sample_bonus
        - dd_penalty
    )
    return round(score, 3)


def _classic_row(path: Path, data: dict[str, Any]) -> dict[str, Any] | None:
    if not path.name.endswith("_costs.json"):
        return None
    strategy = str(data.get("strategy") or "").strip()
    period = str(data.get("period") or "").strip()
    if not strategy or not period:
        return None

    gross = _metrics(data, "gross") or data.get("expected_gross_oos", {}) or {}
    light = _metrics(data, "light_5_5bps")
    moderate = _metrics(data, "moderate_10_10bps")
    trades = int(_num(moderate.get("trades", gross.get("trades", 0))))
    robust = bool(data.get("robust_sample", trades >= PROMOTE_MIN_TRADES))
    explicit = data.get("moderate_cost_pass")
    if not isinstance(explicit, bool):
        explicit = None

    status, reasons = _classify(
        trades=trades,
        gross_pf=_num(gross.get("profit_factor")),
        light_pf=_num(light.get("profit_factor")),
        moderate_pf=_num(moderate.get("profit_factor")),
        moderate_expectancy=_num(moderate.get("expectancy_r")),
        explicit_pass=explicit,
        robust_sample=robust,
    )
    row = {
        "kind": "classic",
        "period": period,
        "system": strategy,
        "native_status": data.get("status"),
        "classification": status,
        "reasons": reasons,
        "trades": trades,
        "gross_pf": _num(gross.get("profit_factor")),
        "light_pf": _num(light.get("profit_factor")),
        "moderate_pf": _num(moderate.get("profit_factor")),
        "moderate_expectancy": _num(moderate.get("expectancy_r")),
        "moderate_drawdown_r": _num(moderate.get("max_drawdown_r")),
        "win_rate": _num(moderate.get("win_rate")),
        "profile": data.get("profile"),
        "source": str(path),
    }
    row["score"] = _score(row)
    return row


def _system_j_row(path: Path, data: dict[str, Any]) -> dict[str, Any] | None:
    meta = data.get("metadata", {}) or {}
    setup = str(meta.get("setup") or data.get("setup") or "").strip()
    period = str(meta.get("period") or data.get("period") or "").strip()
    if not setup or not period or "scenarios" not in data:
        return None

    gross = _metrics(data, "gross") or data.get("out_of_sample", {}) or {}
    light = _metrics(data, "light_5_5bps")
    moderate = _metrics(data, "moderate_10_10bps")
    trades = int(_num(moderate.get("trades", gross.get("trades", 0))))
    native_status = str(data.get("status") or "")
    explicit = True if native_status.upper() in {"PROMOTED", "PASS", "OOS_COST_PASS"} else None
    if "FAIL" in native_status.upper():
        explicit = False
    robust = trades >= PROMOTE_MIN_TRADES

    status, reasons = _classify(
        trades=trades,
        gross_pf=_num(gross.get("profit_factor")),
        light_pf=_num(light.get("profit_factor")),
        moderate_pf=_num(moderate.get("profit_factor")),
        moderate_expectancy=_num(moderate.get("expectancy_r")),
        explicit_pass=explicit,
        robust_sample=robust,
    )
    row = {
        "kind": "system-j",
        "period": period,
        "system": f"System J · {setup}",
        "setup": setup,
        "native_status": native_status,
        "classification": status,
        "reasons": reasons,
        "trades": trades,
        "gross_pf": _num(gross.get("profit_factor")),
        "light_pf": _num(light.get("profit_factor")),
        "moderate_pf": _num(moderate.get("profit_factor")),
        "moderate_expectancy": _num(moderate.get("expectancy_r")),
        "moderate_drawdown_r": _num(moderate.get("max_drawdown_r")),
        "win_rate": _num(moderate.get("win_rate")),
        "profile": (data.get("selected") or {}).get("profile"),
        "source": str(path),
    }
    row["score"] = _score(row)
    return row


def _iter_json(root: Path) -> Iterable[Path]:
    yield from sorted(root.rglob("*.json"))


def aggregate(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for path in _iter_json(root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # artifact files should not break the whole matrix
            parse_errors.append({"source": str(path), "error": str(exc)})
            continue
        if not isinstance(data, dict):
            continue

        row = _classic_row(path, data) or _system_j_row(path, data)
        if row is None:
            continue
        key = (row["kind"], row["period"], row["system"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)

    rows.sort(
        key=lambda r: (
            PERIOD_ORDER.get(r["period"], 99),
            {"PROMOTE": 0, "WATCH": 1, "REJECT": 2}.get(r["classification"], 3),
            -_num(r.get("score")),
            r["system"],
        )
    )

    counts = {label: sum(1 for r in rows if r["classification"] == label) for label in ("PROMOTE", "WATCH", "REJECT")}
    return {
        "policy": {
            "promote_min_trades": PROMOTE_MIN_TRADES,
            "promote_min_moderate_pf": PROMOTE_MIN_MODERATE_PF,
            "promote_min_moderate_expectancy": PROMOTE_MIN_MODERATE_EXPECTANCY,
            "watch_min_light_pf": WATCH_MIN_LIGHT_PF,
            "watch_min_gross_pf": WATCH_MIN_GROSS_PF,
            "note": "Classification is a research/production gate, not a buy/sell signal.",
        },
        "counts": counts,
        "rows": rows,
        "parse_errors": parse_errors,
    }


def to_markdown(result: dict[str, Any]) -> str:
    rows = result["rows"]
    counts = result["counts"]
    lines = [
        "# Timeframe Research Production Matrix",
        "",
        f"**PROMOTE:** {counts['PROMOTE']} · **WATCH:** {counts['WATCH']} · **REJECT:** {counts['REJECT']}",
        "",
        "Promotion gate: at least 50 OOS trades, moderate-cost PF ≥ 1.20, positive moderate-cost expectancy, robust sample, and no native cost-gate failure.",
        "",
        "| TF | System | Class | Trades | Gross PF | Light PF | Moderate PF | Mod. Exp (R) | Mod. DD (R) | Score |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            "| {period} | {system} | **{classification}** | {trades} | {gross_pf:.2f} | {light_pf:.2f} | {moderate_pf:.2f} | {moderate_expectancy:.3f} | {moderate_drawdown_r:.1f} | {score:.1f} |".format(**r)
        )

    promoted = [r for r in rows if r["classification"] == "PROMOTE"]
    lines.extend(["", "## Production candidates", ""])
    if promoted:
        for r in sorted(promoted, key=lambda x: -_num(x.get("score"))):
            lines.append(
                f"- **{r['period']} · {r['system']}** — moderate PF {r['moderate_pf']:.2f}, expectancy {r['moderate_expectancy']:.3f}R, {r['trades']} trades."
            )
    else:
        lines.append("No combination passed the hard production gate.")

    lines.extend(["", "## Incomplete / retry handling", ""])
    lines.append("Missing combinations do not receive a synthetic score. Re-run the specific job and regenerate this matrix.")
    if result.get("parse_errors"):
        lines.append(f"Parse errors: {len(result['parse_errors'])} (see JSON output).")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    result = aggregate(args.root)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_md.write_text(to_markdown(result), encoding="utf-8")
    print(f"rows={len(result['rows'])} promote={result['counts']['PROMOTE']} watch={result['counts']['WATCH']} reject={result['counts']['REJECT']}")


if __name__ == "__main__":
    main()
