"""
Taramabot Ekran Görüntüsü Modülü
Playwright kullanarak TradingView grafiklerinin ekran görüntüsünü alır.
Retry mekanizması ve hata yönetimi ile sağlam bir yapı sunar.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Browser, Page

from config import (
    TV_USERNAME, TV_PASSWORD, TV_CHART_ID,
    SCREENSHOT_WIDTH, SCREENSHOT_HEIGHT, SCREENSHOT_TIMEOUT,
    SCREENSHOT_WAIT_TIME, SCREENSHOT_RETRY_COUNT, SCREENSHOT_RETRY_DELAY,
    SCREENSHOTS_DIR
)

logger = logging.getLogger(__name__)


class TradingViewScreenshot:
    """TradingView ekran görüntüsü alıcı."""
    
    def __init__(self):
        self.screenshots_dir = SCREENSHOTS_DIR
        Path(self.screenshots_dir).mkdir(exist_ok=True)
    
    async def get_screenshot(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        retry_count: int = SCREENSHOT_RETRY_COUNT
    ) -> Optional[str]:
        """
        TradingView grafiğinin ekran görüntüsünü al.
        
        Args:
            symbol: Sembol (örn: AKBNK, BTCUSD)
            exchange: Borsa (örn: BIST, BINANCE, OANDA)
            interval: Zaman dilimi (örn: 4H, 1D, 1W)
            retry_count: Tekrar deneme sayısı
            
        Returns:
            Ekran görüntüsü dosya yolu veya None
        """
        screenshot_path = f"{self.screenshots_dir}/{symbol}_{interval}.png"
        
        for attempt in range(1, retry_count + 1):
            try:
                logger.info(f"Ekran görüntüsü alınıyor: {symbol} ({interval}) - Deneme {attempt}/{retry_count}")
                
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True,
                        args=['--no-sandbox', '--disable-setuid-sandbox']
                    )
                    
                    try:
                        context = await browser.new_context(
                            viewport={'width': SCREENSHOT_WIDTH, 'height': SCREENSHOT_HEIGHT}
                        )
                        page = await context.new_page()
                        
                        # TradingView'a giriş yap
                        if TV_USERNAME and TV_PASSWORD:
                            await self._login_tradingview(page)
                        
                        # Grafiğe git
                        await self._navigate_to_chart(page, symbol, exchange, interval)
                        
                        # Ekran görüntüsünü al
                        await page.screenshot(path=screenshot_path)
                        logger.info(f"Ekran görüntüsü kaydedildi: {screenshot_path}")
                        
                        await context.close()
                        return screenshot_path
                        
                    finally:
                        await browser.close()
                        
            except asyncio.TimeoutError:
                logger.warning(f"Zaman aşımı: {symbol} ({interval}) - Deneme {attempt}/{retry_count}")
                if attempt < retry_count:
                    await asyncio.sleep(SCREENSHOT_RETRY_DELAY)
                    
            except Exception as e:
                logger.error(f"Hata: {symbol} ({interval}) - {str(e)}")
                if attempt < retry_count:
                    await asyncio.sleep(SCREENSHOT_RETRY_DELAY)
        
        logger.error(f"Ekran görüntüsü alınamadı: {symbol} ({interval}) - {retry_count} deneme başarısız")
        return None
    
    async def _login_tradingview(self, page: Page) -> bool:
        """TradingView'a giriş yap."""
        try:
            logger.info("TradingView'a giriş yapılıyor...")
            await page.goto("https://www.tradingview.com/accounts/signin/", timeout=SCREENSHOT_TIMEOUT)
            
            # Giriş formunu doldur
            await page.fill("input[name='username']", TV_USERNAME)
            await page.fill("input[name='password']", TV_PASSWORD)
            
            # Giriş butonuna tıkla
            await page.click("button[type='submit']")
            
            # Giriş başarılı mı kontrol et
            try:
                await page.wait_for_url("https://www.tradingview.com/", timeout=10000)
                logger.info("TradingView girişi başarılı")
                return True
            except:
                logger.warning("TradingView girişi başarısız olabilir, devam ediliyor...")
                return False
                
        except Exception as e:
            logger.error(f"TradingView giriş hatası: {str(e)}")
            return False
    
    async def _navigate_to_chart(self, page: Page, symbol: str, exchange: str, interval: str) -> None:
        """TradingView grafiğine git."""
        try:
            # Zaman dilimini TradingView formatına çevir
            tv_intervals = {"4H": "240", "1D": "D", "1W": "W"}
            tv_interval = tv_intervals.get(interval, "D")
            
            # Grafik URL'sini oluştur
            if TV_CHART_ID:
                chart_url = f"https://www.tradingview.com/chart/{TV_CHART_ID}/?symbol={exchange}:{symbol}&interval={tv_interval}"
            else:
                chart_url = f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol}&interval={tv_interval}"
            
            logger.info(f"Grafiğe gidiliyor: {chart_url}")
            await page.goto(chart_url, wait_until="networkidle", timeout=SCREENSHOT_TIMEOUT)
            
            # Grafik yüklenene kadar bekle
            await page.wait_for_selector(".chart-container", timeout=SCREENSHOT_TIMEOUT)
            
            # Grafik tamamen yüklensin diye biraz bekle
            await asyncio.sleep(SCREENSHOT_WAIT_TIME)
            
        except Exception as e:
            logger.error(f"Grafik navigasyon hatası: {str(e)}")
            raise


# Global singleton instance
_screenshot_instance: Optional[TradingViewScreenshot] = None


def get_screenshot_handler() -> TradingViewScreenshot:
    """Singleton ekran görüntüsü handler'ını al."""
    global _screenshot_instance
    if _screenshot_instance is None:
        _screenshot_instance = TradingViewScreenshot()
    return _screenshot_instance


async def take_screenshot(symbol: str, exchange: str, interval: str) -> Optional[str]:
    """
    Kolaylık fonksiyonu: Ekran görüntüsü al.
    
    Args:
        symbol: Sembol
        exchange: Borsa
        interval: Zaman dilimi
        
    Returns:
        Dosya yolu veya None
    """
    handler = get_screenshot_handler()
    return await handler.get_screenshot(symbol, exchange, interval)
