"""
Taramabot İndikatör Modülü
SMI/MACD ve RSI indikatörlerini hesaplar.
"""

import pandas as pd
import numpy as np
from config import (
    SMI_PERIOD, SMI_EMA_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    MA200_PERIOD, VOLUME_MULTIPLIER, RSI_PERIOD, RSI_THRESHOLD, RSI_CROSSOVER
)


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential Moving Average hesapla."""
    return series.ewm(span=length, adjust=False).mean()


def ema2(series: pd.Series, length: int) -> pd.Series:
    """Çift Exponential Moving Average hesapla."""
    return ema(ema(series, length), length)


def calc_smi(df: pd.DataFrame) -> tuple:
    """
    Stochastic Momentum Index (SMI) hesapla.
    
    Args:
        df: OHLCV verileri içeren DataFrame
        
    Returns:
        (smi, smi_ema) tuple
    """
    hh = df["high"].rolling(SMI_PERIOD).max()
    ll = df["low"].rolling(SMI_PERIOD).min()
    rng = (hh - ll).replace(0, 0.000001)
    rel = df["close"] - (hh + ll) / 2
    smi = 200 * (ema2(rel, SMI_EMA_PERIOD) / ema2(rng, SMI_EMA_PERIOD))
    smi_ema = ema(smi, SMI_EMA_PERIOD)
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
    prev_smi = smi.iloc[-1]
    last_smi_ema = smi_ema.iloc[-1]
    prev_smi_ema = smi_ema.iloc[-2]
    last_hist = hist.iloc[-1]
    prev_hist = hist.iloc[-2]
    
    # SMI/MACD Şartları
    cross_up = prev_smi <= prev_smi_ema and last_smi > last_smi_ema
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
    
    Şartlar:
    1. RSI(7) > 60
    2. RSI 50'yi yukarı kesmiş
    3. Hacim, ortalamanın 1.5 katı
    
    Args:
        df: OHLCV verileri içeren DataFrame
        
    Returns:
        {
            'signal': bool,
            'details': dict
        }
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
    
    # Şart 2: RSI 50'yi yukarı kesmiş
    cond2 = (rsi_prev <= RSI_CROSSOVER) and (rsi_last > RSI_CROSSOVER)
    
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
