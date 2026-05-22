"""
Taramabot Zamanlayıcı Modülü
Belirli saatlerde ve sadece hafta içi çalışmasını sağlar.
"""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Callable, Optional
import pytz

from config import SCAN_TIMES, SCAN_WEEKENDS

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
    def get_scan_times() -> list[time]:
        """Konfigürasyondaki tarama saatlerini time objesi olarak al."""
        times = []
        for t_str in SCAN_TIMES:
            hour, minute = map(int, t_str.split(':'))
            times.append(time(hour, minute))
        return sorted(times)

    @staticmethod
    def is_scan_day(dt: datetime) -> bool:
        """Verilen tarih tarama günü mü? (Haftasonu kontrolü)"""
        if not SCAN_WEEKENDS and dt.weekday() >= 5:  # 5: Cumartesi, 6: Pazar
            return False
        return True

    @staticmethod
    def get_next_scan_time() -> datetime:
        """Sonraki tarama saatini hesapla."""
        now = Scheduler.get_current_time()
        scan_times = Scheduler.get_scan_times()
        
        # Bugün için sonraki saatleri kontrol et
        if Scheduler.is_scan_day(now):
            for s_time in scan_times:
                if s_time > now.time():
                    return now.replace(
                        hour=s_time.hour,
                        minute=s_time.minute,
                        second=0,
                        microsecond=0
                    )
        
        # Bugün bitti veya bugün haftasonu, sonraki günlere bak
        next_day = now + timedelta(days=1)
        while not Scheduler.is_scan_day(next_day):
            next_day += timedelta(days=1)
        
        # Bir sonraki çalışma gününün ilk saati
        first_scan_time = scan_times[0]
        return next_day.replace(
            hour=first_scan_time.hour,
            minute=first_scan_time.minute,
            second=0,
            microsecond=0
        )

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
        """
        self.is_running = True
        logger.info(f"Zamanlayıcı başlatılıyor (Türkiye saati: {self.get_current_time()})")
        logger.info(f"Planlanan tarama saatleri: {SCAN_TIMES}")
        logger.info(f"Haftasonu çalışma: {SCAN_WEEKENDS}")
        
        while self.is_running:
            try:
                wait_seconds = self.get_wait_seconds()
                next_scan = Scheduler.get_next_scan_time()
                
                if wait_seconds > 0:
                    logger.info(f"Sonraki tarama bekliyor: {next_scan.strftime('%Y-%m-%d %H:%M:%S')} ({wait_seconds} saniye sonra)")
                    # Çok uzun beklemelerde ara ara kontrol etmek için max 5 dakika bekle
                    await asyncio.sleep(min(wait_seconds, 300))
                else:
                    # Tarama saati geldi
                    logger.info(f"Tarama saati geldi, başlatılıyor ({market_type.upper()}, {period})...")
                    await scan_func(market_type, period)
                    
                    # Aynı dakikada tekrar çalışmaması için biraz bekle
                    logger.info("Tarama tamamlandı, bir sonraki periyot için bekleniyor...")
                    await asyncio.sleep(61) 
            
            except Exception as e:
                logger.error(f"Zamanlayıcı hatası: {str(e)}")
                await asyncio.sleep(60)
    
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
