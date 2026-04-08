"""
Taramabot Telegram Gönderici Modülü
Telegram API üzerinden mesaj gönderir.
HTML formatında şık ve profesyonel mesajlar oluşturur.
"""

import logging
import requests
from typing import Optional, List
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, EMOJI_FULL_SIGNAL, EMOJI_SMI_SIGNAL, EMOJI_RSI_SIGNAL, EMOJI_NEW_SCAN_SIGNAL, EMOJI_RSI_MACD_SIGNAL, EMOJI_EMA_SIGNAL

logger = logging.getLogger(__name__)


class TelegramSender:
    """Telegram mesaj gönderici."""
    
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
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
            
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info(f"Telegram mesajı gönderildi: {text[:50]}...")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram mesaj gönderme hatası: {str(e)}")
            return False
    
    def send_grouped_summary(
        self,
        period: str,
        full_signals: List[dict],
        smi_signals: List[dict],
        rsi_signals: List[dict],
        new_scan_signals: List[dict] = None,
        rsi_macd_signals: List[dict] = None,
        ema_signals: List[dict] = None
    ) -> bool:
        """
        Tarama sonuçlarını stratejiye göre gruplayarak şık bir özet mesajı gönderir.
        """
        if new_scan_signals is None:
            new_scan_signals = []
        if rsi_macd_signals is None:
            rsi_macd_signals = []
        if ema_signals is None:
            ema_signals = []
            
        period_names = {
            "15m": "15 DAKİKA",
            "1H": "1 SAAT",
            "4H": "4 SAAT",
            "1D": "GÜNLÜK",
            "1W": "HAFTALIK",
            "1M": "AYLIK"
        }
        p_name = period_names.get(period, period)
        
        header = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        header += f"<b>🔍 {p_name} TARAMA ÖZETİ</b>\n"
        header += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        
        body = ""
        
        # 1. Strateji: EMA Dizilimi + Bağ Hacim
        if ema_signals:
            body += f"<b>{EMOJI_EMA_SIGNAL} EMA DİZİLİMİ + HACİM SİNYALLERİ</b>\n"
            body += f"<i>EMA(5,8,13) Kesişimi + EMA(21,55,200) Altı</i>\n"
            for s in ema_signals:
                c_str = f"🟢 +{s['change']:.2f}%" if s['change'] >= 0 else f"🔴 {s['change']:.2f}%"
                body += f"• <code>{s['symbol']:<7}</code> | {c_str} | {s['close']:.2f}\n"
            body += "\n"

        # 2. Strateji: RSI + MACD + Hacim
        if rsi_macd_signals:
            body += f"<b>{EMOJI_RSI_MACD_SIGNAL} RSI + MACD + HACİM SİNYALLERİ</b>\n"
            body += f"<i>RSI(14) 50 Kes. + RSI < 70 + MACD Kes.</i>\n"
            for s in rsi_macd_signals:
                c_str = f"🟢 +{s['change']:.2f}%" if s['change'] >= 0 else f"🔴 {s['change']:.2f}%"
                body += f"• <code>{s['symbol']:<7}</code> | {c_str} | {s['close']:.2f}\n"
            body += "\n"

        # 2. Strateji: Yeni Tarama (Scanner 3)
        if new_scan_signals:
            body += f"<b>{EMOJI_NEW_SCAN_SIGNAL} ÖZEL TARAMA SİNYALLERİ</b>\n"
            body += f"<i>SMA Dizilimi + MACD Pozitif</i>\n"
            for s in new_scan_signals:
                c_str = f"🟢 +{s['change']:.2f}%" if s['change'] >= 0 else f"🔴 {s['change']:.2f}%"
                body += f"• <code>{s['symbol']:<7}</code> | {c_str} | {s['close']:.2f}\n"
            body += "\n"

        # 2. Strateji: Tam Alım (SMI + MA200 + Hacim)
        if full_signals:
            body += f"<b>{EMOJI_FULL_SIGNAL} TAM ALIM SİNYALLERİ (GÜÇLÜ)</b>\n"
            body += f"<i>SMI Kesişimi + MA200 Üstü + Hacim</i>\n"
            for s in full_signals:
                c_str = f"🟢 +{s['change']:.2f}%" if s['change'] >= 0 else f"🔴 {s['change']:.2f}%"
                body += f"• <code>{s['symbol']:<7}</code> | {c_str} | {s['close']:.2f}\n"
            body += "\n"

        # 3. Strateji: SMI/MACD Alım
        if smi_signals:
            body += f"<b>{EMOJI_SMI_SIGNAL} SMI/MACD ALIM SİNYALLERİ</b>\n"
            body += f"<i>SMI/MACD Pozitif Kesişim</i>\n"
            for s in smi_signals:
                c_str = f"🟢 +{s['change']:.2f}%" if s['change'] >= 0 else f"🔴 {s['change']:.2f}%"
                body += f"• <code>{s['symbol']:<7}</code> | {c_str} | {s['close']:.2f}\n"
            body += "\n"

        # 4. Strateji: RSI Alım
        if rsi_signals:
            body += f"<b>{EMOJI_RSI_SIGNAL} RSI ALIM SİNYALLERİ</b>\n"
            body += f"<i>RSI > 60 + RSI 50 Kesişimi</i>\n"
            for s in rsi_signals:
                c_str = f"🟢 +{s['change']:.2f}%" if s['change'] >= 0 else f"🔴 {s['change']:.2f}%"
                body += f"• <code>{s['symbol']:<7}</code> | {c_str} | {s['close']:.2f}\n"
            body += "\n"

        # Herhangi bir sinyal varsa mesajı gönder
        if body:
            footer = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>"
            return self.send_message(header + body + footer)
        else:
            # Sinyal yoksa bilgilendirme mesajı gönder
            body = f"<b>ℹ️ SİNYAL BULUNAMADI</b>\n\n<i>{p_name} periyodunda kriterlere uygun hisse tespit edilemedi.</i>\n"
            footer = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>"
            return self.send_message(header + body + footer)
    
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
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        _sender_instance = TelegramSender(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    return _sender_instance
