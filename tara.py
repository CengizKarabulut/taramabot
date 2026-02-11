# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")

from tvDatafeed import TvDatafeed, Interval
import pandas as pd
import time
import sys
import os
import requests
import mplfinance as mpf
from datetime import datetime
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)

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

# =========================
# TELEGRAM YARDIMCISI
# =========================
def tg_enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

def tg_send_message(text: str):
    if not tg_enabled(): return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
    except: pass

def tg_send_photo(image_path: str, caption: str = ""):
    if not tg_enabled(): return
    if not os.path.exists(image_path):
        print(f"HATA: Gönderilecek görsel dosyası bulunamadı: {image_path}")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(image_path, "rb") as f:
            r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}, files={"photo": f})
            if r.status_code != 200:
                print(f"Telegram Fotoğraf Gönderme Hatası: {r.text}")
    except Exception as e:
        print(f"Telegram Fotoğraf Hatası: {e}")

# =========================
# 🎨 GELİŞMİŞ GRAFİK ÇİZİMİ
# =========================
def make_candle_chart(df, out_png: str, title: str):
    try:
        # KLASÖR YÖNETİMİ: Kesin çözüm için klasörü burada oluşturuyoruz
        dir_path = os.path.dirname(out_png)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        
        # Profesyonel Siyah Tema
        mc = mpf.make_marketcolors(up='#26a69a', down='#ef5350', edge='inherit', wick='inherit', volume='in', ohlc='i')
        s = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc, gridstyle=':', y_on_right=True, facecolor='#000000', edgecolor='#222222', gridcolor='#222222')

        addplots = []

        # SMA 20 (Sarı), SMA 50 (Mavi), SMA 200 (Pembe)
        if 'SMA20' in df.columns: addplots.append(mpf.make_addplot(df['SMA20'], color='#FFD700', width=0.8, alpha=0.8))
        if 'SMA50' in df.columns: addplots.append(mpf.make_addplot(df['SMA50'], color='#00BFFF', width=1.0, alpha=0.8))
        if 'SMA200' in df.columns: addplots.append(mpf.make_addplot(df['SMA200'], color='#E377C2', width=1.5))

        # SMI Panel
        if 'SMI' in df.columns:
            addplots.append(mpf.make_addplot(df['SMI'], panel=2, color='#00FF00', width=1.0, ylabel='SMI'))
            addplots.append(mpf.make_addplot(df['SMI_EMA'], panel=2, color='#FFA500', width=1.0))
            addplots.append(mpf.make_addplot([40]*len(df), panel=2, color='#333333', linestyle='--', width=0.5))
            addplots.append(mpf.make_addplot([-40]*len(df), panel=2, color='#333333', linestyle='--', width=0.5))

        # MACD Panel
        if 'MACD' in df.columns:
            addplots.append(mpf.make_addplot(df['MACD'], panel=3, color='#00FFFF', width=1.0, ylabel='MACD'))
            addplots.append(mpf.make_addplot(df['Signal'], panel=3, color='#FF4500', width=1.0))
            hist_colors = ['#26a69a' if v >= 0 else '#ef5350' for v in df['Hist']]
            addplots.append(mpf.make_addplot(df['Hist'], panel=3, type='bar', color=hist_colors, alpha=0.4))

        mpf.plot(
            df, type="candle", style=s, title=dict(title=title, color='white', fontsize=11),
            volume=True, addplot=addplots, panel_ratios=(4, 1, 1, 1),
            savefig=dict(fname=out_png, dpi=150, bbox_inches='tight', facecolor='black'),
            tight_layout=True, datetime_format='%d %b', xrotation=0
        )
        return True
    except Exception as e:
        print(f"Grafik oluşturma hatası: {e}")
        return False

# =========================
# HESAPLAMALAR
# =========================
def make_tv():
    if TV_USER and TV_PASS:
        try: return TvDatafeed(username=TV_USER, password=TV_PASS)
        except: return TvDatafeed()
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

def process_symbol(sym, exchange, interval, n_bars, period_str):
    global tv 
    try:
        df = tv.get_hist(sym, exchange, interval=interval, n_bars=n_bars)
    except:
        time.sleep(2)
        try:
            tv = make_tv()
            df = tv.get_hist(sym, exchange, interval=interval, n_bars=n_bars)
        except: return None

    if df is None or df.empty: return None

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

        prev_close = df.iloc[-2]['close']
        current_close = last['close']
        change_percent = ((current_close - prev_close) / prev_close) * 100

        smi_macd_buy = cross_up and smi_neg and hist_neg and hist_up
        full_buy_signal = smi_macd_buy and above_ma200 and vol_ok

        return {
            "Symbol": sym, "Exchange": exchange, "Period": period_str, 
            "Close": last["close"], "Change": change_percent,
            "Full_BUY_Signal": full_buy_signal, "SMI_MACD_BUY": smi_macd_buy
        }
    except: return None

