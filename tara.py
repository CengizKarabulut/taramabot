# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")  # Grafik motoru hatasını önle

from tvDatafeed import TvDatafeed, Interval
import pandas as pd
import time
import sys
import sqlite3
import os
import requests
import mplfinance as mpf
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Yardımcı dosyalar
try:
    from telegram_utils import send_message as tg_send_message
    from telegram_utils import send_photo as tg_send_photo
except ImportError:
    pass

load_dotenv()

# =========================
# GÜVENLİK
# =========================
TV_USER = os.getenv("TV_USER")
TV_PASS = os.getenv("TV_PASS")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TV_USER or not TV_PASS:
    print("⚠️ UYARI: TV_USER veya TV_PASS bulunamadı. Misafir moduyla devam edilecek.")

# =========================
# TELEGRAM YARDIMCISI
# =========================
def tg_enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

def tg_send_message(text: str):
    if not tg_enabled(): return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text})
    except: pass

def tg_send_photo(image_path: str, caption: str = ""):
    if not tg_enabled(): return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(image_path, "rb") as f:
            requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, files={"photo": f})
    except: pass

def tg_send_document(file_path: str):
    if not tg_enabled(): return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        with open(file_path, "rb") as f:
            requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": "📂 Tarama Raporu"}, files={"document": f})
            print(">> Dosya gönderildi.")
    except Exception as e:
        print(f"Dosya gönderme hatası: {e}")

# =========================
# 🎨 PROFESYONEL GRAFİK ÇİZİMİ (SİYAH TEMA + İNDİKATÖRLER)
# =========================
def make_candle_chart(df, out_png: str, title: str):
    try:
        os.makedirs(os.path.dirname(out_png), exist_ok=True)
        
        # 1. Stil Ayarları (Karanlık Mod)
        mc = mpf.make_marketcolors(
            up='#00ff00', down='#ff0000',   # Yeşil/Kırmızı Mumlar
            edge='inherit', wick='inherit', 
            volume='in', ohlc='i'
        )
        s = mpf.make_mpf_style(
            base_mpf_style='nightclouds',   # Siyah/Koyu Tema
            marketcolors=mc, 
            gridstyle=':', 
            y_on_right=True
        )

        addplots = []

        # PANEL 0: Fiyat Üzerine Hareketli Ortalama (Mor Çizgi)
        if 'MA200' in df.columns:
            if not df['MA200'].isnull().all():
                addplots.append(mpf.make_addplot(df['MA200'], color='#E377C2', width=2.0)) 

        # PANEL 2: SMI (Hacim Panel 1'dir)
        if 'SMI' in df.columns and 'SMI_EMA' in df.columns:
            addplots.append(mpf.make_addplot(df['SMI'], panel=2, color='lime', width=1.5, ylabel='SMI'))
            addplots.append(mpf.make_addplot(df['SMI_EMA'], panel=2, color='orange', width=1.5))
            # Referans çizgileri
            addplots.append(mpf.make_addplot([40]*len(df), panel=2, color='gray', linestyle='--', width=0.8))
            addplots.append(mpf.make_addplot([-40]*len(df), panel=2, color='gray', linestyle='--', width=0.8))

        # PANEL 3: MACD
        if 'MACD' in df.columns and 'Signal' in df.columns:
            addplots.append(mpf.make_addplot(df['MACD'], panel=3, color='cyan', width=1.5, ylabel='MACD'))
            addplots.append(mpf.make_addplot(df['Signal'], panel=3, color='red', width=1.5))
            if 'Hist' in df.columns:
                 # Histogramı renklendir
                 hist_colors = ['green' if v >= 0 else 'red' for v in df['Hist']]
                 addplots.append(mpf.make_addplot(df['Hist'], panel=3, type='bar', color=hist_colors, alpha=0.6))

        # Çizim Yap
        mpf.plot(
            df,
            type="candle",
            style=s,
            title=title,
            volume=True,
            addplot=addplots,
            panel_ratios=(3, 1, 1, 1),
            savefig=dict(fname=out_png, dpi=100, bbox_inches='tight'),
            tight_layout=True,
            datetime_format='%b %d',
            xrotation=20
        )
    except Exception as e:
        print(f"Grafik hatası ({title}): {e}")
        # Hata olursa basit çiz
        try: mpf.plot(df, type="candle", volume=True, title=title, savefig=out_png, style='yahoo')
        except: pass

