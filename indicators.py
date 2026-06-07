"""
Taramabot İndikatör Modülü
SMI/MACD, RSI ve SMA tabanlı indikatörleri hesaplar.
"""

import pandas as pd
import numpy as np
from config import (
    SMI_PERIOD, SMI_EMA_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    MA200_PERIOD, VOLUME_MULTIPLIER, RSI_PERIOD, RSI_THRESHOLD, RSI_CROSSOVER,
    SMA_5, SMA_8, SMA_21, SMA_50, SMA_55, SMA_200, MACD_LEVEL_THRESHOLD, VOLUME_RATIO_THRESHOLD,
    NEW_RSI_PERIOD, NEW_RSI_UP_THRESHOLD, NEW_RSI_MAX_THRESHOLD, NEW_VOLUME_RATIO,
    EMA_5, EMA_8, EMA_13, EMA_21, EMA_55, EMA_200
)


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential Moving Average hesapla."""
    return series.ewm(span=length, adjust=False).mean()


def ema2(series: pd.Series, length: int) -> pd.Series:
    """Çift Exponential Moving Average hesapla."""
    return ema(ema(series, length), length)


def sma(series: pd.Series, length: int) -> pd.Series:
    """Simple Moving Average hesapla."""
    return series.rolling(window=length).mean()


def calc_smi(df: pd.DataFrame) -> tuple:
    """
    Stochastic Momentum Index (SMI) hesapla (Pine Script v6 uyumlu).
    
    Args:
        df: OHLCV verileri içeren DataFrame
        
    Returns:
        (smi, smi_ema) tuple
    """
    # Pine Script parametreleri: lengthK=10, lengthD=3, lengthEMA=3
    lengthK = 10
    lengthD = 3
    lengthEMA = 3
    
    hh = df["high"].rolling(lengthK).max()
    ll = df["low"].rolling(lengthK).min()
    
    highest_lowest_range = hh - ll
    relative_range = df["close"] - (hh + ll) / 2
    
    # Pine Script: emaEma(source, length) => ta.ema(ta.ema(source, length), length)
    # 200 * (emaEma(relativeRange, lengthD) / emaEma(highestLowestRange, lengthD))
    
    num = ema2(relative_range, lengthD)
    den = ema2(highest_lowest_range, lengthD)
    
    smi = 200 * (num / den.replace(0, 0.000001))
    smi_ema = ema(smi, lengthEMA)
    
    return smi, smi_ema


def calc_macd(df: pd.DataFrame) -> tuple:
    """
    MACD (Moving Average Convergence Divergence) hesapla.
    
    Args:
        df: OHLCV verileri içeren DataFrame
        
    Returns:
        (macd, signal, hist) tuple
    """
    macd = ema(df["close"], MACD_FAST) - ema(df["close"], MACD_SLOW)
    signal = ema(macd, MACD_SIGNAL)
    hist = macd - signal
    return macd, signal, hist


def calc_ma200(df: pd.DataFrame) -> pd.Series:
    """200 günlük hareketli ortalama hesapla."""
    return df["close"].rolling(MA200_PERIOD).mean()


def calc_rsi(series: pd.Series, length: int = RSI_PERIOD) -> pd.Series:
    """
    Relative Strength Index (RSI) hesapla.
    
    Args:
        series: Fiyat serisi
        length: RSI periyodu
        
    Returns:
        RSI değerleri
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def check_smi_macd_signal(df: pd.DataFrame) -> dict:
    """
    SMI/MACD tabanlı alım sinyalini kontrol et.
    
    Args:
        df: OHLCV verileri içeren DataFrame
        
    Returns:
        {
            'full_buy_signal': bool,
            'smi_macd_buy': bool,
            'details': dict
        }
    """
    if df is None or df.empty or len(df) < 200:
        return {'full_buy_signal': False, 'smi_macd_buy': False, 'details': {}}
    
    # İndikatörleri hesapla
    smi, smi_ema = calc_smi(df)
    macd, signal, hist = calc_macd(df)
    ma200 = calc_ma200(df)
    
    # Son ve önceki barları al
    last = df.iloc[-1]
    prev = df.iloc[-2]
    last_smi = smi.iloc[-1]
    prev_smi = smi.iloc[-2]
    last_smi_ema = smi_ema.iloc[-1]
    prev_smi_ema = smi_ema.iloc[-2]
    last_hist = hist.iloc[-1]
    prev_hist = hist.iloc[-2]
    
    # SMI/MACD Şartları
    # Sadece o anki kesişim değil, SMI'nın EMA üzerinde olduğu durumu da kapsayalım (esneklik için)
    cross_up = (prev_smi <= prev_smi_ema and last_smi > last_smi_ema) or (last_smi > last_smi_ema and last_smi > prev_smi)
    smi_neg = last_smi < 0
    hist_neg = last_hist < 0
    hist_up = last_hist > prev_hist
    
    smi_macd_buy = cross_up and smi_neg and hist_neg and hist_up
    
    # Ek Şartlar (Tam Alım Sinyali)
    ma200_val = ma200.iloc[-1] if len(df) > 200 else 0
    above_ma200 = last["close"] > ma200_val
    
    vol_ma = df["volume"].rolling(20).mean().iloc[-1]
    vol_ok = last["volume"] > (vol_ma * VOLUME_MULTIPLIER) if vol_ma > 0 else False
    
    full_buy_signal = smi_macd_buy and above_ma200 and vol_ok
    
    return {
        'full_buy_signal': full_buy_signal,
        'smi_macd_buy': smi_macd_buy,
        'details': {
            'smi': last_smi,
            'smi_ema': last_smi_ema,
            'macd': macd.iloc[-1],
            'signal': signal.iloc[-1],
            'hist': last_hist,
            'ma200': ma200_val,
            'above_ma200': above_ma200,
            'volume_ok': vol_ok,
            'cross_up': cross_up,
            'smi_neg': smi_neg,
            'hist_neg': hist_neg,
            'hist_up': hist_up
        }
    }


