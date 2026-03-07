import json
import os
import asyncio
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import numpy as np
from playwright.async_api import async_playwright
import logging
from tvDatafeed import TvDatafeed, Interval

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# -----------------------------
# FILES
# -----------------------------
STATE_FILE = "state.json"

# -----------------------------
# TELEGRAM
# -----------------------------
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# -----------------------------
# TRADINGVIEW
# -----------------------------
TV_USERNAME = os.getenv("TV_USERNAME")
TV_PASSWORD = os.getenv("TV_PASSWORD")
TV_CHART_ID = os.getenv("TV_CHART_ID")

# -----------------------------
# STATE
# -----------------------------
def load_state():
    if not os.path.exists(STATE_FILE):
        logging.info(f"State file not found: {STATE_FILE}. Initializing new state.")
        return {"last_sent": {}}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logging.error(f"Error loading or parsing state file {STATE_FILE}: {e}. Initializing new state.")
        return {"last_sent": {}}

    if "last_sent" not in state:
        state["last_sent"] = {}

    return state

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        logging.info(f"State saved to {STATE_FILE}.")
    except Exception as e:
        logging.error(f"Error saving state file {STATE_FILE}: {e}")

# -----------------------------
# TELEGRAM
# -----------------------------
def tg_send_message(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        logging.warning(f"Telegram Config Missing. Message: {text}")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": True, "parse_mode": "HTML"},
            timeout=10,
        )
        r.raise_for_status()
        logging.info(f"Telegram message sent: {text[:50]}...")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending Telegram message: {e}")

def tg_send_photo(photo_path: str, caption: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        logging.warning(f"Telegram Config Missing. Photo: {photo_path}, Caption: {caption}")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as f:
            r = requests.post(
                url,
                data={"chat_id": TG_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                files={"photo": f},
                timeout=30,
            )
        r.raise_for_status()
        logging.info(f"Telegram photo sent: {photo_path}")
    except (requests.exceptions.RequestException, FileNotFoundError) as e:
        logging.error(f"Error sending Telegram photo {photo_path}: {e}")

# -----------------------------
# TRADINGVIEW SCREENSHOT
# -----------------------------
async def get_tv_screenshot(symbol, exchange, interval_str):
    tv_intervals = {"4H": "240", "1D": "D", "1W": "W"}
    tv_interval = tv_intervals.get(interval_str, "D")
    
    os.makedirs("screenshots", exist_ok=True)
    screenshot_path = f"screenshots/{symbol}_{interval_str}.png"
    
    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            context = await browser.new_context(viewport={'width': 1280, 'height': 720})
            page = await context.new_page()
            
            if TV_USERNAME and TV_PASSWORD:
                logging.info(f"Attempting to log in to TradingView for {symbol}...")
                await page.goto("https://www.tradingview.com/accounts/signin/")
                await page.fill("input[name='username']", TV_USERNAME)
                await page.fill("input[name='password']", TV_PASSWORD)
                await page.click("button[type='submit']")
                try:
                    await page.wait_for_url("https://www.tradingview.com/", timeout=10000)
                    logging.info("TradingView login successful.")
                except Exception:
                    logging.warning("TradingView login might have failed.")
            
            chart_url = f"https://www.tradingview.com/chart/{TV_CHART_ID}/?symbol={exchange}:{symbol}&interval={tv_interval}" if TV_CHART_ID else f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol}&interval={tv_interval}"
            logging.info(f"Navigating to chart: {chart_url}")
            await page.goto(chart_url, wait_until="networkidle")
            
            await page.wait_for_selector(".chart-container", timeout=15000)
            await asyncio.sleep(5)
            
            await page.screenshot(path=screenshot_path)
            logging.info(f"Screenshot saved to {screenshot_path}")
            return screenshot_path
        except Exception as e:
            logging.error(f"TV Screenshot Error for {symbol} ({interval_str}): {e}", exc_info=True)
            return None
        finally:
            if browser:
                await browser.close()

# -----------------------------
# CALCULATIONS
# -----------------------------
def make_tv():
    return TvDatafeed(username=TV_USERNAME, password=TV_PASSWORD) if TV_USERNAME and TV_PASSWORD else TvDatafeed()

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
    except Exception as e:
        logging.error(f"Error fetching history for {sym}: {e}")
        return None

    if df is None or df.empty:
        return None

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
        "Full_BUY_Signal": full_buy_signal, "SMI_MACD_BUY": smi_macd_buy,
        "bar_time": df.index[-1].isoformat()
    }

# -----------------------------
# MAIN
# -----------------------------
async def async_main():
    if len(sys.argv) < 3:
        sys.exit(0)

    PERIOD = sys.argv[2].upper()
    MARKET_TYPE = sys.argv[3].lower() if len(sys.argv) > 3 else "bist"
    
    interval_map = {"4H": Interval.in_4_hour, "1W": Interval.in_weekly, "1D": Interval.in_daily}
    INTERVAL = interval_map.get(PERIOD, Interval.in_daily)

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

    state = load_state()
    results = []
    for sym, exc in SYMBOLS:
        row = process_symbol(sym, exc, INTERVAL, 400, PERIOD)
        if row and (row['Full_BUY_Signal'] or row['SMI_MACD_BUY']):
            last_sent_time = state["last_sent"].get(f"{sym}_{PERIOD}")
            if last_sent_time and last_sent_time == row["bar_time"]:
                logging.info(f"Signal for {sym} ({PERIOD}) already sent for this bar. Skipping.")
                continue
            results.append(row)
        await asyncio.sleep(1.2)

    if results:
        full_list = [f"🚀 <b>{r['Symbol']}</b> | %{r['Change']:.2f} | {r['Close']:.2f}" for r in results if r['Full_BUY_Signal']]
        smi_list = [f"🟡 <b>{r['Symbol']}</b> | %{r['Change']:.2f} | {r['Close']:.2f}" for r in results if not r['Full_BUY_Signal']]
        
        summary_msg = f"📊 <b>{PERIOD} TARAMA SONUÇLARI</b>\n\n"
        if full_list: summary_msg += "✅ <b>TAM ALIM SİNYALLERİ:</b>\n" + "\n".join(full_list) + "\n\n"
        if smi_list: summary_msg += "🟡 <b>SMI ALIM SİNYALLERİ:</b>\n" + "\n".join(smi_list)
        
        tg_send_message(summary_msg)
        await asyncio.sleep(2)

        for r in results:
            caption = f"#{r['Symbol']} ({r['Period']}) | Fiyat: {r['Close']:.2f}"
            screenshot_path = await get_tv_screenshot(r['Symbol'], r['Exchange'], r['Period'])
            if screenshot_path:
                tg_send_photo(screenshot_path, caption=caption)
                state["last_sent"][f"{r['Symbol']}_{r['Period']}"] = r["bar_time"]
                await asyncio.sleep(2.0)
            else:
                tg_send_message(f"Grafik alınamadı: {caption}")
        save_state(state)
    else:
        tg_send_message(f"ℹ️ {PERIOD} taramasında sinyal bulunamadı.")

if __name__ == "__main__":
    import sys
    asyncio.run(async_main())
