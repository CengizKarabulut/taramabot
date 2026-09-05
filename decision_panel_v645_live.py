"""Production adapter for Karar Paneli v6.4.5.

The rich technical commentary/reporting remains in decision_panel_v2, while
signal state, score and setup selection are replaced with the Pine-compatible
v6.4.5 layer.  The primary snapshot scanner now exposes KARAR beside A-I; this
standalone adapter stays available for audit/research artifacts without sending
a duplicate Telegram message in the same workflow workspace.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import decision_panel_v2 as _v2
from decision_panel_v2 import *  # noqa: F401,F403

from decision_panel_v645_signal import latest_decision
from exit_engine import build_exit_plan
from strategy_exit_profiles import get_exit_profile


_legacy_analyze_symbol = _v2.analyze_symbol
_legacy_build_telegram_report = _v2.build_telegram_report
INTEGRATED_DECISION_FLAG = Path("data/decision-integrated.flag")


def _profile_for_setup(setup: str):
    # Pullback is promoted globally in strategy_exit_profiles after the fixed
    # current-vs-proposed OOS comparison. Trend/Breakout deliberately remain on
    # their current profiles until stronger evidence exists.
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


def build_telegram_report(*args, **kwargs):
    text = _legacy_build_telegram_report(*args, **kwargs)
    text = text.replace("KARAR PANELİ v2 · GÜNLÜK TARAMA", "KARAR PANELİ v6.4.5 · GÜNLÜK TARAMA")
    return text


def _output_dir_from_argv() -> Path | None:
    try:
        pos = sys.argv.index("--output-dir")
        if pos + 1 < len(sys.argv) and sys.argv[pos + 1].strip():
            return Path(sys.argv[pos + 1])
    except ValueError:
        return None
    return None


def _rewrite_summary_metadata(output_dir: Path | None) -> None:
    if output_dir is None:
        return
    path = output_dir / "ozet.json"
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return
    payload["engine"] = "decision_panel_v6.4.5"
    payload["signal_model"] = "pine-compatible-v6.4.5"
    payload["risk_model"] = "setup-specific shared exit_engine"
    payload["integrated_scan_family"] = "KARAR"
    payload["notes"] = [
        "Sinyal/puan/setup katmanı TradingView v6.4.5 ile aynı no-lookahead v6.4.4 çekirdeğinden gelir.",
        "Canlı snapshot Telegram akışında KARAR, A-I ile aynı tarama paketinin onuncu ailesidir.",
        "Bu standalone çıktı yalnız audit/araştırma içindir; entegre paket gönderildiyse ayrı Telegram mesajı bastırılır.",
        "Pullback v6.4.5 filtresi RVOL<=2.0 ve fiyatın 20-bar tepesinin en az %95'inde olmasını ister.",
        "Pullback çıkışı uzun-tarih walk-forward/OOS doğrulamasıyla promote edilmiştir; Trend ve Breakout mevcut profillerini korur.",
        "Relatif güç, XU100 benchmark serisi snapshot'a ayrı eklenene kadar bilgi amaçlı beklemededir.",
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _suppress_duplicate_telegram_if_integrated() -> bool:
    force = os.getenv("ALLOW_SEPARATE_DECISION_TELEGRAM", "").strip().lower() in {"1", "true", "yes", "on"}
    if force or not INTEGRATED_DECISION_FLAG.is_file() or "--telegram" not in sys.argv:
        return False
    sys.argv = [arg for arg in sys.argv if arg not in {"--telegram", "--telegram-on-change"}]
    print("KARAR zaten A-I tarama paketinde gonderildi; ayri Karar Paneli Telegram mesaji bastirildi.")
    return True


def main() -> int:
    # decision_panel_v2.main resolves these functions from its own module globals,
    # so patch only the live hooks before invoking the existing reporting CLI.
    _v2.analyze_symbol = analyze_symbol
    _v2.build_telegram_report = build_telegram_report
    output_dir = _output_dir_from_argv()
    _suppress_duplicate_telegram_if_integrated()
    code = _v2.main()
    _rewrite_summary_metadata(output_dir)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
