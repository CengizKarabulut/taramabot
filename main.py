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
        # scan_market artık 7 değer döndürüyor (ema_signals eklendi)
        full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals, ema_signals, total_scanned = await scanner.scan_market(
            market_type=market_type,
            period=period,
            strategies=["smi_macd", "rsi", "new_scan", "rsi_macd", "ema"]
        )

        # 1. Özet listeyi gönder (Tüm sinyalleri içeren gruplanmış özet)
        if full_signals or smi_signals or rsi_signals or new_scan_signals or rsi_macd_signals or ema_signals:
            telegram_sender.send_grouped_summary(period, full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals, ema_signals)
            
            # 2. İstatistik Mesajı
            finish_msg = f"{EMOJI_SUCCESS} <b>Tarama Tamamlandı! ({period})</b>\n"
            finish_msg += f"⏱ {datetime.now(TZ_TURKEY).strftime('%Y-%m-%d %H:%M')}\n"
            finish_msg += f"🔍 Toplam {total_scanned} hisse tarandı.\n"
            finish_msg += f"📈 {len(ema_signals)} EMA, {len(rsi_macd_signals)} RSI+MACD, {len(new_scan_signals)} Özel, {len(full_signals)} Tam, {len(smi_signals)} SMI, {len(rsi_signals)} RSI sinyali bulundu."
            telegram_sender.send_message(finish_msg)

        else:
            telegram_sender.send_message(f"{EMOJI_INFO} <b>{period}</b> taramasında ({total_scanned} hisse) kriterlere uygun sinyal bulunamadı.")

        # Sonuçları bir sonraki aşama (toplu özet) için döndür
        return {
            "period": period,
            "full": full_signals,
            "smi": smi_signals,
            "rsi": rsi_signals,
            "new": new_scan_signals,
            "rsi_macd": rsi_macd_signals,
            "ema": ema_signals
        }

    except Exception as e:
        logger.error(f"Ana tarama mantığında hata: {str(e)}", exc_info=True)
        telegram_sender.send_error(f"Tarama sırasında bir hata oluştu: {str(e)}")
        return None


async def run_multi_scan(market_type: str = "bist"):
    """Tüm zaman dilimlerini sırayla tara."""
    periods = ["15m", "1H", "4H", "1D", "1W", "1M"]
    logger.info(f"Çoklu tarama başlatılıyor: {periods}")
    
    all_results = []
    for p in periods:
        res = await main_scan_logic(market_type, p)
        if res:
            all_results.append(res)
        await asyncio.sleep(2)
    
    # Tüm taramalar bitince BİRDEN FAZLA taramada çıkan hisseleri belirtecek özel özet raporu oluştur
    if all_results:
        send_final_summary(all_results)


def send_final_summary(all_results: list):
    """
    Tüm taramalar bittikten sonra BİRDEN FAZLA taramada çıkan hisseleri özetler.
    """
    telegram_sender = get_telegram_sender()
    symbol_map = {} # symbol -> { period -> [strategies] }
    
    strategy_names = {
        "full": "Tam Alım",
        "smi": "SMI/MACD",
        "rsi": "RSI",
        "new": "Özel Tarama",
        "rsi_macd": "RSI+MACD+Hacim",
        "ema": "EMA Dizilimi"
    }
    
    for res in all_results:
        p = res["period"]
        for strat in ["full", "smi", "rsi", "new", "rsi_macd", "ema"]:
            for item in res[strat]:
                sym = item["symbol"]
                if sym not in symbol_map:
                    symbol_map[sym] = {}
                if p not in symbol_map[sym]:
                    symbol_map[sym][p] = []
                symbol_map[sym][p].append(strategy_names[strat])
    
    # Birden fazla sinyal varsa (farklı periyot veya aynı periyot farklı strateji)
    filtered_map = {}
    for sym, periods in symbol_map.items():
        total_signals = sum(len(strats) for strats in periods.values())
        if total_signals > 1:
            filtered_map[sym] = periods

    if not filtered_map:
        return

    header = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
    header += f"<b>💎 ÇOKLU SİNYAL VEREN HİSSELER</b>\n"
    header += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
    
    body = ""
    # Alfabetik sırala
    for sym in sorted(filtered_map.keys()):
        body += f"<b>• {sym}:</b>\n"
        for p in ["15m", "1H", "4H", "1D", "1W", "1M"]:
            if p in filtered_map[sym]:
                strats = ", ".join(filtered_map[sym][p])
                body += f"  └ {p}: {strats}\n"
        body += "\n"
        
        # Telegram mesaj sınırı kontrolü
        if len(header + body) > 3500:
            telegram_sender.send_message(header + body + "<b>━━━━━━━━━━━━━━━━━━━━━━</b>")
            body = ""
    
    if body:
        footer = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>"
        telegram_sender.send_message(header + body + footer)


async def run_bot():
    """
    Botu çalıştıran ana fonksiyon.
    """
    if len(sys.argv) > 1:
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
        scheduler = Scheduler()
        await scheduler.start(run_multi_scan, market_type="bist")


if __name__ == "__main__":
    asyncio.run(run_bot())
