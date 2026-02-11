# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")  # Grafik motoru sunucuda hata vermesin

from tvDatafeed import TvDatafeed, Interval
import pandas as pd
import time
import sys
import sqlite3
import os
from datetime import datetime
from dotenv import load_dotenv

# Yardımcı dosyalarımızı çağırıyoruz
try:
    from telegram_utils import send_message as tg_send_message
    from telegram_utils import send_photo as tg_send_photo
    from chart_utils import make_candle_chart
except ImportError:
    print("YARDIMCI DOSYALAR EKSİK! (telegram_utils.py veya chart_utils.py bulunamadı)")
    sys.exit(1)

load_dotenv()

# =========================
# AYARLAR (HIZ VE PERFORMANS)
# =========================
# Bekleme süresini 2.0 saniyeden 0.5 saniyeye düşürdük. Çok daha hızlı olacak.
SLEEP_BETWEEN_SYMBOLS = 0.5 
SLEEP_BETWEEN_BATCH = 5      # Her 30 hissede bir verilen molayı 20sn'den 5sn'ye indirdik.
BATCH_SIZE = 50              # Tek seferde işlenen hisse sayısı

# İndikatör Ayarları
lengthK, lengthD, lengthEMA = 10, 3, 3
macd_fast, macd_slow, macd_signal = 12, 26, 9
SMI_SELL_THRESHOLD = 40
VOLUME_MULTIPLIER = 1.5

# Telegram Spam Kontrolü
TG_MAX_ITEMS = 15            # En fazla 15 grafik gönder
TG_SLEEP_BETWEEN_PHOTOS = 1.0

# =========================
# GÜVENLİK
# =========================
TV_USER = os.getenv("TV_USER")
TV_PASS = os.getenv("TV_PASS")

if not TV_USER or not TV_PASS:
    print("HATA: TV_USER ve TV_PASS ayarlanmamış!")
    sys.exit(1)

# =========================
# VERİTABANI
# =========================
DB_NAME = "market_tarama.db"

def init_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tarama_sonuclari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarama_tarihi DATETIME,
            symbol TEXT,
            exchange TEXT,
            period TEXT,
            close_price REAL,
            buy_signal INTEGER,
            sell_signal INTEGER,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_result(row):
    if not row: return
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO tarama_sonuclari (tarama_tarihi, symbol, exchange, period, close_price, buy_signal, sell_signal, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (datetime.now(), row['Symbol'], row['Exchange'], row['Period'], row['Close'], 
          1 if row['Full_BUY_Signal'] else 0, 
          1 if row['SELL_Signal'] else 0, 
          row['Status']))
    conn.commit()
    conn.close()

# =========================
# TV BAĞLANTISI
# =========================
tv = TvDatafeed(username=TV_USER, password=TV_PASS)

def renew_tv():
    global tv
    try:
        tv = TvDatafeed(username=TV_USER, password=TV_PASS)
    except:
        pass

# =========================
# HESAPLAMALAR
# =========================
def ema(s, l): return s.ewm(span=l, adjust=False).mean()
def ema2(s, l): return ema(ema(s, l), l)

def calc_indicators(df):
    # SMI
    hh = df["high"].rolling(lengthK).max()
    ll = df["low"].rolling(lengthK).min()
    rng = hh - ll
    rel = df["close"] - (hh + ll) / 2
    
    # Sıfıra bölme hatasını önle
    rng = rng.replace(0, 0.000001)
    
    smi = 200 * (ema2(rel, lengthD) / ema2(rng, lengthD))
    smi_ema = ema(smi, lengthEMA)
    
    # MACD
    macd = ema(df["close"], macd_fast) - ema(df["close"], macd_slow)
    signal = ema(macd, macd_signal)
    hist = macd - signal
    
    return smi, smi_ema, hist

# =========================
# İŞLEM MANTIĞI
# =========================
def process_symbol(sym, exchange, interval, n_bars):
    try:
        df = tv.get_hist(sym, exchange, interval=interval, n_bars=n_bars)
    except:
        renew_tv()
        return None, "CONNECTION_ERROR"

    if df is None or df.empty or len(df) < 50:
        return None, "NO_DATA"

    # İndikatörleri hesapla
    smi, smi_ema, hist = calc_indicators(df)
    
    df["SMI"] = smi
    df["SMI_EMA"] = smi_ema
    df["HIST"] = hist
    
    # Son veriler
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # Sinyal Kontrolleri
    cross_up = prev["SMI"] <= prev["SMI_EMA"] and last["SMI"] > last["SMI_EMA"]
    smi_neg = last["SMI"] < 0
    hist_neg = last["HIST"] < 0
    hist_up = last["HIST"] > prev["HIST"]
    
    # Hacim Kontrolü
    vol_ma = df["volume"].rolling(20).mean().iloc[-1]
    vol_ok = last["volume"] > (vol_ma * VOLUME_MULTIPLIER) if vol_ma > 0 else False

    # MA200 Kontrolü (Basit trend)
    ma200 = df["close"].rolling(200).mean().iloc[-1] if len(df) > 200 else 0
    above_ma200 = last["close"] > ma200

    # Karar
    smi_macd_buy = cross_up and smi_neg and hist_neg and hist_up
    full_buy = smi_macd_buy and above_ma200 and vol_ok
    
    sell_signal = (prev["SMI"] >= prev["SMI_EMA"] and last["SMI"] < last["SMI_EMA"] and last["SMI"] > SMI_SELL_THRESHOLD)

    return {
        "Symbol": sym,
        "Exchange": exchange,
        "Period": sys.argv[2] if len(sys.argv) > 2 else "1D",
        "Close": last["close"],
        "SMI": last["SMI"],
        "Full_BUY_Signal": full_buy,
        "SMI_MACD_BUY": smi_macd_buy,
        "SELL_Signal": sell_signal,
        "Status": "OK"
    }, None

