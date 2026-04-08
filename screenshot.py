"""
Taramabot Ekran Görüntüsü Modülü
undetected-chromedriver kullanarak TradingView grafiklerinin ekran görüntüsünü alır.
finans_botu'ndaki çalışan yapı referans alınarak güncellenmiştir.
"""

import os
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional, List, Dict

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc

from config import (
    TV_CHART_ID, SCREENSHOT_WIDTH, SCREENSHOT_HEIGHT, 
    SCREENSHOT_TIMEOUT, SCREENSHOT_WAIT_TIME, SCREENSHOTS_DIR
)

logger = logging.getLogger(__name__)

# Ayarlar
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selenium_profile")
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tv_cookies.txt")
DEFAULT_CHART_URL = "https://www.tradingview.com/chart/"

CHART_LOAD_WAIT = 10
SYMBOL_CHANGE_WAIT = 5
UI_HIDE_WAIT = 2

# Interval mapping (TradingView URL formatı)
_INTERVAL_MAP = {
    "15m": "15", "1H": "60", "4H": "240",
    "1D": "D", "1W": "W", "1M": "M"
}

def _tv_sembol_formatla(symbol: str, exchange: str) -> str:
    """Sembolü TradingView arama formatına çevirir."""
    s = symbol.upper().strip()
    e = exchange.upper().strip() if exchange else ""
    
    if e == "BIST" or s.endswith(".IS"):
        clean_s = s.replace(".IS", "")
        return f"BIST:{clean_s}"
    
    if e:
        return f"{e}:{s}"
    
    return s

def _cookie_dosyasi_oku() -> List[Dict]:
    """tv_cookies.txt dosyasından cookie'leri okur."""
    if not os.path.exists(COOKIE_FILE):
        logger.warning(f"Cookie dosyası bulunamadı: {COOKIE_FILE}")
        return []

    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()

        if not raw:
            return []

        cookies = []
        for part in raw.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            idx = part.index("=")
            name = part[:idx].strip()
            value = part[idx + 1:].strip()

            if not name:
                continue

            cookies.append({
                "name": name,
                "value": value,
                "domain": ".tradingview.com",
                "path": "/",
                "secure": True,
            })
        return cookies
    except Exception as e:
        logger.error(f"Cookie okuma hatası: {e}")
        return []

def _cookie_enjekte(driver, cookies: List[Dict]) -> bool:
    """Cookie'leri Selenium driver'a enjekte eder."""
    if not cookies:
        return False

    try:
        # Önce ana sayfaya git ki cookie domaini eşleşsin
        driver.get("https://www.tradingview.com/")
        time.sleep(3)
        driver.delete_all_cookies()

        eklenen = 0
        for cookie in cookies:
            try:
                driver.add_cookie(cookie)
                eklenen += 1
            except Exception as e:
                logger.debug(f"Cookie atlandı ({cookie['name']}): {e}")

        logger.info(f"{eklenen}/{len(cookies)} cookie enjekte edildi.")
        return eklenen > 0
    except Exception as e:
        logger.error(f"Cookie enjeksiyon hatası: {e}")
        return False

class TVBrowser:
    """Singleton Chrome WebDriver yöneticisi."""
    _driver = None
    _cookies_injected = False

    @classmethod
    def get_driver(cls, headless: bool = True):
        if cls._driver is not None:
            try:
                # Driver hala yaşıyor mu kontrol et
                _ = cls._driver.current_url
                return cls._driver
            except Exception:
                cls._force_quit()

        os.makedirs(PROFILE_DIR, exist_ok=True)
        options = uc.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")

        options.add_argument(f"--user-data-dir={PROFILE_DIR}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument(f"--window-size={SCREENSHOT_WIDTH},{SCREENSHOT_HEIGHT}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        # Dil ayarını Türkçe yapalım ki UI seçicileri şaşmasın
        options.add_argument("--lang=tr-TR")

        try:
            # version_main kaldırıldı, otomatik eşleşme sağlandı
            cls._driver = uc.Chrome(options=options)
            cls._driver.set_page_load_timeout(60)
            cls._driver.implicitly_wait(5)
            cls._cookies_injected = False
            return cls._driver
        except Exception as e:
            logger.error(f"WebDriver başlatılamadı: {e}")
            cls._driver = None
            return None

    @classmethod
    def _force_quit(cls):
        try:
            if cls._driver:
                cls._driver.quit()
        except: pass
        cls._driver = None
        cls._cookies_injected = False

def _oturum_acik_mi(driver) -> bool:
    """TradingView'da oturum açık mı kontrol eder."""
    try:
        selectors = [
            '[data-name="header-user-menu-button"]',
            'button[aria-label="Open user menu"]',
            'button[aria-label="Kullanıcı menüsünü aç"]',
            '.tv-header__user-menu-button',
        ]
        for sel in selectors:
            if driver.find_elements(By.CSS_SELECTOR, sel):
                return True
        return False
    except:
        return False

def _sembol_degistir(driver, tv_symbol: str) -> bool:
    """TradingView grafik üzerinde sembolü değiştirir."""
    try:
        # Önce ESC ile varsa açık diyalogları kapat
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ESCAPE)
        time.sleep(1)
        
        # Doğrudan sembolü yaz (TradingView klavye kısayolu)
        body.send_keys(tv_symbol)
        time.sleep(2)
        body.send_keys(Keys.ENTER)
        time.sleep(2)
        
        logger.info(f"Sembol değiştirildi: {tv_symbol}")
        return True
    except Exception as e:
        logger.warning(f"Sembol değiştirme hatası: {e}")
        return False