# =========================
# VERİTABANI
# =========================
DB_NAME = "market_tarama.db"

def b2i(x): return int(bool(x))

def init_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tarama_sonuclari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarama_tarihi DATETIME, symbol TEXT, exchange TEXT, period TEXT, close_price REAL,
            full_buy_signal INTEGER, smi_macd_buy INTEGER, status TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_result(row):
    if not row: return
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO tarama_sonuclari (tarama_tarihi, symbol, exchange, period, close_price, full_buy_signal, smi_macd_buy, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (datetime.now(), row['Symbol'], row['Exchange'], row['Period'], row['Close'], 
              1 if row['Full_BUY_Signal'] else 0, 1 if row['SMI_MACD_BUY'] else 0, row['Status']))
        conn.commit()
    except: pass
    conn.close()

# =========================
# BAĞLANTI VE HESAPLAMALAR
# =========================
def make_tv():
    # Parametresiz çağırarak misafir modunu zorluyoruz (daha az hata için)
    return TvDatafeed()

tv = make_tv()

def ema(s, l): return s.ewm(span=l, adjust=False).mean()
def ema2(s, l): return ema(ema(s, l), l)

def calc_smi(df):
    hh = df["high"].rolling(10).max()
    ll = df["low"].rolling(10).min()
    rng = (hh - ll).replace(0, 0.000001)
    rel = df["close"] - (hh + ll) / 2
    smi = 200 * (ema2(rel, 3) / ema2(rng, 3))
    smi_ema = ema(smi, 3)
    return smi, smi_ema

def process_symbol(sym, exchange, interval, n_bars):
    global tv 
    try:
        df = tv.get_hist(sym, exchange, interval=interval, n_bars=n_bars)
    except:
        print(f"⚠️ Bağlantı hatası ({sym}), tekrar deneniyor...")
        # Kısa bir bekleme ve yeniden bağlanma
        time.sleep(2)
        try:
            tv = make_tv()
            df = tv.get_hist(sym, exchange, interval=interval, n_bars=n_bars)
        except:
            return None, "CONNECTION_ERROR"

    if df is None or df.empty:
        return None, "NO_DATA"

    try:
        smi, smi_ema = calc_smi(df)
        
        macd = ema(df["close"], 12) - ema(df["close"], 26)
        signal = ema(macd, 9)
        hist = macd - signal

        df["SMI"], df["SMI_EMA"], df["HIST"] = smi, smi_ema, hist
        
        last, prev = df.iloc[-1], df.iloc[-2]
        cross_up = prev["SMI"] <= prev["SMI_EMA"] and last["SMI"] > last["SMI_EMA"]
        smi_neg = last["SMI"] < 0
        hist_neg = last["HIST"] < 0
        hist_up = last["HIST"] > prev["HIST"]
        
        ma200 = df["close"].rolling(200).mean().iloc[-1] if len(df) > 200 else 0
        above_ma200 = last["close"] > ma200
        vol_ma = df["volume"].rolling(20).mean().iloc[-1]
        vol_ok = last["volume"] > (vol_ma * 1.5) if vol_ma > 0 else False

        smi_macd_buy = cross_up and smi_neg and hist_neg and hist_up
        full_buy_signal = smi_macd_buy and above_ma200 and vol_ok
        sell_signal = (prev["SMI"] >= prev["SMI_EMA"] and last["SMI"] < last["SMI_EMA"] and last["SMI"] > 40)

        row = {
            "Symbol": sym, "Exchange": exchange, "Period": sys.argv[2] if len(sys.argv)>2 else "1D", 
            "Date": df.index[-1], "Close": last["close"],
            "SMI": last["SMI"], "SMI_EMA": last["SMI_EMA"], "MACD_Hist": last["HIST"],
            "Full_BUY_Signal": full_buy_signal, "SMI_MACD_BUY": smi_macd_buy, "Status": "OK",
            "SELL_Signal": sell_signal
        }
        return row, None
    except:
        return None, "CALC_ERROR"