def check_rsi_signal(df: pd.DataFrame) -> dict:
    """
    RSI tabanlı alım sinyalini kontrol et.
    """
    if df is None or df.empty or len(df) < 30:
        return {'signal': False, 'details': {}}
    
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)
    
    # RSI hesapla
    rsi_values = calc_rsi(close, RSI_PERIOD)
    
    # Son ve önceki RSI değerleri
    rsi_last = rsi_values.iloc[-1]
    rsi_prev = rsi_values.iloc[-2]
    
    # Şart 1: RSI > 60
    cond1 = rsi_last > RSI_THRESHOLD
    
    # Şart 2: RSI 50'yi yukarı kesmiş veya 50 üzerinde yükseliyor
    cond2 = (rsi_prev <= RSI_CROSSOVER and rsi_last > RSI_CROSSOVER) or (rsi_last > RSI_CROSSOVER and rsi_last > rsi_prev)
    
    # Şart 3: Hacim kontrol
    vol_last = vol.iloc[-1]
    vol_avg = vol.tail(10).mean()
    cond3 = vol_last > (vol_avg * VOLUME_MULTIPLIER)
    
    signal = cond1 and cond2 and cond3
    
    return {
        'signal': signal,
        'details': {
            'rsi': rsi_last,
            'rsi_prev': rsi_prev,
            'rsi_above_60': cond1,
            'rsi_crossed_50': cond2,
            'volume_ok': cond3,
            'volume_ratio': vol_last / vol_avg if vol_avg > 0 else 0
        }
    }


def check_new_scan_signal(df: pd.DataFrame) -> dict:
    """
    Görseldeki kriterlere göre yeni tarama (Scanner 3) kontrolü.
    """
    if df is None or df.empty or len(df) < 200:
        return {'signal': False, 'details': {}}
    
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)
    
    # SMA'ları hesapla
    sma5 = sma(close, SMA_5)
    sma8 = sma(close, SMA_8)
    sma21 = sma(close, SMA_21)
    sma50 = sma(close, SMA_50)
    sma55 = sma(close, SMA_55)
    sma200 = sma(close, SMA_200)
    
    # MACD hesapla
    macd_line, signal_line, _ = calc_macd(df)
    
    # Son değerler
    last_close = close.iloc[-1]
    last_vol = vol.iloc[-1]
    avg_vol = vol.tail(20).mean()
    
    last_macd = macd_line.iloc[-1]
    prev_macd = macd_line.iloc[-2]
    last_signal = signal_line.iloc[-1]
    prev_signal = signal_line.iloc[-2]
    
    # Şartlar
    cond_vol = last_vol > (avg_vol * VOLUME_RATIO_THRESHOLD)
    cond_sma5 = last_close > sma5.iloc[-1]
    cond_sma8 = last_close > sma8.iloc[-1]
    cond_sma21 = last_close > sma21.iloc[-1]
    cond_sma50 = last_close > sma50.iloc[-1]
    cond_sma55 = last_close > sma55.iloc[-1]
    cond_sma200 = last_close > sma200.iloc[-1]
    
    cond_macd_level = last_macd > MACD_LEVEL_THRESHOLD
    # MACD kesişimi veya MACD'nin sinyal üzerinde yükselmesi
    cond_macd_cross = (prev_macd <= prev_signal and last_macd > last_signal) or (last_macd > last_signal and last_macd > prev_macd)
    
    signal = (cond_vol and cond_sma5 and cond_sma8 and cond_sma21 and 
              cond_sma50 and cond_sma55 and cond_sma200 and 
              cond_macd_level and cond_macd_cross)
    
    return {
        'signal': signal,
        'details': {
            'volume_ratio': last_vol / avg_vol if avg_vol > 0 else 0,
            'sma5': sma5.iloc[-1],
            'sma8': sma8.iloc[-1],
            'sma21': sma21.iloc[-1],
            'sma50': sma50.iloc[-1],
            'sma55': sma55.iloc[-1],
            'sma200': sma200.iloc[-1],
            'macd': last_macd,
            'signal_line': last_signal,
            'macd_above_0': cond_macd_level,
            'macd_cross_up': cond_macd_cross
        }
    }