# =========================
# ANA KOD
# =========================
if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(0)

    PERIOD = sys.argv[2].upper()
    MARKET_TYPE = sys.argv[3].lower() if len(sys.argv) > 3 else "bist"
    
    if PERIOD == "4H": INTERVAL = Interval.in_4_hour
    elif PERIOD == "1W": INTERVAL = Interval.in_weekly
    else: INTERVAL = Interval.in_daily

    BIST_STOCKS = [
        "AKBNK", "ALARK", "ARCLK", "ASELS", "ASTOR", "BIMAS", "BRSAN", "DOAS", "EGEEN", 
        "EKGYO", "ENKAI", "EREGL", "FROTO", "GARAN", "GUBRF", "HEKTS", "ISCTR", "KCHOL", 
        "KONTR", "KOZAL", "KRDMD", "ODAS", "OYAKC", "PETKM", "PGSUS", "SAHOL", "SASA", 
        "SISE", "TCELL", "THYAO", "TOASO", "TSKB", "TTKOM", "TUPRS", "VAKBN", "YKBNK",
        "CANTE", "EUPWR", "GESAN", "SMRTG", "YEOTK", "MIATK", "ALFAS", "CWENE", "SDTTR",
        "ONCSM", "KMPUR", "KLSER", "KCAER", "AGROT", "KBORU", "TARKM", "MEGMT", "CVKMD"
    ]
    
    SYMBOLS = [(s, "BIST") for s in BIST_STOCKS]
    if MARKET_TYPE == "emtia": SYMBOLS = [("XAUUSD", "OANDA"), ("XAGUSD", "OANDA")]
    elif MARKET_TYPE == "kripto": SYMBOLS = [("BTCUSD", "BINANCE"), ("ETHUSD", "BINANCE")]

    results = []
    for sym, exc in SYMBOLS:
        row = process_symbol(sym, exc, INTERVAL, 400, PERIOD)
        if row and (row['Full_BUY_Signal'] or row['SMI_MACD_BUY']):
            results.append(row)
        time.sleep(1.2)

    if results:
        # Özet Liste Mesajı
        full_list = [f"🚀 <b>{r['Symbol']}</b> | %{r['Change']:.2f} | {r['Close']:.2f}" for r in results if r['Full_BUY_Signal']]
        smi_list = [f"🟡 <b>{r['Symbol']}</b> | %{r['Change']:.2f} | {r['Close']:.2f}" for r in results if not r['Full_BUY_Signal']]
        
        summary_msg = f"📊 <b>{PERIOD} TARAMA SONUÇLARI</b>\n\n"
        if full_list: summary_msg += "✅ <b>TAM ALIM SİNYALLERİ:</b>\n" + "\n".join(full_list) + "\n\n"
        if smi_list: summary_msg += "🟡 <b>SMI ALIM SİNYALLERİ:</b>\n" + "\n".join(smi_list)
        
        tg_send_message(summary_msg)
        time.sleep(1)

        # Görselleri Tek Tek Gönder
        for r in results:
            caption = f"#{r['Symbol']} ({r['Period']}) | Fiyat: {r['Close']:.2f}"
            # Dosya adını ve yolunu basitleştiriyoruz (Klasör sorununu aşmak için)
            img_name = f"{r['Symbol']}_{PERIOD}.png"
            img_path = os.path.join(os.getcwd(), "outputs", img_name)
            
            df_chart = tv.get_hist(r['Symbol'], r['Exchange'], interval=INTERVAL, n_bars=100)
            if df_chart is not None:
                df_chart['SMA20'] = df_chart['close'].rolling(20).mean()
                df_chart['SMA50'] = df_chart['close'].rolling(50).mean()
                df_chart['SMA200'] = df_chart['close'].rolling(200).mean()
                
                s, se = calc_smi(df_chart)
                df_chart['SMI'], df_chart['SMI_EMA'] = s, se
                m = ema(df_chart['close'], 12) - ema(df_chart['close'], 26)
                sig = ema(m, 9)
                df_chart['MACD'], df_chart['Signal'], df_chart['Hist'] = m, sig, m - sig
                
                chart_title = f"{r['Symbol']} ({r['Period']}) %{r['Change']:.2f} - {r['Close']:.2f}"
                if make_candle_chart(df_chart, img_path, chart_title):
                    tg_send_photo(img_path, caption=caption)
                    time.sleep(1.5) # Telegram spam filtresine takılmamak için süreyi artırdık
    else:
        tg_send_message(f"ℹ️ {PERIOD} taramasında sinyal bulunamadı.")
