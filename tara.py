# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")

from tvDatafeed import TvDatafeed, Interval
import pandas as pd
import time
import sys
import sqlite3
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
    if not os.path.exists(image_path): return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(image_path, "rb") as f:
            requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}, files={"photo": f})
    except: pass

# =========================
# 🎨 GÖRSELDEKİ STİLDE GRAFİK ÇİZİMİ
# =========================
def make_candle_chart(df, out_png: str, title: str):
    try:
        os.makedirs(os.path.dirname(out_png), exist_ok=True)
        
        # Görseldeki renk paleti ve stil
        mc = mpf.make_marketcolors(up='#00ff00', down='#ff0000', edge='inherit', wick='inherit', volume='in', ohlc='i')
        s = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc, gridstyle=':', y_on_right=True, facecolor='black', edgecolor='#444444', gridcolor='#444444')

        addplots = []

        # MA200 (Pembe Çizgi)
        if 'MA200' in df.columns and not df['MA200'].isnull().all():
            addplots.append(mpf.make_addplot(df['MA200'], color='#E377C2', width=1.5)) 

        # SMI (Lime ve Turuncu)
        if 'SMI' in df.columns and 'SMI_EMA' in df.columns:
            addplots.append(mpf.make_addplot(df['SMI'], panel=2, color='lime', width=1.0, ylabel='SMI'))
            addplots.append(mpf.make_addplot(df['SMI_EMA'], panel=2, color='orange', width=1.0))
            addplots.append(mpf.make_addplot([40]*len(df), panel=2, color='gray', linestyle='--', width=0.5))
            addplots.append(mpf.make_addplot([-40]*len(df), panel=2, color='gray', linestyle='--', width=0.5))

        # MACD (Cyan ve Kırmızı)
        if 'MACD' in df.columns and 'Signal' in df.columns:
            addplots.append(mpf.make_addplot(df['MACD'], panel=3, color='cyan', width=1.0, ylabel='MACD'))
            addplots.append(mpf.make_addplot(df['Signal'], panel=3, color='red', width=1.0))
            if 'Hist' in df.columns:
                 hist_colors = ['green' if v >= 0 else 'red' for v in df['Hist']]
                 addplots.append(mpf.make_addplot(df['Hist'], panel=3, type='bar', color=hist_colors, alpha=0.5))

        # Çizim (Görseldeki gibi sıkışık ve net)
        mpf.plot(
            df, type="candle", style=s, title=dict(title=title, color='white', fontsize=10),
            volume=True, addplot=addplots, panel_ratios=(4, 1, 1, 1),
            savefig=dict(fname=out_png, dpi=150, bbox_inches='tight', facecolor='black'),
            tight_layout=True, datetime_format='%b %d', xrotation=0
        )
        return True
    except Exception as e:
        print(f"Grafik hatası: {e}")
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
    
    if MARKET_TYPE == "bist": SYMBOLS = [(s, "BIST") for s in BIST_STOCKS]
    elif MARKET_TYPE == "emtia": SYMBOLS = [("XAUUSD", "OANDA"), ("XAGUSD", "OANDA")]
    elif MARKET_TYPE == "kripto": SYMBOLS = [("BTCUSD", "BINANCE"), ("ETHUSD", "BINANCE")]
    else: SYMBOLS = [(s, "BIST") for s in BIST_STOCKS]

    results = []
    for sym, exc in SYMBOLS:
        row = process_symbol(sym, exc, INTERVAL, 400, PERIOD)
        if row and (row['Full_BUY_Signal'] or row['SMI_MACD_BUY']):
            results.append(row)
        time.sleep(1.2)

    for r in results:
        # Görseldeki Mesaj Formatı: #HISSE (PERIYOT) | Fiyat: ...
        caption = f"#{r['Symbol']} ({r['Period']}) | Fiyat: {r['Close']:.2f}"
        
        img_path = f"outputs/charts/{r['Symbol']}_{PERIOD}.png"
        df_chart = tv.get_hist(r['Symbol'], r['Exchange'], interval=INTERVAL, n_bars=100)
        if df_chart is not None:
            df_chart['MA200'] = df_chart['close'].rolling(200).mean()
            s, se = calc_smi(df_chart)
            df_chart['SMI'], df_chart['SMI_EMA'] = s, se
            m = ema(df_chart['close'], 12) - ema(df_chart['close'], 26)
            sig = ema(m, 9)
            df_chart['MACD'], df_chart['Signal'], df_chart['Hist'] = m, sig, m - sig
            
            # Grafik başlığına görseldeki gibi bilgi ekle
            chart_title = f"{r['Symbol']} ({r['Period']}) %{r['Change']:.2f} - {r['Close']:.2f}"
            if make_candle_chart(df_chart, img_path, chart_title):
                tg_send_photo(img_path, caption=caption)
                time.sleep(1)
