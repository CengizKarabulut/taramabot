"""
Taramabot Zamanlayıcı Modülü
GitHub Actions gecikmelerine karşı dayanıklı akıllı zamanlama sistemi.
"""

import asyncio
import logging
import json
import os
from datetime import datetime, time, timedelta
from typing import Callable, Optional
import pytz

from config import SCAN_TIMES, SCAN_WEEKENDS, STATE_FILE

logger = logging.getLogger(__name__)

# Türkiye saat dilimi
TZ_TURKEY = pytz.timezone('Europe/Istanbul')


class Scheduler:
    """Tarama zamanlayıcı."""
    
    def __init__(self):
        self.is_running = False
    
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
    def get_last_scan_state() -> dict:
        """Son yapılan taramaların durumunu state.json'dan oku."""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    return data.get("last_successful_scans", {})
            except:
                return {}
        return {}

    @staticmethod
    def save_scan_state(scan_key: str):
        """Başarılı taramayı state.json'a kaydet."""
        data = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
            except:
                data = {}
        
        if "last_successful_scans" not in data:
            data["last_successful_scans"] = {}
        
        data["last_successful_scans"][scan_key] = datetime.now(TZ_TURKEY).strftime("%Y-%m-%d %H:%M:%S")
        
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def should_run_now(force: bool = False) -> Optional[str]:
        """
        Şu an bir tarama yapılması gerekiyor mu?
        """
        now = Scheduler.get_current_time()
        
        # MANUEL ÇALIŞTIRMA: Eğer force=True ise hiçbir kontrol yapmadan özel bir key döndür
        if force:
            logger.info("Manuel (zorunlu) çalıştırma tetiklendi. Kontroller atlanıyor.")
            return f"manual_{now.strftime('%Y%m%d_%H%M%S')}"
        
        # Planlı çalıştırma için hafta sonu kontrolü
        if not Scheduler.is_scan_day(now):
            logger.info("Bugün haftasonu, planlı tarama yapılmayacak.")
            return None
            
        scan_times = Scheduler.get_scan_times()
        last_scans = Scheduler.get_last_scan_state()
        today_str = now.strftime("%Y-%m-%d")

        for s_time in reversed(scan_times):
            scan_dt = now.replace(hour=s_time.hour, minute=s_time.minute, second=0, microsecond=0)
            
            if now >= scan_dt:
                scan_key = f"{today_str}_{s_time.strftime('%H:%M')}"
                
                if scan_key not in last_scans:
                    if now <= scan_dt + timedelta(hours=3):
                        return scan_key
                    else:
                        logger.info(f"{s_time.strftime('%H:%M')} taraması üzerinden çok zaman geçmiş (3 saat+), atlanıyor.")
                else:
                    logger.info(f"En güncel planlı tarama ({s_time.strftime('%H:%M')}) zaten yapılmış.")
                    break
            
        return None

    async def run_once_if_needed(self, scan_func: Callable, market_type: str = "bist", force: bool = False):
        """GitHub Actions veya manuel çalışmalarda kullanılır."""
        scan_key = self.should_run_now(force=force)
        if scan_key:
            logger.info(f"Tarama başlatılıyor: {scan_key}")
            await scan_func(market_type)
            # Manuel taramaları state'e kaydetme ki planlı akışı etkilemesin
            if not scan_key.startswith("manual"):
                self.save_scan_state(scan_key)
            logger.info(f"Tarama başarıyla tamamlandı.")
        else:
            logger.info("Şu an planlanmış bir tarama vakti değil veya bekleyen tarama yok.")

    async def start(self, scan_func: Callable, market_type: str = "bist", period: str = "1D") -> None:
        """Sürekli çalışan (server) modu için zamanlayıcı."""
        self.is_running = True
        logger.info("Sürekli zamanlayıcı başlatıldı.")
        while self.is_running:
            await self.run_once_if_needed(scan_func, market_type)
            await asyncio.sleep(60) # Her dakika kontrol et


# Global singleton instance
_scheduler_instance: Optional[Scheduler] = None

def get_scheduler() -> Scheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = Scheduler()
    return _scheduler_instance
