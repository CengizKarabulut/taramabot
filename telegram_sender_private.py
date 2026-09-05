"""Private-facing Telegram renderer.

Production signal keys stay unchanged for compatibility, while every external
A-I label is reduced to an opaque code.  The Pine-compatible decision signal is
rendered as a tenth group named KARAR.
"""

from __future__ import annotations

from typing import Optional, List

from telegram_sender_enhanced import *  # noqa: F401,F403
from telegram_sender_enhanced import TelegramSender as EnhancedTelegramSender


# Stable external codes.  Keep this order consistent with the TradingView panel.
_DISPLAY_BY_KEY = {
    "macd_cross": "A",
    "h8": "B",
    "i9": "C",
    "ema": "D",
    "rsi_macd": "E",
    "new_scan": "F",
    "full": "G",
    "smi": "H",
    "rsi": "I",
}


class TelegramSender(EnhancedTelegramSender):
    def _strategy_groups(
        self,
        full_signals: List[dict],
        smi_signals: List[dict],
        rsi_signals: List[dict],
        new_scan_signals: List[dict],
        rsi_macd_signals: List[dict],
        ema_signals: List[dict],
        macd_cross_signals: List[dict],
        h8_signals: List[dict],
        i9_signals: List[dict],
        enabled_strategy_codes: Optional[List[str]] = None,
    ) -> List[dict]:
        # Build the exact same production groups, then replace presentation only.
        groups = super()._strategy_groups(
            full_signals,
            smi_signals,
            rsi_signals,
            new_scan_signals,
            rsi_macd_signals,
            ema_signals,
            macd_cross_signals,
            h8_signals,
            i9_signals,
            enabled_strategy_codes=None,
        )

        requested = {str(value).strip().lower() for value in (enabled_strategy_codes or [])}
        for group in groups:
            key = str(group.get("key", ""))
            old_code = str(group.get("code", ""))
            alias = _DISPLAY_BY_KEY.get(key, "?")

            group["code"] = alias
            group["name"] = alias
            group["description"] = "Ozel sistem"
            group["aliases"] = [key, old_code, alias]
            group["enabled"] = (
                not requested
                or key.lower() in requested
                or old_code.lower() in requested
                or alias.lower() in requested
            )
        return groups

    def send_grouped_summary(
        self,
        period: str,
        full_signals: List[dict],
        smi_signals: List[dict],
        rsi_signals: List[dict],
        new_scan_signals: List[dict] = None,
        rsi_macd_signals: List[dict] = None,
        ema_signals: List[dict] = None,
        macd_cross_signals: List[dict] = None,
        h8_signals: List[dict] = None,
        i9_signals: List[dict] = None,
        decision_signals: List[dict] = None,
        market_type: Optional[str] = None,
        total_scanned: Optional[int] = None,
        enabled_strategy_codes: Optional[List[str]] = None,
        send_images: bool = True,
    ) -> bool:
        """Render A-I and KARAR in one scan package for the same timeframe."""
        groups = self._strategy_groups(
            full_signals=full_signals or [],
            smi_signals=smi_signals or [],
            rsi_signals=rsi_signals or [],
            new_scan_signals=new_scan_signals or [],
            rsi_macd_signals=rsi_macd_signals or [],
            ema_signals=ema_signals or [],
            macd_cross_signals=macd_cross_signals or [],
            h8_signals=h8_signals or [],
            i9_signals=i9_signals or [],
            enabled_strategy_codes=enabled_strategy_codes,
        )

        requested = {str(value).strip().lower() for value in (enabled_strategy_codes or [])}
        karar_enabled = not requested or "karar" in requested or "decision" in requested
        groups.append(
            {
                "key": "decision",
                "signals": decision_signals or [],
                "emoji": "🧭",
                "code": "KARAR",
                "name": "KARAR",
                "description": "Pine-compatible v6.4.5 karar sinyali",
                "color": "#475569",
                "aliases": ["decision", "karar"],
                "enabled": karar_enabled,
            }
        )

        return self.send_scan_visuals(
            period=period,
            strategies=groups,
            market_type=market_type,
            total_scanned=total_scanned,
        )


_sender_instance = None


def get_telegram_sender() -> TelegramSender:
    global _sender_instance
    if _sender_instance is None:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID
        _sender_instance = TelegramSender(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID)
    return _sender_instance
