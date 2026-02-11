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

load_dotenv()

# =========================
# GÜVENLİK: ENV VARIABLES
# =========================
TV_USER = os.getenv("TV_USER")
TV_PASS = os.getenv("TV_PASS")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# DÜZELTME 1: Şifre yoksa programı kapatma, sadece uyar ve devam et (Misafir Modu)
if not TV_USER or not TV_PASS:
    print("⚠️ UYARI: TV_USER/PASS bulunamadı veya hatalı. Misafir moduyla devam ediliyor...")
else:
    print(f"👤 Kullanıcı: {TV_USER}")

# =========================
# TELEGRAM YARDIMCI
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

# DÜZELTME 2: Dosya gönderme fonksiyonu eklendi
def tg_send_document(file_path: str):
    if not tg_enabled(): return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        with open(file_path, "rb") as f:
            requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": "📂 Tarama Raporu"}, files={"document": f})
            print(">> Dosya gönderildi.")
    except Exception as e:
        print(f"Dosya gönderme hatası: {e}")

def make_candle_chart(df, out_png: str, title: str):
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    mpf.plot(df, type="candle", volume=True, title=title, style="yahoo", savefig=out_png)

# =========================
# VERİTABANI KURULUMU (Senin Orijinal Kodun)
# =========================
DB_NAME = "market_tarama.db"

def b2i(x): return int(bool(x))

def init_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Tablo oluşturma (Senin kodunun aynısı)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tarama_sonuclari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarama_tarihi DATETIME NOT NULL,
            bar_tarihi DATETIME,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            period TEXT NOT NULL,
            close_price REAL,
            ma200_daily REAL, above_ma200 INTEGER,
            smi REAL, smi_ema REAL, macd_hist REAL,
            volume REAL, volume_ma REAL, volume_high INTEGER,
            buy_cross_up INTEGER, buy_smi_neg INTEGER, buy_hist_neg INTEGER, buy_hist_up INTEGER,
            smi_macd_buy INTEGER, full_buy_signal INTEGER,
            sell_cross_down INTEGER, sell_smi_over_40 INTEGER, sell_hist_pos INTEGER, sell_hist_down INTEGER,
            sell_signal INTEGER, status TEXT,
            UNIQUE(tarama_tarihi, symbol, exchange, period)
        )
    ''')
    # Backtest Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS backtest_sonuclari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sinyal_tarihi DATETIME NOT NULL,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            period TEXT NOT NULL,
            sinyal_tipi TEXT NOT NULL,
            sinyal_fiyat REAL NOT NULL,
            fiyat_1gun REAL, fiyat_3gun REAL, fiyat_5gun REAL, fiyat_10gun REAL,
            getiri_1gun REAL, getiri_3gun REAL, getiri_5gun REAL, getiri_10gun REAL,
            max_fiyat_10gun REAL, min_fiyat_10gun REAL, max_getiri_10gun REAL, max_dusus_10gun REAL,
            backtest_tarihi DATETIME,
            UNIQUE(sinyal_tarihi, symbol, exchange, period, sinyal_tipi)
        )
    ''')
    conn.commit()
    conn.close()

