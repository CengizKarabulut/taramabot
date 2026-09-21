"""Compatibility entry point for the risk-aware taramabot orchestration.

The public scanner surface is intentionally limited to 1H, 4H, 1D and 1W.
Legacy internals remain import-compatible, but cannot re-enable retired scan
periods through this entry point.
"""

from __future__ import annotations

import asyncio
import sys

from main_enhanced import *  # noqa: F401,F403


SCAN_PERIODS = ("1H", "4H", "1D", "1W")
_PERIOD_ALIASES = {
    "1h": "1H",
    "1H": "1H",
    "4h": "4H",
    "4H": "4H",
    "1d": "1D",
    "1D": "1D",
    "1w": "1W",
    "1W": "1W",
    "1wk": "1W",
}


def _normalize_public_period(value: str) -> str:
    try:
        return _PERIOD_ALIASES[value]
    except KeyError as exc:
        raise SystemExit(
            f"Desteklenmeyen tarama zaman dilimi: {value}. "
            "Yalnizca 1H, 4H, 1D ve 1W kullanilabilir."
        ) from exc


async def _run_core_multi_scan(
    market_type: str = "bist",
    period: str = "1D",
    use_state: bool = True,
):
    """Run only the four supported scan periods, shortest to longest."""
    all_results = []
    legacy.logger.info("Cekirdek coklu tarama baslatiliyor: %s", SCAN_PERIODS)
    for selected_period in SCAN_PERIODS:
        result = await legacy.main_scan_logic(
            market_type,
            selected_period,
            use_state=use_state,
        )
        if result:
            all_results.append(result)
        await asyncio.sleep(5)
    if all_results:
        legacy.send_final_summary(all_results)


# Keep the legacy module as a compatibility implementation while constraining
# every public multi-scan path to the current four-period policy.
legacy.PERIOD_ORDER = list(SCAN_PERIODS)
legacy.run_multi_scan = _run_core_multi_scan
run_multi_scan = _run_core_multi_scan


def _prepare_cli() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "scan":
        sys.argv[2] = _normalize_public_period(sys.argv[2])


if __name__ == "__main__":
    _prepare_cli()
    asyncio.run(run_bot())