def _ui_gizle(driver):
    """TradingView UI elemanlarını gizler."""
    try:
        driver.execute_script("""
            var selectors = [
                'header', '.tv-header', '.tv-header--mobile',
                '[data-name="drawing-toolbar"]', '.tv-side-toolbar',
                '.layout__area--left', '.layout__area--right',
                '.chart-controls-bar', '.bottom-widgetbar-content',
                '.layout__area--bottom', '[data-name="right-toolbar"]',
                '.is-hidden', '.tv-floating-toolbar'
            ];
            selectors.forEach(function(s) {
                try {
                    document.querySelectorAll(s).forEach(function(el) {
                        el.style.setProperty('display', 'none', 'important');
                    });
                } catch(e) {}
            });
            try {
                var c = document.querySelector('.layout__area--center');
                if (c) {
                    c.style.setProperty('left', '0', 'important');
                    c.style.setProperty('top', '0', 'important');
                    c.style.setProperty('width', '100vw', 'important');
                    c.style.setProperty('height', '100vh', 'important');
                }
            } catch(e) {}
        """)
    except: pass

async def take_screenshot(symbol: str, exchange: str, interval: str) -> Optional[str]:
    """TradingView üzerinden grafik ekran görüntüsü alır."""
    loop = asyncio.get_running_loop()
    output_path = os.path.join(SCREENSHOTS_DIR, f"{symbol}_{interval}.png")
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    try:
        driver = await loop.run_in_executor(None, lambda: TVBrowser.get_driver(headless=True))
        if not driver:
            return None

        # Cookie enjeksiyonu (Sadece ilk seferde veya oturum kapalıysa)
        if not TVBrowser._cookies_injected:
            cookies = _cookie_dosyasi_oku()
            if cookies:
                await loop.run_in_executor(None, lambda: _cookie_enjekte(driver, cookies))
                TVBrowser._cookies_injected = True

        # URL Oluşturma - Public chart URL (login gerektirmez, her zaman açılır)
        tv_symbol = _tv_sembol_formatla(symbol, exchange)
        tv_interval = _INTERVAL_MAP.get(interval, "D")
        
        # Eğer TV_CHART_ID varsa layout'u açar, yoksa public URL kullanır
        if TV_CHART_ID and TV_CHART_ID.strip():
            clean_id = TV_CHART_ID.strip().strip('/')
            chart_url = f"https://www.tradingview.com/chart/{clean_id}/?symbol={tv_symbol}&interval={tv_interval}"
        else:
            chart_url = f"https://www.tradingview.com/chart/?symbol={tv_symbol}&interval={tv_interval}"
        
        logger.info(f"Grafik URL'sine gidiliyor: {chart_url}")
        await loop.run_in_executor(None, lambda: driver.get(chart_url))
        
        # Sayfanın yüklenmesini bekle
        await asyncio.sleep(CHART_LOAD_WAIT)

        # Oturum kontrolü (Opsiyonel, loglama için)
        is_logged_in = await loop.run_in_executor(None, lambda: _oturum_acik_mi(driver))
        if not is_logged_in:
            logger.warning("TradingView oturumu açık görünmüyor. Lütfen TV_COOKIES secret'ını kontrol edin.")

        # Verilerin ve indikatörlerin yüklenmesi için bekle
        await asyncio.sleep(SYMBOL_CHANGE_WAIT)

        # UI gizle
        await loop.run_in_executor(None, lambda: _ui_gizle(driver))
        await asyncio.sleep(UI_HIDE_WAIT)

        # Screenshot al
        await loop.run_in_executor(None, lambda: driver.save_screenshot(output_path))
        
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            logger.info(f"Grafik başarıyla kaydedildi: {output_path}")
            return output_path
        
        return None

    except Exception as e:
        logger.error(f"Grafik çekme hatası ({symbol}): {e}")
        # Hata durumunda driver'ı sıfırlayalım ki bir sonraki denemede temiz başlasın
        TVBrowser._force_quit()
        return None
