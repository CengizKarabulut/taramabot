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
    Sembolleri tarar, sinyalleri işler ve Telegram'a rapor gönderir.
    """
    scanner = MarketScanner()
    telegram_sender = get_telegram_sender()

    # Periyot ismini düzelt (TV formatına uygun hale getir)
    period_map = {
        "15m": "15m", "15M": "15m",
        "1h": "1H", "1H": "1H",
        "4h": "4H", "4H": "4H",
        "1d": "1D", "1D": "1D",
        "1w": "1W", "1W": "1W",
        "1m": "1M", "1M": "1M"
    }
    period = period_map.get(period, period)
    
    logger.info(f"Tarama başlatılıyor: Pazar={market_type.upper()}, Periyot={period}")

    try:
        # scan_market çağrısı
        full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals, ema_signals, macd_cross_signals, h8_signals, i9_signals, total_scanned = await scanner.scan_market(
            market_type=market_type,
            period=period,
            strategies=["smi_macd", "rsi", "new_scan", "rsi_macd", "ema", "macd_cross", "h8", "i9"]
        )

        # 1. Gruplanmış özet listeyi gönder (Sinyal olsun veya olmasın mesaj gönderilir)
        telegram_sender.send_grouped_summary(
            period=period, 
            full_signals=full_signals, 
            smi_signals=smi_signals, 
            rsi_signals=rsi_signals, 
            new_scan_signals=new_scan_signals, 
            rsi_macd_signals=rsi_macd_signals, 
            ema_signals=ema_signals,
            macd_cross_signals=macd_cross_signals,
            h8_signals=h8_signals,
            i9_signals=i9_signals
        )
        
        # 2. İstatistik Mesajı (Her periyot sonunda mutlaka gönderilir)
        finish_msg = f"{EMOJI_SUCCESS} <b>Tarama Tamamlandı! ({period})</b>\n"
        finish_msg += f"⏱ {datetime.now(TZ_TURKEY).strftime('%Y-%m-%d %H:%M')}\n"
        finish_msg += f"🔍 {total_scanned} sembol tarandı."
        telegram_sender.send_message(finish_msg)

        # Sonuçları bir sonraki aşama (toplu özet) için döndür
        return {
            "period": period,
            "full": full_signals,
            "smi": smi_signals,
            "rsi": rsi_signals,
            "new": new_scan_signals,
            "rsi_macd": rsi_macd_signals,
            "ema": ema_signals,
            "macd_cross": macd_cross_signals,
            "h8": h8_signals,
            "i9": i9_signals
        }

    except Exception as e:
        logger.error(f"Ana tarama mantığında hata: {str(e)}", exc_info=True)
        telegram_sender.send_error(f"Tarama sırasında bir hata oluştu: {str(e)}")
        return None


async def run_multi_scan(market_type: str = "bist", period: str = "1D"):
    """Tüm zaman dilimlerini KESİN SIRAYLA (Küçükten Büyüğe) tara."""
    # Sıralama: 15m -> 1H -> 4H -> 1D -> 1W -> 1M
    periods = ["15m", "1H", "4H", "1D", "1W", "1M"]
    logger.info(f"Çoklu tarama başlatılıyor: {periods}")
    
    all_results = []
    for p in periods:
        res = await main_scan_logic(market_type, p)
        if res:
            all_results.append(res)
        # Her periyot arasında güvenli bir bekleme (Mesajların sırasının karışmaması için)
        await asyncio.sleep(3)
    
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
        "full": "S5",
        "smi": "S6",
        "rsi": "S7",
        "new": "S4",
        "rsi_macd": "S3",
        "ema": "S2",
        "macd_cross": "S1",
        "h8": "H8",
        "i9": "I9"
    }
    
    for res in all_results:
        p = res["period"]
        for strat in ["full", "smi", "rsi", "new", "rsi_macd", "ema", "macd_cross", "h8", "i9"]:
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
        # Periyotları belirli bir sırada göster
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
        # Çoklu sinyal listesi de çok uzun olabilir, parçalayarak gönder
        full_msg = header + body + footer
        if len(full_msg) > 3500:
            # Basitçe ikiye böl (veya daha fazla)
            lines = body.split("\n")
            mid = len(lines) // 2
            telegram_sender.send_message(header + "\n".join(lines[:mid]) + "\n<b>(Devamı...)</b>")
            telegram_sender.send_message(header + "<b>(Devam)</b>\n" + "\n".join(lines[mid:]) + footer)
        else:
            telegram_sender.send_message(full_msg)


async def run_bot():
    """
    Botu çalıştıran ana fonksiyon.
    """
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "scan":
            period = sys.argv[2]
            market_type = sys.argv[3].lower() if len(sys.argv) > 3 else "bist"
            await main_scan_logic(market_type, period)
        elif command == "multi":
            market_type = sys.argv[2].lower() if len(sys.argv) > 2 else "bist"
            await run_multi_scan(market_type)
        elif command == "auto":
            # GitHub Actions için akıllı mod
            from scheduler import get_scheduler
            scheduler = get_scheduler()
            await scheduler.run_once_if_needed(run_multi_scan, market_type="bist")
        else:
            logger.warning(f"Bilinmeyen komut: {command}")
            sys.exit(1)
    else:
        from scheduler import get_scheduler
        scheduler = get_scheduler()
        await scheduler.start(run_multi_scan, market_type="bist")


if __name__ == "__main__":
    asyncio.run(run_bot())