# =========================
# ANA AKIŞ
# =========================
if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "tarama":
        print("Kullanım: python tara.py tarama 1D all")
        sys.exit(0)

    # Parametreler
    PERIOD = sys.argv[2].upper()
    MARKET = sys.argv[3].lower() if len(sys.argv) > 3 else "bist"
    
    INTERVAL = Interval.in_4_hour if PERIOD == "4H" else Interval.in_daily
    N_BARS = 300

    # Sembol Listesi (Örnek olarak kısaltılmıştır, siz tam listeyi buraya ekleyebilirsiniz)
    # Hız testi için şimdilik BIST30 kullanabilirsiniz veya tam listeyi eski dosyanızdan alabilirsiniz.
    # Ben buraya BIST30 örneği koyuyorum, eski dosyadaki o UZUN listeyi buraya 'SYMBOLS =' kısmına yapıştırmalısın.
    BIST_LIST = [
        "AKBNK", "ALARK", "ARCLK", "ASELS", "ASTOR", "BIMAS", "BRSAN", "DOAS", "EGEEN", 
        "EKGYO", "ENKAI", "EREGL", "FROTO", "GARAN", "GUBRF", "HEKTS", "ISCTR", "KCHOL", 
        "KONTR", "KOZAL", "KRDMD", "ODAS", "OYAKC", "PETKM", "PGSUS", "SAHOL", "SASA", 
        "SISE", "TCELL", "THYAO", "TOASO", "TSKB", "TTKOM", "TUPRS", "VAKBN", "YKBNK"
    ]
    
    # Eğer "all" seçildiyse hepsini tarayacak şekilde ayarlayabilirsin.
    # Şimdilik basitlik adına sadece BIST listesini işliyoruz.
    SYMBOLS = [(s, "BIST") for s in BIST_LIST]

    print(f"--- TARAMA BAŞLADI: {len(SYMBOLS)} sembol ---")
    init_database()
    
    results = []
    
    for i, (sym, exc) in enumerate(SYMBOLS):
        print(f"[{i+1}/{len(SYMBOLS)}] {sym} taranıyor...", end="\r")
        row, err = process_symbol(sym, exc, INTERVAL, N_BARS)
        
        if row:
            results.append(row)
            save_result(row)
            
            # Sinyal varsa grafik gönder
            if row["Full_BUY_Signal"] or row["SMI_MACD_BUY"]:
                 print(f"\n >>> {sym} SİNYAL BULUNDU!")
                 # Grafik oluştur ve gönder (Spam olmasın diye her sinyalde değil, toplu da atılabilir)
                 # Şimdilik burayı boş geçiyoruz, toplu raporu aşağıda yollayacağız.
        
        time.sleep(SLEEP_BETWEEN_SYMBOLS)

    # Sonuçları Kaydet
    df_res = pd.DataFrame(results)
    csv_name = f"tarama_sonuc_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    df_res.to_csv(csv_name, index=False)
    
    # Telegram Raporu
    buys = df_res[df_res["Full_BUY_Signal"] == True]
    msg = f"📢 TARAMA BİTTİ\n\n✅ Tam Alım: {len(buys)}\n📂 Dosya: {csv_name}"
    
    try:
        tg_send_message(msg)
        # Sinyal Olanların Grafiğini At (İlk 5 Tane)
        for _, r in buys.head(5).iterrows():
            sym = r["Symbol"]
            df_chart = tv.get_hist(sym, r["Exchange"], interval=INTERVAL, n_bars=100)
            if df_chart is not None:
                img_path = f"chart_{sym}.png"
                make_candle_chart(df_chart, img_path, f"{sym} AL SINYALI")
                tg_send_photo(img_path, caption=f"#{sym} - Fiyat: {r['Close']}")
    except Exception as e:
        print(f"Telegram hatası: {e}")

    print("\nİşlem Tamam.")
