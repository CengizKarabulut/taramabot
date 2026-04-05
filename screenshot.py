"""
Taramabot Ekran Görüntüsü Modülü
Playwright kullanarak TradingView grafiklerinin ekran görüntüsünü alır.
Kullanıcının TV_CHART_ID şablonunu kullanarak kendi indikatörlerini görmesini sağlar.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Page

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
        """
        screenshot_path = f"{self.screenshots_dir}/{symbol}_{interval}.png"
        
        for attempt in range(1, retry_count + 1):
            try:
                logger.info(f"Ekran görüntüsü alınıyor: {symbol} ({interval}) - Deneme {attempt}/{retry_count}")
                
                async with async_playwright() as p:
                    # Tarayıcıyı başlat
                    browser = await p.chromium.launch(
                        headless=True,
                        args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
                    )
                    
                    try:
                        context = await browser.new_context(
                            viewport={'width': SCREENSHOT_WIDTH, 'height': SCREENSHOT_HEIGHT},
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        )
                        page = await context.new_page()
                        
                        # 1. Giriş Yap (Eğer bilgiler varsa)
                        if TV_USERNAME and TV_PASSWORD:
                            await self._login_tradingview(page)
                        
                        # 2. Grafiğe Git
                        await self._navigate_to_chart(page, symbol, exchange, interval)
                        
                        # 3. Ekran Görüntüsü Al
                        # Grafik elementinin yüklenmesini bekle (TradingView'da ana grafik alanı)
                        try:
                            await page.wait_for_selector(".chart-container", timeout=SCREENSHOT_TIMEOUT)
                        except:
                            logger.warning("chart-container bulunamadı, genel sayfa bekleniyor...")
                            await page.wait_for_load_state("networkidle", timeout=SCREENSHOT_TIMEOUT)
                        
                        # İndikatörlerin ve verilerin yüklenmesi için bekle
                        await asyncio.sleep(SCREENSHOT_WAIT_TIME)
                        
                        # Ekran görüntüsü al
                        await page.screenshot(path=screenshot_path)
                        
                        if os.path.exists(screenshot_path) and os.path.getsize(screenshot_path) > 1000:
                            logger.info(f"Ekran görüntüsü başarıyla alındı: {screenshot_path}")
                            return screenshot_path
                        else:
                            logger.warning("Ekran görüntüsü dosyası boş veya oluşmadı.")
                        
                    finally:
                        await browser.close()
                        
            except Exception as e:
                logger.error(f"Ekran görüntüsü hatası ({symbol}, {interval}): {str(e)}")
                if attempt < retry_count:
                    await asyncio.sleep(SCREENSHOT_RETRY_DELAY)
        
        return None
    
    async def _login_tradingview(self, page: Page):
        """TradingView'a giriş yap."""
        try:
            logger.info("TradingView'a giriş yapılıyor...")
            await page.goto("https://www.tradingview.com/accounts/signin/", timeout=SCREENSHOT_TIMEOUT)
            
            # Kullanıcı adı ve şifre alanlarını doldur
            await page.fill("input[name='username']", TV_USERNAME)
            await page.fill("input[name='password']", TV_PASSWORD)
            
            # Giriş butonuna tıkla
            await page.click("button[type='submit']")
            
            # Girişin tamamlanmasını bekle
            try:
                await page.wait_for_url("https://www.tradingview.com/", timeout=10000)
                logger.info("Giriş başarılı.")
            except:
                logger.warning("Giriş yönlendirmesi beklenenden uzun sürdü veya başarısız oldu.")
                
        except Exception as e:
            logger.error(f"Giriş sırasında hata: {str(e)}")

    async def _navigate_to_chart(self, page: Page, symbol: str, exchange: str, interval: str):
        """Grafik sayfasına git."""
        # Zaman dilimi dönüşümü
        tv_intervals = {
            "1H": "60",
            "4H": "240",
            "1D": "D",
            "1W": "W"
        }
        tv_interval = tv_intervals.get(interval, "D")
        
        # URL oluşturma
        if TV_CHART_ID:
            # Kullanıcının kendi grafik şablonu
            chart_url = f"https://www.tradingview.com/chart/{TV_CHART_ID}/?symbol={exchange}:{symbol}&interval={tv_interval}"
        else:
            # Varsayılan grafik
            chart_url = f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol}&interval={tv_interval}"
            
        logger.info(f"Grafik URL'sine gidiliyor: {chart_url}")
        await page.goto(chart_url, wait_until="domcontentloaded", timeout=SCREENSHOT_TIMEOUT)
        # Ekstra bekleme
        await asyncio.sleep(2)


# Singleton instance
_handler = TradingViewScreenshot()

async def take_screenshot(symbol: str, exchange: str, interval: str) -> Optional[str]:
    return await _handler.get_screenshot(symbol, exchange, interval)
