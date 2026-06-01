import asyncio
import logging
import sys
from main import main_scan_logic
from config import LOG_LEVEL, LOG_FORMAT

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

async def run_strategy_scan(market_type="bist"):
    periods = ["15m", "1H", "4H", "1D", "1W", "1M"]
    strategy = "rsi"
    logger.info(f"{strategy} taraması başlatılıyor ({market_type})...")
    
    for p in periods:
        # main_scan_logic'i sadece bu strateji ile çalışacak şekilde modifiye etmemiz gerekebilir 
        # veya mevcut main_scan_logic'i kullanıp sadece ilgili sonuçları filtreleyebiliriz.
        # Ancak en temiz yol scanner.scan_market'i doğrudan çağırmaktır.
        from scanner import MarketScanner
        from telegram_sender import get_telegram_sender
        
        scanner = MarketScanner()
        telegram_sender = get_telegram_sender()
        
        logger.info(f"Periyot: {p}")
        try:
            results = await scanner.scan_market(
                market_type=market_type,
                period=p,
                strategies=["rsi"]
            )
            
            # scan_market 8 değer döndürüyor
            (full_signals, smi_signals, rsi_signals, new_scan_signals, 
             rsi_macd_signals, ema_signals, macd_cross_signals, total_scanned) = results
            
            # Sadece bu stratejiye ait sinyalleri gönder
            telegram_sender.send_grouped_summary(
                period=p,
                full_signals=[],
                smi_signals=[],
                rsi_signals=rsi_signals,
                new_scan_signals=[],
                rsi_macd_signals=[],
                ema_signals=[],
                macd_cross_signals=[]
            )
        except Exception as e:
            logger.error(f"Hata: {str(e)}")

if __name__ == "__main__":
    market = sys.argv[1] if len(sys.argv) > 1 else "bist"
    asyncio.run(run_strategy_scan(market))