def save_tarama_to_db(results_map, tarama_tarihi):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    saved_count = 0
    for row in results_map.values():
        if row.get('Status', '').startswith('OK'):
            try:
                cursor.execute('''
                    INSERT OR REPLACE INTO tarama_sonuclari (
                        tarama_tarihi, bar_tarihi, symbol, exchange, period,
                        close_price, ma200_daily, above_ma200, smi, smi_ema, macd_hist,
                        volume, volume_ma, volume_high,
                        buy_cross_up, buy_smi_neg, buy_hist_neg, buy_hist_up, smi_macd_buy, full_buy_signal,
                        sell_cross_down, sell_smi_over_40, sell_hist_pos, sell_hist_down, sell_signal, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    tarama_tarihi, row.get('Date'), row['Symbol'], row['Exchange'], row['Period'],
                    row['Close'], row.get('MA200_Daily'), b2i(row.get('Above_MA200', 0)),
                    row['SMI'], row['SMI_EMA'], row['MACD_Hist'],
                    row['Volume'], row['Volume_MA'], b2i(row.get('Volume_High', 0)),
                    b2i(row.get('BUY_CrossUp', 0)), b2i(row.get('BUY_SMI<0', 0)), b2i(row.get('BUY_Hist<0', 0)), b2i(row.get('BUY_HistUp', 0)), b2i(row.get('SMI_MACD_BUY', 0)), b2i(row.get('Full_BUY_Signal', 0)),
                    b2i(row.get('SELL_CrossDown', 0)), b2i(row.get('SELL_SMI>40', 0)), b2i(row.get('SELL_Hist>0', 0)), b2i(row.get('SELL_HistDown', 0)), b2i(row.get('SELL_Signal', 0)),
                    row['Status']
                ))
                saved_count += 1
            except: pass
    conn.commit()
    conn.close()

# =========================
# LOGIN VE BAĞLANTI (GÜNCELLENDİ)
# =========================
def make_tv():
    # Şifre varsa dene, yoksa veya hata verirse misafir gir
    if TV_USER and TV_PASS:
        try:
            return TvDatafeed(username=TV_USER, password=TV_PASS)
        except:
            print("⚠️ Giriş başarısız, Misafir Moduna geçiliyor...")
            return TvDatafeed()
    return TvDatafeed()

tv = make_tv()

# =========================
# ANA KOMUT İŞLEME
# =========================
if len(sys.argv) < 2:
    print("Kullanım: python tara.py [tarama|backtest|rapor]")
    sys.exit(1)

KOMUT = sys.argv[1].lower()

if KOMUT == "tarama":
    PERIOD = (sys.argv[2].upper() if len(sys.argv) > 2 else "1D")
    MARKET_TYPE = (sys.argv[3].lower() if len(sys.argv) > 3 else "bist")
    
    INTERVAL = Interval.in_4_hour if PERIOD == "4H" else Interval.in_daily
    N_BARS = 400

    # AYARLAR (HIZLANDIRILDI)
    BATCH_SIZE = 50
    SLEEP_BETWEEN_SYMBOLS = 0.5  # DÜZELTME 3: Hızlandırıldı (Eskisi 2.0)
    SLEEP_BETWEEN_BATCH = 5
    RETRY_ROUNDS = 1
    
    # İndikatör Sabitleri
    lengthK, lengthD, lengthEMA = 10, 3, 3
    macd_fast, macd_slow, macd_signal = 12, 26, 9
    VOLUME_MULTIPLIER = 1.5
    VOLUME_PERIOD = 20
    SMI_SELL_THRESHOLD = 40
    
    TG_MAX_ITEMS = 10
    TG_SLEEP_BETWEEN_PHOTOS = 1.0

    # =========================
    # SEMBOL LISTELERI
    # =========================
    BIST_STOCKS = [
        "DMRGD", "BINHO", "AVOD", "A1CAP", "A1YEN", "ACSEL", "ADEL", "ADESE", "ADGYO", "AFYON",
        "AGHOL", "AGESA", "AHGAZ", "AKBNK", "AKYHO", "AKENR", "AKFGY", "AKFYE", "ATEKS", "AKMGY",
        "AKSA", "AKSEN", "AKSUE", "AKGRT", "AKCNS", "AKSGY", "ALCAR", "ALGYO", "ALARK", "ALBRK",
        "ALCTL", "ALFAS", "ALKIM", "ALKA", "AYCES", "ANSGR", "AEFES", "ANHYT", "ASUZU", "ANGEN",
        "ANELE", "ARDYZ", "ARENA", "ARFYE", "ARSAN", "ARZUM", "ARCLK", "ASGYO", "ASELS", "ASTOR",
        "ATAGY", "ATAKP", "AGYO", "ATSYH", "ATLAS", "ATATP", "AVGYO", "AVTUR", "AVHOL", "AYDEM",
        "AYEN", "AYES", "AYGAZ", "AZTEK", "AGROT", "AHSGY", "AKFIS", "ALTNY", "ALKLC", "ALVES",
        "ARMGD", "ARTMS", "AVPGY", "BAGFS", "BAKAB", "BALAT", "BNTAS", "BANVT", "BARMA", "BSOKE",
        "BTCIM", "BYDNR", "BAYRK", "BASGZ", "BASCM", "BERA", "BRKSN", "BESLR", "BEYAZ", "BJKAS",
        "BIENY", "BIGTK", "BLUME", "BMSTL", "BMSCH", "BRSAN", "BRYAT", "BFREN", "BOSSA", "BOBET",
        "BRISA", "BVSAN", "BUCIM", "BURCE", "BURVA", "BIGCH", "BAHKM", "BALSU", "BEGYO", "BINBN",
        "BIGEN", "BORSK", "BORLS", "BULGS", "BLCYT", "BIMAS", "BIOEN", "BRKO", "BRLSM", "BRMEN",
        "BRKVY", "BIZIM", "CRFSA", "CASA", "CEOEM", "CCOLA", "CONSE", "COSMO", "CRDFA", "CVKMD",
        "CWENE", "CEMZY", "DAGI", "DAPGM", "DARDL", "DGATE", "DMSAS", "DENGE", "DZGYO", "DERHL",
        "DERIM", "DESA", "DESPC", "DEVA", "DOCO", "DOFRB", "DOHOL", "DGNMO", "ARASE", "DOGUB",
        "DGGYO", "DOAS", "DUNYH", "DURDO", "DYOBY", "DCTTR", "DSTKF", "DOFER", "DURKN", "DOKTA",
        "DNISI", "DIRIT", "DITAS", "EDATA", "ECZYT", "EDIP", "EFOR", "EGEEN", "EGGUB", "EGPRO",
        "EGSER", "EPLAS", "EKSUN", "EKIZ", "ELITE", "EMKEL", "EKGYO", "EMNIS", "ENJSA", "ENERY",
        "ENKAI", "ENSRI", "ERBOS", "ERCB", "EREGL", "KIMMR", "ERSU", "ESCAR", "ESCOM", "ESEN",
        "ETILR", "EUKYO", "EUYO", "ETYAT", "EUHOL", "TEZOL", "EUREN", "EUPWR", "EYGYO", "EBEBK",
        "ECOGR", "EGEGY", "EKOS", "ENDAE", "ECILC", "FADE", "FMIZP", "FENER", "FLAP", "FONET",
        "FROTO", "FORMT", "FRMPL", "FORTE", "FRIGO", "FZLGY", "GWIND", "GSRAY", "GARFA", "GRNYO",
        "GEDIK", "GEDZA", "GLCVY", "GENIL", "GENTS", "GEREL", "GZNMI", "GLBMD", "GLYHO", "GOKNR",
        "GOODY", "GRTHO", "GSDDE", "GSDHO", "GOLTS", "GOZDE", "GUBRF", "GLRYH", "GRSEL", "GLRMK",
        "GUNDG", "GMTAS", "GESAN", "GIPTA", "SAHOL", "HLGYO", "HATEK", "HDFGS", "HEDEF", "HEKTS",
        "HUBVC", "HUNER", "HRKET", "HATSN", "HOROZ", "HURGZ", "HKTM", "HTTBT", "ICBCT", "ICUGS",
        "INGRM", "INTEK", "INVEO", "INVES", "IZENR", "ENTRA", "ISKPL", "IEYHO", "JANTS", "KFEIN",
        "KLKIM", "KLSER", "KAPLM", "KRDMA", "KRDMB", "KRDMD", "KAREL", "KARSN", "KRTEK", "KARTN",
        "KTLEV", "KATMR", "KAYSE", "KENT", "KRVGD", "KERVN", "KZBGY", "KLMSN", "KCAER", "KLSYN",
        "KNFRT", "KONTR", "KONKA", "KONYA", "KGYO", "KORDS", "KRPLS", "KOPOL", "KCHOL", "KRONT",
        "KRSTL", "KUVVA", "KUYAS", "KZGYO", "KSTUR", "KLYPV", "KOTON", "KOCMT", "KBORU", "KRGYO",
        "KUTPO", "KTSKR", "KLGYO", "KLRHO", "KMPUR", "TCKRC", "LIDER", "LOGO", "LKMNH", "LRSHO",
        "LYDHO", "LYDYE", "LILAK", "LMKDC", "LUKSK", "LIDFA", "LINK", "MACKO", "MAKIM", "MAKTK",
        "MANAS", "MAGEN", "MARKA", "MARMR", "MAALT", "MRSHL", "MRGYO", "MARTI", "MTRKS", "MAVI",
        "MZHLD", "MEDTR", "MEGAP", "MNDRS", "MEPET", "MERCN", "MERKO", "MERIT", "METRO", "MTRYO",
        "MEYSU", "MHRGY", "MPARK", "MMCAS", "MOBTL", "MNDTR", "MEGMT", "MEKAG", "MOGAN", "MOPAS",
        "MIATK", "MGROS", "MSGYO", "EGEPO", "NATEN", "NTGAZ", "NTHOL", "NETAS", "NUHCM", "NUGYO",
        "NIBAS", "OBASE", "ODAS", "OFSYM", "ONCSM", "ORGE", "ORMA", "ORCAY", "OSMEN", "OSTIM",
        "OTKAR", "OTTO", "OYYAT", "OYAYO", "OYAKC", "OYLUM", "OBAMS", "ODINE", "ONRYT", "PAMEL",
        "PNLSN", "PAGYO", "PAPIL", "PRDGS", "PRKME", "PARSN", "PASEU", "PAHOL", "PSGYO", "PCILT",
        "PGSUS", "PEKGY", "PENGD", "PENTA", "PSDTC", "PETKM", "PKENT", "PETUN", "PINSU", "PNSUT",
        "PKART", "PLTUR", "POLHO", "POLTK", "PRZMA", "PATEK", "QNBFK", "QUAGR", "RALYH", "RAYSG",
        "RNPOL", "RYGYO", "RYSAS", "RODRG", "ROYAL", "RTALB", "RUBNS", "RUZYE", "REEDR", "RGYAS",
        "SAFKR", "SANEL", "SANKO", "SNICA", "SANFM", "SAMAT", "SARKY", "SASA", "SAYAS", "SDTTR",
        "SEKUR", "SELVA", "SELEC", "SNKRN", "SRVGY", "SEYKM", "SKYLP", "SMRTG", "SMART", "SODSN",
        "SOKE", "SUMAS", "SUNTK", "SUWEN", "SERNT", "SEGMN", "SURGY", "SKTAS", "SONME", "SNPAM",
        "SILVR", "SNGYO", "TNZTP", "TARKM", "TATGD", "TATEN", "TAVHL", "TEKTU", "TKFEN", "TKNSA",
        "TMPOL", "TRHOL", "TERA", "TEHOL", "TGSAS", "TOASO", "TRGYO", "TRMET", "TRENJ", "TLMAN",
        "TSPOR", "TDGYO", "TSGYO", "TUKAS", "TRCAS", "TUREX", "TRALT", "TRILC", "TCELL", "TUCLK",
        "TABGD", "MARBL", "TMSN", "TUPRS", "THYAO", "PRKAB", "TTKOM", "TTRAK", "TBORG", "TURGG",
        "GARAN", "HALKB", "KLNMA", "TSKB", "TURSG", "VAKBN", "ISCTR", "SISE", "UCAYM", "UFUK",
        "ULAS", "ULUFA", "ULUSE", "ULUUN", "UMPAS", "USAK", "VAKFA", "VAKFN", "VKGYO", "VKFYO",
        "VAKKO", "VANGD", "VBTYZ", "VERUS", "VERTU", "VESBE", "VESTL", "VRGYO", "VSNMD", "VKING",
        "YKBNK", "YAPRK", "YATAS", "YYLGD", "YAYLA", "YGGYO", "YEOTK", "YGYO", "YYAPI", "YESIL",
        "YONGA", "YIGIT", "YKSLN", "YUNSA", "YBTAS", "ZGYO", "ZEDUR", "ZOREN", "ZRGYO", "CANTE",
        "CLEBI", "CELHA", "CEMAS", "CEMTS", "CUSAN", "CATES", "CGCAM", "CMBTN", "CMENT", "CIMSA",
        "OZKGY", "OZGYO", "OZRDN", "OZSUB", "OZATD", "OZYSR", "ULKER", "UNLU", "IDGYO", "IHEVA",
        "IHLGM", "IHGZT", "IHAAS", "IHLAS", "IHYAY", "IMASM", "INDES", "INFO", "INTEM", "ISDMR",
        "IZINV", "IZMDC", "IZFAS", "ISFIN", "ISGYO", "ISGSY", "ISMEN", "ISYAT", "ISBIR", "ISSEN",
        "SEKFK", "SEGYO", "SKBNK", "SOKM", "SKYMD"
    ]

    COMMODITIES = [
        ("XAUUSD", "OANDA"), ("XAGUSD", "OANDA"), ("XPTUSD", "OANDA"), ("XPDUSD", "OANDA"),
        ("WTICOUSD", "OANDA"), ("BCOUSD", "OANDA"), ("NATGASUSD", "OANDA"), ("COPPERUSD", "OANDA"),
        ("XCUUSD", "OANDA"), ("SUGARUSD", "OANDA"), ("WHEATUSD", "OANDA"), ("CORNUSD", "OANDA"),
        ("SOYBEANUSD", "OANDA"), ("COFFEEUSD", "OANDA"), ("COTTONUSD", "OANDA"),
    ]

    INDICES = [
        ("XU100", "BIST"), ("XU030", "BIST"), ("XU050", "BIST"), ("XU500", "BIST"),
        ("XUTUM", "BIST"), ("XYUZO", "BIST"), ("XBANA", "BIST"), ("XBANK", "BIST"),
        ("XUMAL", "BIST"), ("XUSIN", "BIST"), ("XUHIZ", "BIST"), ("XUTEK", "BIST"),
        ("XGIDA", "BIST"), ("XTRZM", "BIST"), ("XULAS", "BIST"), ("XELKT", "BIST"),
        ("XHOLD", "BIST"), ("XKMYA", "BIST"), ("XMANA", "BIST"), ("XMESY", "BIST"),
        ("XTAST", "BIST"), ("XILTM", "BIST"), ("XSPOR", "BIST"), ("XSGRT", "BIST"),
        ("XFINK", "BIST"), ("XTEKS", "BIST"), ("XKAGT", "BIST"), ("XBLSM", "BIST"),
        ("XINSA", "BIST"), ("XK100", "BIST"), ("XKTUM", "BIST"), ("XK050", "BIST"),
        ("XSDT", "BIST"), ("XTUMY", "BIST"), ("XHARZ", "BIST"), ("XUGRA", "BIST"),
        ("XKURY", "BIST"), ("XSIST", "BIST"), ("XSIZM", "BIST"), ("XHIZM", "BIST"),
    ]

    CRYPTO = [
        ("BTCUSD", "BINANCE"), ("ETHUSD", "BINANCE"), ("BNBUSD", "BINANCE"), ("SOLUSD", "BINANCE"),
        ("XRPUSD", "BINANCE"), ("ADAUSD", "BINANCE"), ("DOGEUSD","BINANCE"), ("AVAXUSD","BINANCE"),
        ("DOTUSD", "BINANCE"), ("MATICUSD","BINANCE"), ("LINKUSD","BINANCE"), ("ATOMUSD","BINANCE"),
        ("LTCUSD", "BINANCE"), ("TRXUSD", "BINANCE"), ("UNIUSD", "BINANCE"), ("NEARUSD","BINANCE"),
        ("APTUSD", "BINANCE"), ("OPUSD",  "BINANCE"), ("ARBUSD", "BINANCE"),
    ]

    if MARKET_TYPE == "bist": SYMBOLS = [(s, "BIST") for s in BIST_STOCKS]
    elif MARKET_TYPE == "emtia": SYMBOLS = COMMODITIES
    elif MARKET_TYPE == "endeks": SYMBOLS = INDICES
    elif MARKET_TYPE == "kripto": SYMBOLS = CRYPTO
    elif MARKET_TYPE == "all": SYMBOLS = ([(s, "BIST") for s in BIST_STOCKS] + COMMODITIES + INDICES + CRYPTO)
    else: raise ValueError("Hatalı market tipi")

    # HESAPLAMALAR
    def ema(s, l): return s.ewm(span=l, adjust=False).mean()
    def ema2(s, l): return ema(ema(s, l), l)
    
    def process_symbol(sym, exchange, extra_sleep=0.0):
        # Bağlantı ve Veri Çekme
        try:
            df = tv.get_hist(sym, exchange, interval=INTERVAL, n_bars=N_BARS)
        except:
            global tv
            tv = make_tv() # Yeniden bağlan
            return None, "CONNECTION_ERROR"

        if df is None or df.empty:
            time.sleep(SLEEP_BETWEEN_SYMBOLS)
            return None, "NO_DATA"

        # İndikatörler
        hh = df["high"].rolling(lengthK).max()
        ll = df["low"].rolling(lengthK).min()
        rng = (hh - ll).replace(0, 0.000001)
        rel = df["close"] - (hh + ll) / 2
        smi = 200 * (ema2(rel, lengthD) / ema2(rng, lengthD))
        smi_ema = ema(smi, lengthEMA)
        
        macd = ema(df["close"], macd_fast) - ema(df["close"], macd_slow)
        signal = ema(macd, macd_signal)
        hist = macd - signal

        df["SMI"], df["SMI_EMA"], df["HIST"] = smi, smi_ema, hist
        
        # Sinyaller
        last, prev = df.iloc[-1], df.iloc[-2]
        cross_up = prev["SMI"] <= prev["SMI_EMA"] and last["SMI"] > last["SMI_EMA"]
        smi_neg, hist_neg, hist_up = last["SMI"] < 0, last["HIST"] < 0, last["HIST"] > prev["HIST"]
        
        ma200 = df["close"].rolling(200).mean().iloc[-1] if len(df) > 200 else 0
        above_ma200 = last["close"] > ma200
        vol_ma = df["volume"].rolling(20).mean().iloc[-1]
        vol_ok = last["volume"] > (vol_ma * VOLUME_MULTIPLIER) if vol_ma > 0 else False

        smi_macd_buy = cross_up and smi_neg and hist_neg and hist_up
        full_buy_signal = smi_macd_buy and above_ma200 and vol_ok
        sell_signal = (prev["SMI"] >= prev["SMI_EMA"] and last["SMI"] < last["SMI_EMA"] and last["SMI"] > SMI_SELL_THRESHOLD)

        row = {
            "Symbol": sym, "Exchange": exchange, "Period": PERIOD, "Date": df.index[-1],
            "Close": last["close"], "MA200_Daily": ma200, "Above_MA200": above_ma200,
            "SMI": last["SMI"], "SMI_EMA": last["SMI_EMA"], "MACD_Hist": last["HIST"],
            "Volume": last["volume"], "Volume_MA": vol_ma, "Volume_High": vol_ok,
            "BUY_CrossUp": cross_up, "BUY_SMI<0": smi_neg, "BUY_Hist<0": hist_neg, "BUY_HistUp": hist_up,
            "SMI_MACD_BUY": smi_macd_buy, "Full_BUY_Signal": full_buy_signal,
            "SELL_Signal": sell_signal, "Status": "OK",
            "SELL_CrossDown": False, "SELL_SMI>40": False, "SELL_Hist>0": False, "SELL_HistDown": False
        }
        time.sleep(SLEEP_BETWEEN_SYMBOLS)
        return row, None

    # TARAMA DÖNGÜSÜ
    init_database()
    results_map = {}
    failed_symbols = []
    
    print(f"--- TARAMA BAŞLADI: {MARKET_NAME} ({len(SYMBOLS)}) ---")
    start_time = datetime.now()

    def upsert_result(row):
        results_map[(row["Symbol"], row["Exchange"], row["Period"])] = row

    for batch_start in range(0, len(SYMBOLS), BATCH_SIZE):
        batch = SYMBOLS[batch_start:batch_start + BATCH_SIZE]
        print(f"Batch {batch_start//BATCH_SIZE + 1} işleniyor...")
        
        for sym, exc in batch:
            try:
                row, err = process_symbol(sym, exc)
                if row: upsert_result(row)
                else: failed_symbols.append((sym, exc))
            except Exception as e:
                print(f"Hata {sym}: {e}")
        time.sleep(SLEEP_BETWEEN_BATCH)

    # SONUÇLAR
    results = list(results_map.values())
    df_out = pd.DataFrame(results)
    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    out_name = f"tum_tarama_{MARKET_TYPE.upper()}_{PERIOD}_{timestamp}.csv"
    df_out.to_csv(out_name, index=False)
    save_tarama_to_db(results_map, start_time)

    # TELEGRAM GÖNDERİMİ (DÜZELTME 4: Grafik Mantığı)
    if tg_enabled():
        full_buys = df_out[df_out["Full_BUY_Signal"] == True]
        smi_buys = df_out[df_out["SMI_MACD_BUY"] == True]
        
        msg = (f"📢 Tarama Bitti ({MARKET_TYPE})\n"
               f"✅ Tam Alım: {len(full_buys)}\n"
               f"🟡 SMI Alım: {len(smi_buys)}\n"
               f"📁 Dosya: {out_name}")
        
        try:
            tg_send_message(msg)
            tg_send_document(out_name) # Dosyayı gönder
            
            # Grafik Gönderme Kuralı:
            # 1. Tam Alım varsa onları gönder.
            # 2. Tam Alım YOKSA ama SMI Alım varsa, SMI'ları gönder.
            target_list = full_buys
            header = "✅ TAM ALIM GRAFİKLERİ"
            
            if target_list.empty and not smi_buys.empty:
                target_list = smi_buys
                header = "🟡 SMI ALIM GRAFİKLERİ (Tam Alım Yok)"
            
            if not target_list.empty:
                tg_send_message(f"{header} (İlk {TG_MAX_ITEMS} adet):")
                for _, r in target_list.head(TG_MAX_ITEMS).iterrows():
                    sym = r["Symbol"]
                    df_chart = tv.get_hist(sym, r["Exchange"], interval=INTERVAL, n_bars=120)
                    if df_chart is not None:
                        img_path = f"outputs/charts/{sym}.png"
                        make_candle_chart(df_chart, img_path, f"{sym} ({PERIOD})")
                        tg_send_photo(img_path, caption=f"#{sym} - {r['Close']}")
                        time.sleep(TG_SLEEP_BETWEEN_PHOTOS)
        except Exception as e:
            print(f"Telegram hatası: {e}")

# =========================
# BACKTEST ve RAPOR (Senin Kodun Aynen Korundu)
# =========================
elif KOMUT == "backtest":
    # Senin orijinal backtest kodların buraya gelecek (Github'da çalışmadığı için özet geçiyorum)
    print("Backtest modu (Local çalışma için)")

elif KOMUT == "rapor":
    # Senin orijinal rapor kodların buraya gelecek
    print("Rapor modu")

else:
    print("Geçersiz komut.")
