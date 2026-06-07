"""
Taramabot Telegram Gönderici Modülü
Telegram API üzerinden mesaj gönderir.
HTML formatında şık ve profesyonel mesajlar oluşturur.
"""

import logging
import requests
import time
from typing import Optional, List
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID,
    EMOJI_FULL_SIGNAL, EMOJI_SMI_SIGNAL, EMOJI_RSI_SIGNAL, 
    EMOJI_NEW_SCAN_SIGNAL, EMOJI_RSI_MACD_SIGNAL, EMOJI_EMA_SIGNAL
)

logger = logging.getLogger(__name__)


class TelegramSender:
    """Telegram mesaj gönderici."""
    
    def __init__(self, bot_token: str, chat_id: str, thread_id: Optional[str] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
    
    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Telegram'a mesaj gönder.
        """
        if not self.bot_token or not self.chat_id:
            logger.warning(f"Telegram konfigürasyonu eksik. Mesaj: {text[:50]}...")
            return False
        
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True
            }
            
            if self.thread_id:
                payload["message_thread_id"] = self.thread_id
            
            response = requests.post(url, json=payload, timeout=15)
            if response.status_code != 200:
                logger.error(f"Telegram API Hatası ({response.status_code}): {response.text}")
                # Eğer HTML hatası ise düz metin olarak tekrar dene
                if "can't parse entities" in response.text:
                    logger.info("HTML ayrıştırma hatası, düz metin olarak tekrar deneniyor...")
                    payload["parse_mode"] = None
                    # HTML taglerini temizle (basitçe)
                    import re
                    payload["text"] = re.sub('<[^<]+?>', '', text)
                    response = requests.post(url, json=payload, timeout=15)
            
            response.raise_for_status()
            logger.info(f"Telegram mesajı gönderildi: {text[:50]}...")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram mesaj gönderme hatası: {str(e)}")
            return False

    def _format_signal_list(self, signals: List[dict]) -> str:
        """Sinyal listesini formatla."""
        lines = []
        for s in signals:
            c_str = f"🟢 +{s['change']:.2f}%" if s['change'] >= 0 else f"🔴 {s['change']:.2f}%"
            lines.append(f"• <code>{s['symbol']:<7}</code> | {c_str} | {s['close']:.2f}")
        return "\n".join(lines)

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
        i9_signals: List[dict] = None
    ) -> bool:
        """
        Tarama sonuçlarını stratejiye göre gruplayarak parçalı mesajlar halinde gönderir.
        Bu yöntem Telegram karakter sınırına (4096) takılmayı önler.
        """
        new_scan_signals = new_scan_signals or []
        rsi_macd_signals = rsi_macd_signals or []
        ema_signals = ema_signals or []
        macd_cross_signals = macd_cross_signals or []
        h8_signals = h8_signals or []
        i9_signals = i9_signals or []
            
        period_names = {
            "15m": "15 DAKİKA", "15M": "15 DAKİKA",
            "1h": "1 SAAT", "1H": "1 SAAT",
            "4h": "4 SAAT", "4H": "4 SAAT",
            "1d": "GÜNLÜK", "1D": "GÜNLÜK",
            "1w": "HAFTALIK", "1W": "HAFTALIK",
            "1m": "AYLIK", "1M": "AYLIK"
        }
        p_name = period_names.get(period, period)
        
        header = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        header += f"<b>🔍 {p_name}</b>\n"
        header += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        
        # Tüm sinyalleri kontrol et
        all_empty = not any([full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals, ema_signals, macd_cross_signals, h8_signals, i9_signals])
        
        if all_empty:
            empty_msg = header + f"\n<b>ℹ️</b>\n\n<i>Sinyal yok.</i>\n"
            empty_msg += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>"
            return self.send_message(empty_msg)

        # Başlık mesajını gönder
        self.send_message(header)
        time.sleep(0.5)

        # Stratejileri tanımla
        strategies = [
            (macd_cross_signals, "🟣", "S1", ""),
            (h8_signals, "🔷", "H8", ""),
            (i9_signals, "🔹", "I9", ""),
            (ema_signals, EMOJI_EMA_SIGNAL, "S2", ""),
            (rsi_macd_signals, EMOJI_RSI_MACD_SIGNAL, "S3", ""),
            (new_scan_signals, EMOJI_NEW_SCAN_SIGNAL, "S4", ""),
            (full_signals, EMOJI_FULL_SIGNAL, "S5", ""),
            (smi_signals, EMOJI_SMI_SIGNAL, "S6", ""),
            (rsi_signals, EMOJI_RSI_SIGNAL, "S7", "")
        ]

        for signals, emoji, title, desc in strategies:
            if not signals:
                continue
            
            # Her strateji için mesaj oluştur
            strat_header = f"<b>{emoji} {title}</b>\n"
            
            # Sinyalleri 30'arlı gruplara böl (Mesaj boyutu kontrolü için)
            for i in range(0, len(signals), 30):
                chunk = signals[i:i+30]
                body = self._format_signal_list(chunk)
                
                msg = strat_header + body
                if i + 30 < len(signals):
                    msg += "\n\n<b>(Devamı...)</b>"
                
                self.send_message(msg)
                time.sleep(1) # Telegram rate limit koruması

        # Kapanış çizgisi
        self.send_message(f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>")
        return True
    
    def send_error(self, error_msg: str) -> bool:
        """
        Hata mesajı gönder.
        """
        message = f"<b>❌ HATA</b>\n\n{error_msg}"
        return self.send_message(message)


# Global singleton instance
_sender_instance: Optional[TelegramSender] = None


def get_telegram_sender() -> TelegramSender:
    """Singleton Telegram gönderici'yi al."""
    global _sender_instance
    if _sender_instance is None:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID
        _sender_instance = TelegramSender(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID)
    return _sender_instance
