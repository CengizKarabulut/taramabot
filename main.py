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

        # Özet mesaj gönder
        if full_signals or smi_signals or rsi_signals:
            # SMI/MACD özetini gönder
            telegram_sender.send_signal_summary(period, full_signals, smi_signals)
            await asyncio.sleep(2)

            # SMI sinyalleri için detaylı özet (RSI gibi)
            if smi_signals:
                smi_summary_msg = f"<b>📊 {period} SMI ALIM SİNYALLERİ</b>\n\n"
                for s in smi_signals:
                    change_str = f"<b>+{s['change']:.2f}%</b>" if s['change'] >= 0 else f"<b>{s['change']:.2f}%</b>"
                    smi_summary_msg += f"  • <b>{s['symbol']}</b> | {change_str} | Fiyat: {s['close']:.2f}\n"
                telegram_sender.send_message(smi_summary_msg)
                await asyncio.sleep(2)

            # RSI sinyallerini ayrı gönder
            if rsi_signals:
                rsi_summary_msg = f"<b>📊 {period} RSI ALIM SİNYALLERİ</b>\n\n"
                for r in rsi_signals:
                    change_str = f"<b>+{r['change']:.2f}%</b>" if r['change'] >= 0 else f"<b>{r['change']:.2f}%</b>"
                    rsi_summary_msg += f"  • <b>{r['symbol']}</b> | {change_str} | Fiyat: {r['close']:.2f}\n"
                telegram_sender.send_message(rsi_summary_msg)
                await asyncio.sleep(2)

            # Detaylı sinyaller ve grafikler
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

                # İndikatör detaylarını al
                details = {}
                if signal_type == 'full_buy' or signal_type == 'smi_buy':
                    details = signal_data['signals']['smi_macd']['details']
                elif signal_type == 'rsi_buy':
                    details = signal_data['signals']['rsi']['details']

                # Detay mesajı gönder
                telegram_sender.send_signal_detail(
                    symbol, exchange, period_str, close_price, change_percent, signal_type, details
                )
                await asyncio.sleep(2)

                # Ekran görüntüsü al ve gönder (TradingView üzerinden)
                screenshot_path = await take_screenshot(symbol, exchange, period_str)
                if screenshot_path:
                    caption = f"#{symbol} ({period_str}) | Fiyat: {close_price:.2f}"
                    telegram_sender.send_photo(screenshot_path, caption=caption)
                    await asyncio.sleep(2.0)
                else:
                    telegram_sender.send_message(f"{EMOJI_ERROR} Grafik alınamadı: {symbol} ({period_str})")

            # Tarama tamamlandı mesajı ve istatistikler
            finish_msg = f"{EMOJI_SUCCESS} <b>Tarama Tamamlandı! ({period})</b>\n"
            finish_msg += f"⏱ {datetime.now(TZ_TURKEY).strftime('%Y-%m-%d %H:%M')}\n"
            finish_msg += f"🔍 Toplam {total_scanned} hisse tarandı.\n"
            finish_msg += f"📈 {len(full_signals)} Tam, {len(smi_signals)} SMI, {len(rsi_signals)} RSI sinyali bulundu."
            telegram_sender.send_message(finish_msg)

        else:
            telegram_sender.send_message(f"{EMOJI_INFO} {period} taramasında ({total_scanned} hisse) sinyal bulunamadı.")

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
            # Çoklu tarama (15m, 1H, 4H, 1D, 1W, 1M)
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
