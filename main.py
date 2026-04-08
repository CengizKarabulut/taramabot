"""
Taramabot Ana Modülü
Botun ana giriş noktasıdır. Tarama işlemlerini başlatır ve yönetir.
"""

import asyncio
import logging
import sys
from datetime import datetime

from config import (
    LOG_LEVEL, LOG_FORMAT, BIST_STOCKS, COMMODITIES, CRYPTO,
    EMOJI_INFO, EMOJI_ERROR, EMOJI_SUCCESS
)
from scanner import MarketScanner
from telegram_sender import get_telegram_sender
from scheduler import Scheduler, TZ_TURKEY

# Loglama konfigürasyonu
logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


async def main_scan_logic(market_type: str, period: str):
    """
    Ana tarama mantığını çalıştırır.
    Sembolleri tarar, sinyalleri işler ve Telegram'a sadece özet listeyi gönderir.
    """
    scanner = MarketScanner()
    telegram_sender = get_telegram_sender()

    logger.info(f"Tarama başlatılıyor: Pazar={market_type.upper()}, Periyot={period}")

    try:
        full_signals, smi_signals, rsi_signals, new_scan_signals, total_scanned = await scanner.scan_market(
            market_type=market_type,
            period=period,
            strategies=["smi_macd", "rsi", "new_scan"]
        )

        # 1. Özet listeyi gönder (Tüm sinyalleri içeren gruplanmış özet)
        if full_signals or smi_signals or rsi_signals or new_scan_signals:
            telegram_sender.send_grouped_summary(period, full_signals, smi_signals, rsi_signals, new_scan_signals)
            
            # 2. İstatistik Mesajı
            finish_msg = f"{EMOJI_SUCCESS} <b>Tarama Tamamlandı! ({period})</b>\n"
            finish_msg += f"⏱ {datetime.now(TZ_TURKEY).strftime('%Y-%m-%d %H:%M')}\n"
            finish_msg += f"🔍 Toplam {total_scanned} hisse tarandı.\n"
            finish_msg += f"📈 {len(new_scan_signals)} Özel, {len(full_signals)} Tam, {len(smi_signals)} SMI, {len(rsi_signals)} RSI sinyali bulundu."
            telegram_sender.send_message(finish_msg)

        else:
            telegram_sender.send_message(f"{EMOJI_INFO} <b>{period}</b> taramasında ({total_scanned} hisse) kriterlere uygun sinyal bulunamadı.")

    except Exception as e:
        logger.error(f"Ana tarama mantığında hata: {str(e)}", exc_info=True)
        telegram_sender.send_error(f"Tarama sırasında bir hata oluştu: {str(e)}")


async def run_multi_scan(market_type: str = "bist"):
    """Tüm zaman dilimlerini sırayla tara."""
    periods = ["15m", "1H", "4H", "1D", "1W", "1M"]
    logger.info(f"Çoklu tarama başlatılıyor: {periods}")
    for p in periods:
        await main_scan_logic(market_type, p)
        await asyncio.sleep(5)


async def run_bot():
    """
    Botu çalıştıran ana fonksiyon.
    Komut satırı argümanlarını işler veya zamanlayıcıyı başlatır.
    """
    if len(sys.argv) > 1:
        # Komut satırı argümanları ile tek seferlik çalıştırma
        command = sys.argv[1]
        if command == "scan":
            period = sys.argv[2].upper() if len(sys.argv) > 2 else "1D"
            market_type = sys.argv[3].lower() if len(sys.argv) > 3 else "bist"
            await main_scan_logic(market_type, period)
        elif command == "multi":
            market_type = sys.argv[2].lower() if len(sys.argv) > 2 else "bist"
            await run_multi_scan(market_type)
        else:
            logger.warning(f"Bilinmeyen komut: {command}")
            sys.exit(1)
    else:
        # Zamanlanmış çalıştırma (Varsayılan olarak tüm periyotları tara)
        scheduler = Scheduler()
        await scheduler.start(run_multi_scan, market_type="bist")


if __name__ == "__main__":
    asyncio.run(run_bot())
