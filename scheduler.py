"""
Taramabot Zamanlayıcı Modülü
Botun belirli saatlerde (10:30-18:30) çalışmasını sağlar.
"""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Callable, Optional
import pytz

from config import (
    SCAN_START_HOUR, SCAN_START_MINUTE,
    SCAN_END_HOUR, SCAN_END_MINUTE,
    SCAN_INTERVAL_MINUTES
)

logger = logging.getLogger(__name__)

# Türkiye saat dilimi
TZ_TURKEY = pytz.timezone('Europe/Istanbul')


class Scheduler:
    """Tarama zamanlayıcı."""
    
    def __init__(self):
        self.is_running = False
        self.scan_task: Optional[asyncio.Task] = None
    
    @staticmethod
    def get_current_time() -> datetime:
        """Türkiye saatini al."""
        return datetime.now(TZ_TURKEY)
    
    @staticmethod
    def is_within_scan_hours() -> bool:
        """Şu anki saat, tarama saatleri içinde mi?"""
        now = Scheduler.get_current_time()
        current_time = now.time()
        
        start_time = time(SCAN_START_HOUR, SCAN_START_MINUTE)
        end_time = time(SCAN_END_HOUR, SCAN_END_MINUTE)
        
        return start_time <= current_time <= end_time
    
    @staticmethod
    def get_next_scan_time() -> datetime:
        """Sonraki tarama saatini al."""
        now = Scheduler.get_current_time()
        current_time = now.time()
        
        start_time = time(SCAN_START_HOUR, SCAN_START_MINUTE)
        end_time = time(SCAN_END_HOUR, SCAN_END_MINUTE)
        
        # Eğer tarama saatleri içindeyse, sonraki tarama saatini hesapla
        if start_time <= current_time <= end_time:
            # Sonraki tarama saati
            next_scan = now + timedelta(minutes=SCAN_INTERVAL_MINUTES)
            
            # Eğer tarama saatleri dışına çıkacaksa, ertesi gün başlangıç saatine ayarla
            if next_scan.time() > end_time:
                next_scan = next_scan + timedelta(days=1)
                next_scan = next_scan.replace(
                    hour=SCAN_START_HOUR,
                    minute=SCAN_START_MINUTE,
                    second=0,
                    microsecond=0
                )
            
            return next_scan
        
        # Tarama saatleri dışındaysa, ertesi gün başlangıç saatine ayarla
        if current_time > end_time:
            # Ertesi gün
            next_scan = now + timedelta(days=1)
            next_scan = next_scan.replace(
                hour=SCAN_START_HOUR,
                minute=SCAN_START_MINUTE,
                second=0,
                microsecond=0
            )
        else:
            # Bugün
            next_scan = now.replace(
                hour=SCAN_START_HOUR,
                minute=SCAN_START_MINUTE,
                second=0,
                microsecond=0
            )
        
        return next_scan
    
    @staticmethod
    def get_wait_seconds() -> int:
        """Sonraki taramaya kadar beklenecek saniye sayısını al."""
        next_scan = Scheduler.get_next_scan_time()
        now = Scheduler.get_current_time()
        wait_delta = next_scan - now
        return max(0, int(wait_delta.total_seconds()))
    
    async def start(
        self,
        scan_func: Callable,
        market_type: str = "bist",
        period: str = "1D"
    ) -> None:
        """
        Zamanlayıcıyı başlat.
        
        Args:
            scan_func: Çalıştırılacak tarama fonksiyonu (async)
            market_type: Pazar türü
            period: Zaman dilimi
        """
        self.is_running = True
        logger.info(f"Zamanlayıcı başlatılıyor (Türkiye saati: {self.get_current_time()})")
        logger.info(f"Tarama saatleri: {SCAN_START_HOUR}:{SCAN_START_MINUTE:02d} - {SCAN_END_HOUR}:{SCAN_END_MINUTE:02d}")
        logger.info(f"Tarama aralığı: {SCAN_INTERVAL_MINUTES} dakika")
        
        while self.is_running:
            try:
                if self.is_within_scan_hours():
                    logger.info(f"Tarama başlatılıyor ({market_type.upper()}, {period})...")
                    await scan_func(market_type, period)
                    
                    # Sonraki tarama saatine kadar bekle
                    wait_seconds = self.get_wait_seconds()
                    # Eğer bekleme süresi çok kısaysa (örneğin 0 veya negatif), en az 60 saniye bekle
                    if wait_seconds < 60:
                        wait_seconds = SCAN_INTERVAL_MINUTES * 60
                    
                    logger.info(f"Sonraki tarama: {(self.get_current_time() + timedelta(seconds=wait_seconds)).strftime('%H:%M:%S')} ({wait_seconds} saniye sonra)")
                    await asyncio.sleep(wait_seconds)
                else:
                    # Tarama saatleri dışında, sonraki tarama saatine kadar bekle
                    wait_seconds = self.get_wait_seconds()
                    next_scan = self.get_next_scan_time()
                    logger.info(f"Tarama saatleri dışında. Sonraki tarama: {next_scan.strftime('%H:%M:%S')} ({wait_seconds} saniye sonra)")
                    await asyncio.sleep(min(wait_seconds, 300))  # Max 5 dakika bekle
            
            except Exception as e:
                logger.error(f"Zamanlayıcı hatası: {str(e)}")
                await asyncio.sleep(60)  # Hata durumunda 1 dakika bekle
    
    def stop(self) -> None:
        """Zamanlayıcıyı durdur."""
        self.is_running = False
        logger.info("Zamanlayıcı durduruldu")


# Global singleton instance
_scheduler_instance: Optional[Scheduler] = None


def get_scheduler() -> Scheduler:
    """Singleton zamanlayıcıyı al."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = Scheduler()
    return _scheduler_instance
