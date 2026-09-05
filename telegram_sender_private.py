"""Private-facing Telegram renderer.

Production signal keys stay unchanged for compatibility, while every external
label is reduced to an opaque A-I code.  Strategy formulas and internal keys are
never renamed here, so scanner/backtest parity is unaffected.
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
            group["name"] = f"Sistem {alias}"
            group["description"] = "Ozel sistem"
            group["aliases"] = [key, old_code, alias]
            group["enabled"] = (
                not requested
                or key.lower() in requested
                or old_code.lower() in requested
                or alias.lower() in requested
            )
        return groups


_sender_instance = None


def get_telegram_sender() -> TelegramSender:
    global _sender_instance
    if _sender_instance is None:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID
        _sender_instance = TelegramSender(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID)
    return _sender_instance
