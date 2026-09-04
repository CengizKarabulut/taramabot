"""Production adapter for Karar Paneli v6.4.5.

The rich technical commentary/reporting remains in decision_panel_v2, while
signal state, score and setup selection are replaced with the Pine-compatible
v6.4.5 layer.  This keeps Telegram/report ergonomics without allowing the live
scanner and TradingView indicator to silently use different entry logic.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import decision_panel_v2 as _v2
from decision_panel_v2 import *  # noqa: F401,F403

from decision_panel_v645_signal import latest_decision
from exit_engine import build_exit_plan
from strategy_exit_profiles import get_exit_profile


_legacy_analyze_symbol = _v2.analyze_symbol


def _pullback_profile():
    """OOS-validated Pullback exit profile promoted for v6.4.5."""
    base = get_exit_profile("decision_panel", setup="PULLBACK")
    return replace(
        base,
        stop_mode="hybrid_wide",
        atr_mult=1.25,
        tp1_r=0.8,
        tp2_r=1.5,
        tp3_r=2.4,
        trailing_atr_mult=1.8,
    )


def _profile_for_setup(setup: str):
    if setup == "PULLBACK":
        return _pullback_profile()
    # TREND DEVAMI keeps the existing profile.  The proposed swing/0.8-1.5-2.4
    # profile improved win-rate but failed our PF quality floor in final OOS.
    return get_exit_profile("decision_panel", setup=setup)


def analyze_symbol(symbol: str, history, settings, live_bar: bool) -> dict[str, Any]:
    result = _legacy_analyze_symbol(symbol, history, settings, live_bar)
    try:
        pine = latest_decision(history, min_score=int(settings.min_score))
    except Exception as exc:
        result["signal_model"] = "v6.4.5-fallback-v2"
        result["signal_model_error"] = str(exc)
        return result

    active_setup = pine["active_setup"]
    new_setup = pine["new_setup"]
    liquidity_ok = bool(result.get("liquidity_ok", False))
    entry = bool(new_setup) and liquidity_ok

    result["score"] = int(pine["score"])
    result["setup"] = active_setup or "-"
    result["entry"] = entry
    result["signal_model"] = "pine-compatible-v6.4.5"
    result["new_setup"] = new_setup or "-"
    result["pine_rvol"] = round(float(pine["rvol"]), 3)
    result["pct_20_high"] = round(float(pine["pct20"]), 2)
    result["pullback_guard"] = bool(pine["pullback_guard"])

    if active_setup:
        try:
            profile = _profile_for_setup(active_setup)
            plan = build_exit_plan(
                history,
                "decision_panel",
                setup=active_setup,
                profile=profile,
            )
        except Exception:
            plan = None
        result["exit_plan"] = plan
        result["stop"] = round(plan["stop"], 4) if plan else math.nan
        result["tp1"] = round(plan["tp1"], 4) if plan else math.nan
        result["tp2"] = round(plan["tp2"], 4) if plan else math.nan
        result["tp3"] = round(plan["tp3"], 4) if plan else math.nan
        result["stop_pct"] = round(plan["stop_pct"], 2) if plan else math.nan
        result["tp1_pct"] = round(plan["tp1_pct"], 2) if plan else math.nan
        result["tp2_pct"] = round(plan["tp2_pct"], 2) if plan else math.nan
        result["tp3_pct"] = round(plan["tp3_pct"], 2) if plan else math.nan

    technical = str(result.get("technical_confirmation", "ORTA"))
    if entry:
        suffix = "TEYİTLİ" if technical == "GÜÇLÜ" else "KARIŞIK" if technical == "KARIŞIK" else "ORTA TEYİT"
        result["status"] = f"{new_setup} · {suffix}"
        if live_bar:
            result["status"] += " · KAPANIŞ BEKLE"
    elif active_setup:
        result["status"] = f"{active_setup} · AKTİF / YENİ SİNYAL DEĞİL"
    elif bool(pine.get("pullback_base")) and not bool(pine.get("pullback_guard")):
        result["status"] = "PULLBACK ADAYI · v6.4.5 FİLTRESİNDE ELENDİ"

    return result


def main() -> int:
    # decision_panel_v2.main resolves analyze_symbol from its own module globals,
    # so patch that single hook before invoking the existing reporting CLI.
    _v2.analyze_symbol = analyze_symbol
    return _v2.main()


if __name__ == "__main__":
    raise SystemExit(main())
