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
    EMOJI_FULL_SIGNAL, EMOJI_SMI_SIGNAL, EMOJI_INFO, EMOJI_ERROR, EMOJI_SUCCESS
)
from scanner import MarketScanner
from telegram_sender import get_telegram_sender
from screenshot import take_screenshot
from scheduler import Scheduler, TZ_TURKEY

# Loglama konfigürasyonu
logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


async def main_scan_logic(market_type: str, period: str):
    """
    Ana tarama mantığını çalıştırır.
    Sembolleri tarar, sinyalleri işler ve Telegram'a gönderir.
    """
    scanner = MarketScanner()
    telegram_sender = get_telegram_sender()

    logger.info(f"Tarama başlatılıyor: Pazar={market_type.upper()}, Periyot={period}")

    try:
        full_signals, smi_signals, rsi_signals, total_scanned = await scanner.scan_market(
            market_type=market_type,
            period=period,
            strategies=["smi_macd", "rsi"]
        )

        # 1. Özet listeyi gönder (Tüm sinyalleri içeren gruplanmış özet)
        if full_signals or smi_signals or rsi_signals:
            telegram_sender.send_grouped_summary(period, full_signals, smi_signals, rsi_signals)
            await asyncio.sleep(3)

            # 2. Grafiklerin Gönderimi (Önce Tam Alım, Sonra SMI, Sonra RSI)
            all_signals = []
            for s in full_signals: all_signals.append({**s, 'signal_type': 'full_buy'})
            for s in smi_signals: all_signals.append({**s, 'signal_type': 'smi_buy'})
            for s in rsi_signals: all_signals.append({**s, 'signal_type': 'rsi_buy'})

            for signal_data in all_signals:
                symbol = signal_data['symbol']
                exchange = signal_data['exchange']
                period_str = signal_data['period']
                close_price = signal_data['close']
                change_percent = signal_data['change']
                signal_type = signal_data['signal_type']

                # Ekran görüntüsü al ve gönder (TradingView üzerinden)
                screenshot_path = await take_screenshot(symbol, exchange, period_str)
                if screenshot_path:
                    # Sinyal türüne göre emoji seç
                    s_emoji = "🚀" if signal_type == "full_buy" else "🟡" if signal_type == "smi_buy" else "🔵"
                    s_name = "TAM ALIM" if signal_type == "full_buy" else "SMI ALIM" if signal_type == "smi_buy" else "RSI ALIM"
                    
                    caption = f"<b>{s_emoji} {s_name} | #{symbol} ({period_str})</b>\n"
                    caption += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
                    caption += f"💰 <b>Fiyat:</b> {close_price:.2f}\n"
                    caption += f"📈 <b>Değişim:</b> {'+' if change_percent >= 0 else ''}{change_percent:.2f}%\n"
                    caption += f"⏱ <b>Tarih:</b> {datetime.now(TZ_TURKEY).strftime('%d.%m.%Y %H:%M')}"
                    
                    telegram_sender.send_photo(screenshot_path, caption=caption)
                    await asyncio.sleep(3.0)
                else:
                    logger.warning(f"Grafik alınamadı: {symbol} ({period_str})")

            # 3. İstatistik Mesajı
            finish_msg = f"{EMOJI_SUCCESS} <b>Tarama Tamamlandı! ({period})</b>\n"
            finish_msg += f"⏱ {datetime.now(TZ_TURKEY).strftime('%Y-%m-%d %H:%M')}\n"
            finish_msg += f"🔍 Toplam {total_scanned} hisse tarandı.\n"
            finish_msg += f"📈 {len(full_signals)} Tam, {len(smi_signals)} SMI, {len(rsi_signals)} RSI sinyali bulundu."
            telegram_sender.send_message(finish_msg)

        else:
            telegram_sender.send_message(f"{EMOJI_INFO} <b>{period}</b> taramasında ({total_scanned} hisse) kriterlere uygun sinyal bulunamadı.")

    except Exception as e:
        logger.error(f"Ana tarama mantığında hata: {str(e)}", exc_info=True)
        telegram_sender.send_error(f"Tarama sırasında bir hata oluştu: {str(e)}")


async def run_bot():
    """
    Botu çalıştıran ana fonksiyon.
    Komut satırı argümanlarını işler veya zamanlayıcıyı başlatır.
    """
    if len(sys.argv) > 1:
        # Komut satırı argümanları ile tek seferlik çalıştırma
        # Örnek: python main.py scan 1D bist
        command = sys.argv[1]
        if command == "scan":
            period = sys.argv[2].upper() if len(sys.argv) > 2 else "1D"
            market_type = sys.argv[3].lower() if len(sys.argv) > 3 else "bist"
            await main_scan_logic(market_type, period)
        elif command == "multi":
            # Tüm zaman dilimlerini tara (15m, 1H, 4H, 1D, 1W, 1M)
            periods = ["15m", "1H", "4H", "1D", "1W", "1M"]
            for p in periods:
                await main_scan_logic("bist", p)
                await asyncio.sleep(5)
        else:
            logger.warning(f"Bilinmeyen komut: {command}")
            sys.exit(1)
    else:
        # Zamanlanmış çalıştırma
        scheduler = Scheduler()
        # Varsayılan olarak BIST 1D taramasını zamanla
        await scheduler.start(main_scan_logic, market_type="bist", period="1D")


if __name__ == "__main__":
    asyncio.run(run_bot())
