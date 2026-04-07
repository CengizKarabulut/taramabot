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
                            # Farklı grafik konteyner seçicilerini dene
                            chart_selectors = [".chart-container", ".layout__area--center", "canvas.trading-chart"]
                            found = False
                            for selector in chart_selectors:
                                try:
                                    await page.wait_for_selector(selector, timeout=5000)
                                    found = True
                                    break
                                except: continue
                            
                            if not found:
                                logger.warning("Belirli bir grafik konteyneri bulunamadı, genel sayfa bekleniyor...")
                                await page.wait_for_load_state("networkidle", timeout=SCREENSHOT_TIMEOUT)
                        except:
                            logger.warning("Bekleme sırasında hata oluştu, devam ediliyor...")
                        
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
            # Doğrudan giriş sayfasına git
            await page.goto("https://www.tradingview.com/#signin", timeout=SCREENSHOT_TIMEOUT)
            await asyncio.sleep(2)
            
            # Zaten giriş yapılmış mı kontrol et (Kullanıcı menüsü butonu varsa)
            user_menu_selectors = ["button[aria-label='Open user menu']", ".tv-header__user-menu-button", "button[id='header-user-menu-button']"]
            for selector in user_menu_selectors:
                if await page.query_selector(selector):
                    logger.info("Zaten giriş yapılmış.")
                    return

            # Email ile giriş butonunu bul ve tıkla
            # TradingView bazen doğrudan formu gösterir, bazen butonla seçtirir
            email_btn_selectors = [
                "button[name='Email']", 
                "span:has-text('Email')", 
                "button:has-text('Email')",
                "div[data-name='email']"
            ]
            
            for selector in email_btn_selectors:
                btn = await page.query_selector(selector)
                if btn:
                    await btn.click()
                    await asyncio.sleep(1)
                    break

            # Kullanıcı adı/Email ve şifre alanlarını doldur
            user_selectors = ["input[name='id_username']", "input[name='username']", "#id_username", "input[id='id_username']"]
            pass_selectors = ["input[name='id_password']", "input[name='password']", "#id_password", "input[id='id_password']"]
            
            user_filled = False
            for selector in user_selectors:
                try:
                    input_field = await page.query_selector(selector)
                    if input_field and await input_field.is_visible():
                        await input_field.fill(TV_USERNAME)
                        user_filled = True
                        break
                except: continue

            pass_filled = False
            for selector in pass_selectors:
                try:
                    input_field = await page.query_selector(selector)
                    if input_field and await input_field.is_visible():
                        await input_field.fill(TV_PASSWORD)
                        pass_filled = True
                        break
                except: continue
            
            if not user_filled or not pass_filled:
                logger.warning(f"Giriş alanları tam olarak doldurulamadı. User: {user_filled}, Pass: {pass_filled}")
                # Hata ayıklama için ekran görüntüsü al (geçici)
                # await page.screenshot(path=f"{self.screenshots_dir}/login_failed_fields.png")
            
            # Giriş butonuna tıkla
            submit_selectors = ["button[type='submit']", "button:has-text('Sign in')", "button:has-text('Log in')", ".tv-button--primary"]
            for selector in submit_selectors:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    break
            
            # Girişin tamamlanmasını bekle
            await asyncio.sleep(5)
            
            # Başarı kontrolü
            for selector in user_menu_selectors:
                if await page.query_selector(selector):
                    logger.info("Giriş BAŞARILI.")
                    return
            
            logger.warning("Giriş işlemi tamamlanamadı veya başarısız oldu.")
                
        except Exception as e:
            logger.error(f"Giriş sırasında hata: {str(e)}")

    async def _navigate_to_chart(self, page: Page, symbol: str, exchange: str, interval: str):
        """Grafik sayfasına git."""
        # Zaman dilimi dönüşümü (TradingView URL formatı)
        tv_intervals = {
            "15m": "15",
            "1H": "60",
            "4H": "240",
            "1D": "D",
            "1W": "W",
            "1M": "M"
        }
        tv_interval = tv_intervals.get(interval, tv_intervals.get(interval.lower(), "D"))
        
        # URL oluşturma
        symbol_param = f"{exchange}:{symbol}" if exchange else symbol
        
        if TV_CHART_ID:
            # Kullanıcının kendi grafik şablonu
            chart_url = f"https://www.tradingview.com/chart/{TV_CHART_ID}/?symbol={symbol_param}&interval={tv_interval}"
        else:
            # Varsayılan grafik
            chart_url = f"https://www.tradingview.com/chart/?symbol={symbol_param}&interval={tv_interval}"
            
        logger.info(f"Grafik URL'sine gidiliyor: {chart_url}")
        
        # Sayfaya git ve yüklenmesini bekle
        try:
            await page.goto(chart_url, wait_until="networkidle", timeout=SCREENSHOT_TIMEOUT)
        except:
            try:
                await page.goto(chart_url, wait_until="domcontentloaded", timeout=SCREENSHOT_TIMEOUT)
            except Exception as e:
                logger.error(f"Grafik sayfasına gidilemedi: {str(e)}")
            
        await asyncio.sleep(3)


# Singleton instance
_handler = TradingViewScreenshot()

async def take_screenshot(symbol: str, exchange: str, interval: str) -> Optional[str]:
    return await _handler.get_screenshot(symbol, exchange, interval)
