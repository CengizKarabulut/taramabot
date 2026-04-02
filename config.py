"""
Taramabot Konfigürasyon Modülü
Ortam değişkenleri ve sistem ayarlarını yönetir.
"""

import os
from dotenv import load_dotenv
from datetime import time
from typing import List, Tuple

# .env dosyasını yükle
load_dotenv()

# ============================================================================
# TELEGRAM KONFİGÜRASYONU
# ============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID")

# ============================================================================
# TRADİNGVİEW KONFİGÜRASYONU
# ============================================================================
TV_USERNAME = os.getenv("TV_USERNAME")
TV_PASSWORD = os.getenv("TV_PASSWORD")
TV_CHART_ID = os.getenv("TV_CHART_ID")

# ============================================================================
# TARAMA AYARLARI
# ============================================================================

# Zamanlanmış çalışma saatleri (Türkiye saati)
SCAN_START_HOUR = 10
SCAN_START_MINUTE = 30
SCAN_END_HOUR = 18
SCAN_END_MINUTE = 30

# Tarama aralığı (dakika cinsinden)
SCAN_INTERVAL_MINUTES = 60

# ============================================================================
# BIST STOKLARı
# ============================================================================
BIST_STOCKS = [
    "AKBNK", "ALARK", "ARCLK", "ASELS", "ASTOR", "BIMAS", "BRSAN", "DOAS", "EGEEN",
    "EKGYO", "ENKAI", "EREGL", "FROTO", "GARAN", "GUBRF", "HEKTS", "ISCTR", "KCHOL",
    "KONTR", "KOZAL", "KRDMD", "ODAS", "OYAKC", "PETKM", "PGSUS", "SAHOL", "SASA",
    "SISE", "TCELL", "THYAO", "TOASO", "TSKB", "TTKOM", "TUPRS", "VAKBN", "YKBNK",
    "CANTE", "EUPWR", "GESAN", "SMRTG", "YEOTK", "MIATK", "ALFAS", "CWENE", "SDTTR",
    "ONCSM", "KMPUR", "KLSER", "KCAER", "AGROT", "KBORU", "TARKM", "MEGMT", "CVKMD"
]

# Emtia Sembolü
COMMODITIES = [
    ("XAUUSD", "OANDA"),
    ("XAGUSD", "OANDA"),
]

# Kripto Sembolü
CRYPTO = [
    ("BTCUSD", "BINANCE"),
    ("ETHUSD", "BINANCE"),
]

# ============================================================================
# İNDİKATÖR AYARLARI
# ============================================================================

# SMI/MACD Taraması
SMI_PERIOD = 10
SMI_EMA_PERIOD = 3
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MA200_PERIOD = 200
VOLUME_MULTIPLIER = 1.5

# RSI Taraması
RSI_PERIOD = 7
RSI_THRESHOLD = 60
RSI_CROSSOVER = 50

# ============================================================================
# EKRAN GÖRÜNTÜSÜ AYARLARI
# ============================================================================

# Playwright ayarları
SCREENSHOT_WIDTH = 1280
SCREENSHOT_HEIGHT = 720
SCREENSHOT_TIMEOUT = 15000  # ms
SCREENSHOT_WAIT_TIME = 5  # saniye

# Retry mekanizması
SCREENSHOT_RETRY_COUNT = 3
SCREENSHOT_RETRY_DELAY = 2  # saniye

# ============================================================================
# DOSYA AYARLARI
# ============================================================================
STATE_FILE = "state.json"
SCREENSHOTS_DIR = "screenshots"
LOGS_DIR = "logs"

# ============================================================================
# VERI ÇEKME AYARLARI
# ============================================================================
# tvdatafeed ile kaç bar çekeceğimiz
BARS_TO_FETCH = 400

# ============================================================================
# MESAJ AYARLARI
# ============================================================================
# Telegram mesajlarında kullanılacak emojiler
EMOJI_FULL_SIGNAL = "🚀"
EMOJI_SMI_SIGNAL = "🟡"
EMOJI_INFO = "ℹ️"
EMOJI_ERROR = "❌"
EMOJI_SUCCESS = "✅"

# ============================================================================
# LOGLAMA AYARLARI
# ============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
