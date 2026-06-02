"""
Taramabot Konfigürasyon Modülü
Ortam değişkenleri ve sistem ayarlarını yönetir.
"""

import os
import json
import logging
from dotenv import load_dotenv
import borsapy as bp
from datetime import time
from typing import List, Tuple

# .env dosyasını yükle
load_dotenv()

# ============================================================================
# TELEGRAM KONFİGÜRASYONU
# ============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID")
TELEGRAM_THREAD_ID = os.getenv("TG_THREAD_ID")

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
SCAN_TIMES = ["09:30", "11:00", "12:30", "14:00", "15:30", "17:15", "18:30"]

# Haftasonu tarama yapılsın mı?
SCAN_WEEKENDS = False

# ============================================================================
# SEMBOL LİSTELERİ
# ============================================================================

def load_symbols():
    """Sembol listelerini yükler. BIST için borsapy'den güncel listeyi çekmeye çalışır."""
    symbols = {"NASDAQ_100": [], "SP_500": [], "BIST_ALL": []}
    
    # 1. Sabit dosyadan diğer sembolleri yükle (NASDAQ, SP500 vb.)
    try:
        if os.path.exists("all_symbols.json"):
            with open("all_symbols.json", "r", encoding="utf-8") as f:
                file_symbols = json.load(f)
                symbols.update(file_symbols)
    except Exception as e:
        print(f"Sembol dosyası yükleme hatası: {e}")

    # 2. BIST sembollerini borsapy'den dinamik olarak çek
    try:
        print("BIST sembolleri borsapy üzerinden güncelleniyor...")
        bist_all = bp.Index("XUTUM").component_symbols
        if bist_all and len(bist_all) > 0:
            symbols["BIST_ALL"] = bist_all
            print(f"borsapy üzerinden {len(bist_all)} BIST sembolü başarıyla çekildi.")
        else:
            print("borsapy boş liste döndürdü, mevcut listeden devam ediliyor.")
    except Exception as e:
        print(f"borsapy sembol çekme hatası: {e}. Mevcut liste kullanılacak.")
    
    return symbols

ALL_SYMBOLS = load_symbols()

# BIST STOKLARI (Dinamik liste)
BIST_STOCKS = ALL_SYMBOLS.get("BIST_ALL", [])

# ABD STOKLARI
NASDAQ_100 = ALL_SYMBOLS.get("NASDAQ_100", [])
SP_500 = ALL_SYMBOLS.get("SP_500", [])

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

# Yeni RSI + MACD + Hacim Taraması
NEW_RSI_PERIOD = 14
NEW_RSI_UP_THRESHOLD = 50
NEW_RSI_MAX_THRESHOLD = 70
NEW_VOLUME_RATIO = 1.5

# Yeni Tarama (Scanner 3) Ayarları
SMA_5 = 5
SMA_8 = 8
SMA_21 = 21
SMA_50 = 50
SMA_55 = 55
SMA_200 = 200
MACD_LEVEL_THRESHOLD = 0
VOLUME_RATIO_THRESHOLD = 1.5

# EMA Dizilimi Ayarları
EMA_5 = 5
EMA_8 = 8
EMA_13 = 13
EMA_21 = 21
EMA_55 = 55
EMA_200 = 200

# ============================================================================
# EKRAN GÖRÜNTÜSÜ AYARLARI (DEVRE DIŞI)
# ============================================================================
ENABLE_SCREENSHOTS = False

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
EMOJI_RSI_SIGNAL = "🔵"
EMOJI_NEW_SCAN_SIGNAL = "🟣"
EMOJI_RSI_MACD_SIGNAL = "🟢"
EMOJI_EMA_SIGNAL = "🟠"
EMOJI_INFO = "ℹ️"
EMOJI_ERROR = "❌"
EMOJI_SUCCESS = "✅"

# ============================================================================
# LOGLAMA AYARLARI
# ============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
