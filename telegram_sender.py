"""
Taramabot Telegram Gönderici Modülü
Telegram API üzerinden mesaj ve fotoğraf gönderir.
HTML formatında şık ve profesyonel mesajlar oluşturur.
"""

import logging
import requests
from typing import Optional, List
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

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
        
        Args:
            text: Mesaj metni
            parse_mode: HTML veya Markdown
            
        Returns:
            Başarılı olup olmadığı
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
    
    def send_photo(self, photo_path: str, caption: str = "") -> bool:
        """
        Telegram'a fotoğraf gönder.
        
        Args:
            photo_path: Fotoğraf dosya yolu
            caption: Fotoğraf başlığı (HTML formatında)
            
        Returns:
            Başarılı olup olmadığı
        """
        if not self.bot_token or not self.chat_id:
            logger.warning(f"Telegram konfigürasyonu eksik. Fotoğraf: {photo_path}")
            return False
        
        try:
            url = f"{self.base_url}/sendPhoto"
            
            with open(photo_path, 'rb') as f:
                files = {"photo": f}
                data = {
                    "chat_id": self.chat_id,
                    "caption": caption,
                    "parse_mode": "HTML"
                }
                
                response = requests.post(url, data=data, files=files, timeout=30)
                response.raise_for_status()
            
            logger.info(f"Telegram fotoğrafı gönderildi: {photo_path}")
            return True
            
        except FileNotFoundError:
            logger.error(f"Fotoğraf dosyası bulunamadı: {photo_path}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram fotoğraf gönderme hatası: {str(e)}")
            return False
    
    def send_signal_summary(
        self,
        period: str,
        full_signals: List[dict],
        smi_signals: List[dict]
    ) -> bool:
        """
        Tarama sonuçlarının özet mesajını gönder.
        
        Args:
            period: Zaman dilimi (4H, 1D, 1W)
            full_signals: Tam alım sinyalleri
            smi_signals: SMI alım sinyalleri
            
        Returns:
            Başarılı olup olmadığı
        """
        message = f"<b>📊 {period} TARAMA SONUÇLARI</b>\n\n"
        
        if full_signals:
            message += "<b>🚀 TAM ALIM SİNYALLERİ:</b>\n"
            for signal in full_signals:
                change_str = f"<b>+{signal['change']:.2f}%</b>" if signal['change'] >= 0 else f"<b>{signal['change']:.2f}%</b>"
                message += f"  • <b>{signal['symbol']}</b> | {change_str} | Fiyat: {signal['close']:.2f}\n"
            message += "\n"
        
        if smi_signals:
            message += "<b>🟡 SMI ALIM SİNYALLERİ:</b>\n"
            for signal in smi_signals:
                change_str = f"<b>+{signal['change']:.2f}%</b>" if signal['change'] >= 0 else f"<b>{signal['change']:.2f}%</b>"
                message += f"  • <b>{signal['symbol']}</b> | {change_str} | Fiyat: {signal['close']:.2f}\n"
        
        if not full_signals and not smi_signals:
            message = f"<b>ℹ️ {period} TARAMASINDA SİNYAL BULUNAMADI</b>\n\nSonraki taramaya kadar lütfen bekleyiniz."
        
        return self.send_message(message)
    
    def send_signal_detail(
        self,
        symbol: str,
        exchange: str,
        period: str,
        close: float,
        change: float,
        signal_type: str,
        details: dict
    ) -> bool:
        """
        Sinyal detaylarını gönder.
        
        Args:
            symbol: Sembol
            exchange: Borsa
            period: Zaman dilimi
            close: Kapanış fiyatı
            change: Fiyat değişimi (%)
            signal_type: Sinyal türü (full_buy, smi_buy, rsi_buy)
            details: İndikatör detayları
            
        Returns:
            Başarılı olup olmadığı
        """
        change_str = f"<b>+{change:.2f}%</b>" if change >= 0 else f"<b>{change:.2f}%</b>"
        
        message = f"<b>📈 {symbol} ({exchange})</b>\n"
        message += f"<b>Zaman Dilimi:</b> {period}\n"
        message += f"<b>Fiyat:</b> {close:.2f}\n"
        message += f"<b>Değişim:</b> {change_str}\n\n"
        
        if signal_type == "full_buy":
            message += "<b>🚀 TAM ALIM SİNYALİ</b>\n"
            message += "Tüm şartlar sağlanmıştır:\n"
            message += f"  • SMI/MACD Kesişimi ✓\n"
            message += f"  • 200 MA Üstü ✓\n"
            message += f"  • Hacim Artışı ✓\n"
        elif signal_type == "smi_buy":
            message += "<b>🟡 SMI ALIM SİNYALİ</b>\n"
            message += "SMI/MACD şartları sağlanmıştır:\n"
            message += f"  • SMI/MACD Kesişimi ✓\n"
        elif signal_type == "rsi_buy":
            message += "<b>🔵 RSI ALIM SİNYALİ</b>\n"
            message += "RSI şartları sağlanmıştır:\n"
            message += f"  • RSI > 60 ✓\n"
            message += f"  • RSI 50 Kesişimi ✓\n"
            message += f"  • Hacim Artışı ✓\n"
        
        message += f"\n<b>İndikatörler:</b>\n"
        for key, value in details.items():
            if isinstance(value, float):
                message += f"  • {key}: {value:.2f}\n"
            elif isinstance(value, bool):
                message += f"  • {key}: {'✓' if value else '✗'}\n"
        
        return self.send_message(message)
    
    def send_error(self, error_msg: str) -> bool:
        """
        Hata mesajı gönder.
        
        Args:
            error_msg: Hata mesajı
            
        Returns:
            Başarılı olup olmadığı
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