# =========================
# ANA KOD
# =========================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Kullanım: python tara.py tarama 1D bist")
        sys.exit(0)

    PERIOD = sys.argv[2].upper()
    MARKET_TYPE = sys.argv[3].lower() if len(sys.argv) > 3 else "bist"
    
    INTERVAL = Interval.in_4_hour if PERIOD == "4H" else Interval.in_daily
    N_BARS = 400

    BATCH_SIZE = 50
    SLEEP_BETWEEN_SYMBOLS = 2.0  # GÜVENLİ HIZ (Ban yememek için)
    SLEEP_BETWEEN_BATCH = 5
    TG_MAX_ITEMS = 25  # Grafik limiti

    # LİSTELER
    BIST_STOCKS = [
        "AKBNK", "ALARK", "ARCLK", "ASELS", "ASTOR", "BIMAS", "BRSAN", "DOAS", "EGEEN", 
        "EKGYO", "ENKAI", "EREGL", "FROTO", "GARAN", "GUBRF", "HEKTS", "ISCTR", "KCHOL", 
        "KONTR", "KOZAL", "KRDMD", "ODAS", "OYAKC", "PETKM", "PGSUS", "SAHOL", "SASA", 
        "SISE", "TCELL", "THYAO", "TOASO", "TSKB", "TTKOM", "TUPRS", "VAKBN", "YKBNK",
        "CANTE", "EUPWR", "GESAN", "SMRTG", "YEOTK", "MIATK", "ALFAS", "CWENE", "SDTTR",
        "ONCSM", "KMPUR", "KLSER", "KCAER", "AGROT", "KBORU", "TARKM", "MEGMT", "CVKMD",
        "ZOREN", "VESTL", "TKFEN", "SOKM", "SKBNK", "OTKAR", "MGROS", "KOZAA", "KORDS",
        "ULKER", "TKNSA", "TRGYO", "ISGYO", "AKSEN", "GWIND", "MAVI", "AEFES", "CCOLA"
        # ... Buraya kendi uzun BIST listenizi ekleyebilirsiniz ...
    ]
    COMMODITIES = [("XAUUSD", "OANDA"), ("XAGUSD", "OANDA"), ("WTICOUSD", "OANDA"), ("BCOUSD", "OANDA")]
    INDICES = [("XU100", "BIST"), ("XU030", "BIST")]
    CRYPTO = [("BTCUSD", "BINANCE"), ("ETHUSD", "BINANCE"), ("SOLUSD", "BINANCE")]

    # --- HATA DÜZELTME: MARKET_NAME TANIMLAMASI ---
    if MARKET_TYPE == "bist": 
        SYMBOLS = [(s, "BIST") for s in BIST_STOCKS]
        MARKET_NAME = "BIST Hisseleri"
    elif MARKET_TYPE == "emtia": 
        SYMBOLS = COMMODITIES
        MARKET_NAME = "Emtialar"
    elif MARKET_TYPE == "endeks": 
        SYMBOLS = INDICES
        MARKET_NAME = "Endeksler"
    elif MARKET_TYPE == "kripto": 
        SYMBOLS = CRYPTO
        MARKET_NAME = "Kripto Paralar"
    elif MARKET_TYPE == "all": 
        SYMBOLS = ([(s, "BIST") for s in BIST_STOCKS] + COMMODITIES + INDICES + CRYPTO)
        MARKET_NAME = "Tüm Piyasalar"
    else: 
        # Varsayılan
        SYMBOLS = [(s, "BIST") for s in BIST_STOCKS]
        MARKET_NAME = "BIST Hisseleri"

    print(f"--- TARAMA BAŞLADI: {MARKET_NAME} ({len(SYMBOLS)} sembol) ---")
    init_database()
    
    results = []
    
    # Taramayı Başlat
    for i, (sym, exc) in enumerate(SYMBOLS):
        print(f"[{i+1}/{len(SYMBOLS)}] {sym}...", end="\r")
        row, err = process_symbol(sym, exc, INTERVAL, N_BARS)
        if row:
            results.append(row)
            save_result(row)
        else:
            if err != "NO_DATA": pass
        time.sleep(SLEEP_BETWEEN_SYMBOLS)

    # Sonuçlar ve Telegram
    if results:
        df_res = pd.DataFrame(results)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        csv_name = f"tarama_{MARKET_TYPE}_{timestamp}.csv"
        df_res.to_csv(csv_name, index=False)
        
        full_buys = df_res[df_res["Full_BUY_Signal"] == True]
        smi_buys = df_res[df_res["SMI_MACD_BUY"] == True]
        
        msg = (f"📢 Tarama Bitti ({MARKET_NAME})\n"
               f"✅ Tam Alım: {len(full_buys)}\n"
               f"🟡 SMI Alım: {len(smi_buys)}\n"
               f"📂 Dosya: {csv_name}")
        
        tg_send_message(msg)
        tg_send_document(csv_name)
        
        # Grafik Gönderme Kuralı
        target_list = full_buys
        header = "✅ TAM ALIM GRAFİKLERİ"
        if target_list.empty and not smi_buys.empty:
            target_list = smi_buys
            header = "🟡 SMI ALIM GRAFİKLERİ (Tam Alım Yok)"
            
        if not target_list.empty:
            tg_send_message(f"{header} (İlk {TG_MAX_ITEMS} adet):")
            
            # --- GELİŞMİŞ GRAFİK DÖNGÜSÜ ---
            for _, r in target_list.head(TG_MAX_ITEMS).iterrows():
                sym = r["Symbol"]
                
                # Grafiği yeniden çek (daha uzun periyot ile, MA200 için)
                df_chart = tv.get_hist(sym, r["Exchange"], interval=INTERVAL, n_bars=300)
                
                if df_chart is not None and not df_chart.empty:
                    try:
                        # İndikatörleri Hesapla ve Grafiğe Ekle
                        smi, smi_ema = calc_smi(df_chart)
                        df_chart['SMI'] = smi
                        df_chart['SMI_EMA'] = smi_ema
                        
                        macd = ema(df_chart["close"], 12) - ema(df_chart["close"], 26)
                        signal = ema(macd, 9)
                        hist = macd - signal
                        df_chart['MACD'] = macd
                        df_chart['Signal'] = signal
                        df_chart['Hist'] = hist
                        
                        df_chart['MA200'] = df_chart['close'].rolling(200).mean()
                        
                        # Son 120 barı çiz (Okunabilir olması için)
                        df_plot = df_chart.tail(120)
                        
                        img_path = f"outputs/charts/{sym}.png"
                        # Yeni PRO Fonksiyonu Çağır (Karanlık Mod + İndikatörler)
                        make_candle_chart(df_plot, img_path, f"{sym} ({PERIOD})")
                        
                        tg_send_photo(img_path, caption=f"#{sym} - {r['Close']}")
                        time.sleep(TG_SLEEP_BETWEEN_PHOTOS)
                    except Exception as e:
                        print(f"Hata {sym}: {e}")

    else:
        print("\n❌ Hiçbir veri çekilemedi.")
        tg_send_message(f"⚠️ {MARKET_NAME} taraması başarısız.")

    print("\nİşlem Tamam.")

elif KOMUT == "backtest":
    print("Backtest Modu")
elif KOMUT == "rapor":
    print("Rapor Modu")
else:
    print("Geçersiz komut.")
