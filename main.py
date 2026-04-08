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
        full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals, total_scanned = await scanner.scan_market(
            market_type=market_type,
            period=period,
            strategies=["smi_macd", "rsi", "new_scan", "rsi_macd"]
        )

        # 1. Özet listeyi gönder (Tüm sinyalleri içeren gruplanmış özet)
        if full_signals or smi_signals or rsi_signals or new_scan_signals or rsi_macd_signals:
            telegram_sender.send_grouped_summary(period, full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals)
            
            # 2. İstatistik Mesajı
            finish_msg = f"{EMOJI_SUCCESS} <b>Tarama Tamamlandı! ({period})</b>\n"
            finish_msg += f"⏱ {datetime.now(TZ_TURKEY).strftime('%Y-%m-%d %H:%M')}\n"
            finish_msg += f"🔍 Toplam {total_scanned} hisse tarandı.\n"
            finish_msg += f"📈 {len(rsi_macd_signals)} RSI+MACD, {len(new_scan_signals)} Özel, {len(full_signals)} Tam, {len(smi_signals)} SMI, {len(rsi_signals)} RSI sinyali bulundu."
            telegram_sender.send_message(finish_msg)

        else:
            telegram_sender.send_message(f"{EMOJI_INFO} <b>{period}</b> taramasında ({total_scanned} hisse) kriterlere uygun sinyal bulunamadı.")

        return {
            "period": period,
            "full": full_signals,
            "smi": smi_signals,
            "rsi": rsi_signals,
            "new": new_scan_signals,
            "rsi_macd": rsi_macd_signals
        }

    except Exception as e:
        logger.error(f"Ana tarama mantığında hata: {str(e)}", exc_info=True)
        telegram_sender.send_error(f"Tarama sırasında bir hata oluştu: {str(e)}")


async def run_multi_scan(market_type: str = "bist"):
    """Tüm zaman dilimlerini sırayla tara."""
    periods = ["15m", "1H", "4H", "1D", "1W", "1M"]
    logger.info(f"Çoklu tarama başlatılıyor: {periods}")
    
    all_results = []
    for p in periods:
        res = await main_scan_logic(market_type, p)
        if res:
            all_results.append(res)
        await asyncio.sleep(5)
    
    # Tüm taramalar bitince hisse bazlı özet raporu oluştur
    if all_results:
        send_final_summary(all_results)


def send_final_summary(all_results: list):
    """
    Tüm taramalar bittikten sonra hisse bazlı zaman dilimi özetini gönderir.
    Örnek: X hissesi 1W için şu şu taramalarda var...
    """
    telegram_sender = get_telegram_sender()
    symbol_map = {} # symbol -> { period -> [strategies] }
    
    strategy_names = {
        "full": "Tam Alım",
        "smi": "SMI/MACD",
        "rsi": "RSI",
        "new": "Özel Tarama",
        "rsi_macd": "RSI+MACD+Hacim"
    }
    
    for res in all_results:
        p = res["period"]
        for strat in ["full", "smi", "rsi", "new", "rsi_macd"]:
            for item in res[strat]:
                sym = item["symbol"]
                if sym not in symbol_map:
                    symbol_map[sym] = {}
                if p not in symbol_map[sym]:
                    symbol_map[sym][p] = []
                symbol_map[sym][p].append(strategy_names[strat])
    
    if not symbol_map:
        return

    header = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
    header += f"<b>📊 TÜM PERİYOTLAR HİSSE ÖZETİ</b>\n"
    header += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
    
    body = ""
    # Alfabetik sırala
    for sym in sorted(symbol_map.keys()):
        body += f"<b>• {sym}:</b>\n"
        for p in ["15m", "1H", "4H", "1D", "1W", "1M"]:
            if p in symbol_map[sym]:
                strats = ", ".join(symbol_map[sym][p])
                body += f"  └ {p}: {strats}\n"
        body += "\n"
        
        # Telegram mesaj sınırı kontrolü (yaklaşık 4096 karakter)
        if len(header + body) > 3500:
            telegram_sender.send_message(header + body + "<b>━━━━━━━━━━━━━━━━━━━━━━</b>")
            body = "" # Reset body for next part
    
    if body:
        footer = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>"
        telegram_sender.send_message(header + body + footer)


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