def check_rsi_macd_scan_signal(df: pd.DataFrame) -> dict:
    """
    Görseldeki kriterlere göre RSI + MACD + Hacim taraması.
    """
    if df is None or df.empty or len(df) < 35:
        return {'signal': False, 'details': {}}
    
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)
    
    # İndikatörleri hesapla
    rsi_values = calc_rsi(close, NEW_RSI_PERIOD)
    macd_line, signal_line, _ = calc_macd(df)
    
    # Son ve önceki değerler
    rsi_last = rsi_values.iloc[-1]
    rsi_prev = rsi_values.iloc[-2]
    
    macd_last = macd_line.iloc[-1]
    macd_prev = macd_line.iloc[-2]
    sig_last = signal_line.iloc[-1]
    sig_prev = signal_line.iloc[-2]
    
    vol_last = vol.iloc[-1]
    vol_avg = vol.tail(20).mean()
    vol_ratio = vol_last / vol_avg if vol_avg > 0 else 0
    
    # Şartlar
    # RSI(14) 50'yi yukarı kesmiş veya 50 üzerinde yükseliyor
    cond_rsi_up = (rsi_prev <= NEW_RSI_UP_THRESHOLD and rsi_last > NEW_RSI_UP_THRESHOLD) or (rsi_last > NEW_RSI_UP_THRESHOLD and rsi_last > rsi_prev)
    # RSI(14) < 70
    cond_rsi_max = rsi_last < NEW_RSI_MAX_THRESHOLD
    # MACD Level yukarı keser Signal veya üzerinde yükseliyor
    cond_macd_cross = (macd_prev <= sig_prev and macd_last > sig_last) or (macd_last > sig_last and macd_last > macd_prev)
    # Bağ Hacim > 1.5
    cond_vol = vol_ratio > NEW_VOLUME_RATIO
    
    signal = cond_rsi_up and cond_rsi_max and cond_macd_cross and cond_vol
    
    return {
        'signal': signal,
        'details': {
            'rsi': rsi_last,
            'rsi_prev': rsi_prev,
            'macd': macd_last,
            'signal_line': sig_last,
            'volume_ratio': vol_ratio,
            'rsi_up_50': cond_rsi_up,
            'rsi_below_70': cond_rsi_max,
            'macd_cross': cond_macd_cross,
            'volume_ok': cond_vol
        }
    }


def check_ema_scan_signal(df: pd.DataFrame) -> dict:
    """
    Görseldeki kriterlere göre EMA Dizilimi ve Bağ Hacim taraması.
    """
    if df is None or df.empty or len(df) < 200:
        return {'signal': False, 'details': {}}
    
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)
    
    # EMA'ları hesapla
    ema5 = ema(close, EMA_5)
    ema8 = ema(close, EMA_8)
    ema13 = ema(close, EMA_13)
    ema21 = ema(close, EMA_21)
    ema55 = ema(close, EMA_55)
    ema200 = ema(close, EMA_200)
    
    # Son ve önceki değerler
    last_close = close.iloc[-1]
    
    e5_last, e5_prev = ema5.iloc[-1], ema5.iloc[-2]
    e8_last, e8_prev = ema8.iloc[-1], ema8.iloc[-2]
    e13_last, e13_prev = ema13.iloc[-1], ema13.iloc[-2]
    e21_last = ema21.iloc[-1]
    e55_last = ema55.iloc[-1]
    e200_last = ema200.iloc[-1]
    
    vol_last = vol.iloc[-1]
    vol_avg = vol.tail(20).mean()
    vol_ratio = vol_last / vol_avg if vol_avg > 0 else 0
    
    # Şartlar
    cond1 = e200_last < last_close
    cond2 = e55_last < last_close
    cond3 = e21_last < last_close
    # Kesişim veya üzerinde olma durumu
    cond4 = (e8_prev <= e13_prev and e8_last > e13_last) or (e8_last > e13_last and e8_last > e8_prev)
    cond5 = (e5_prev <= e8_prev and e5_last > e8_last) or (e5_last > e8_last and e5_last > e5_prev)
    cond6 = (e5_prev <= e13_prev and e5_last > e13_last) or (e5_last > e13_last and e5_last > e5_prev)
    cond7 = vol_ratio > 1.5
    
    signal = cond1 and cond2 and cond3 and cond4 and cond5 and cond6 and cond7
    
    return {
        'signal': signal,
        'details': {
            'ema5': e5_last,
            'ema8': e8_last,
            'ema13': e13_last,
            'ema21': e21_last,
            'ema55': e55_last,
            'ema200': e200_last,
            'volume_ratio': vol_ratio,
            'conditions': [cond1, cond2, cond3, cond4, cond5, cond6, cond7]
        }
    }


def check_macd_positive_cross_signal(df: pd.DataFrame) -> dict:
    """
    Görseldeki kriterlere göre MACD Pozitif Kesişim taraması.
    """
    if df is None or df.empty or len(df) < 35:
        return {'signal': False, 'details': {}}
    
    close = df["close"].astype(float)
    
    # İndikatörleri hesapla
    rsi_values = calc_rsi(close, 14)
    macd_line, signal_line, _ = calc_macd(df)
    
    # Son ve önceki değerler
    rsi_last = rsi_values.iloc[-1]
    macd_last = macd_line.iloc[-1]
    macd_prev = macd_line.iloc[-2]
    sig_last = signal_line.iloc[-1]
    sig_prev = signal_line.iloc[-2]
    
    # Şartlar
    cond_rsi = rsi_last > 30
    cond_macd_cross = (macd_prev <= sig_prev and macd_last > sig_last) or (macd_last > sig_last and macd_last > macd_prev)
    cond_macd_above_0 = macd_last > 0
    
    signal = cond_rsi and cond_macd_cross and cond_macd_above_0
    
    return {
        'signal': signal,
        'details': {
            'rsi': rsi_last,
            'macd': macd_last,
            'signal_line': sig_last,
            'rsi_above_30': cond_rsi,
            'macd_cross': cond_macd_cross,
            'macd_above_0': cond_macd_above_0
        }
    }


def check_h8_smi_macd_positive_signal(df: pd.DataFrame) -> dict:
    """
    H8 Stratejisi: SMI/MACD Alım Sinyali (Pozitif Bölge)
    """
    if df is None or df.empty or len(df) < 200:
        return {'signal': False, 'details': {}}
    
    smi, smi_ema = calc_smi(df)
    macd, signal, hist = calc_macd(df)
    
    last_smi = smi.iloc[-1]
    prev_smi = smi.iloc[-2]
    last_smi_ema = smi_ema.iloc[-1]
    prev_smi_ema = smi_ema.iloc[-2]
    last_hist = hist.iloc[-1]
    prev_hist = hist.iloc[-2]
    
    # H8 Şartları
    cross_up = (prev_smi <= prev_smi_ema and last_smi > last_smi_ema) or (last_smi > last_smi_ema and last_smi > prev_smi)
    smi_pos = last_smi > 0
    hist_pos = last_hist > 0
    hist_up = last_hist > prev_hist
    
    signal = cross_up and smi_pos and hist_pos and hist_up
    
    return {
        'signal': signal,
        'details': {
            'smi': last_smi,
            'smi_ema': last_smi_ema,
            'hist': last_hist,
            'cross_up': cross_up,
            'smi_positive': smi_pos,
            'hist_positive': hist_pos,
            'hist_up': hist_up
        }
    }


def check_i9_smi_macd_positive_full_signal(df: pd.DataFrame) -> dict:
    """
    I9 Stratejisi: SMI/MACD Tam Alım (Güçlü) - Pozitif Bölge
    """
    if df is None or df.empty or len(df) < 200:
        return {'full_signal': False, 'h8_signal': False, 'details': {}}
    
    h8_result = check_h8_smi_macd_positive_signal(df)
    h8_signal = h8_result['signal']
    
    ma200 = calc_ma200(df)
    last = df.iloc[-1]
    ma200_val = ma200.iloc[-1]
    above_ma200 = last["close"] > ma200_val
    
    vol_ma = df["volume"].rolling(20).mean().iloc[-1]
    vol_ok = last["volume"] > (vol_ma * VOLUME_MULTIPLIER) if vol_ma > 0 else False
    
    full_signal = h8_signal and above_ma200 and vol_ok
    
    return {
        'full_signal': full_signal,
        'h8_signal': h8_signal,
        'details': {
            'above_ma200': above_ma200,
            'volume_ok': vol_ok,
            'h8_details': h8_result['details']
        }
    }
