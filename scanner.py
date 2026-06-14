"""
Taramabot Tarama Motoru Modülü
Asenkron olarak sembolleri tarar ve alım sinyallerini tespit eder.
SMI/MACD, RSI ve SMA tabanlı üç farklı strateji destekler.
"""

import asyncio
import logging
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from tvDatafeed import TvDatafeed, Interval

from config import (
    TV_USERNAME, TV_PASSWORD, BIST_STOCKS, NASDAQ_100, SP_500, COMMODITIES, CRYPTO,
    BARS_TO_FETCH, STATE_FILE
)
from indicators import (
    check_smi_macd_signal, check_rsi_signal, check_new_scan_signal, 
    check_rsi_macd_scan_signal, check_ema_scan_signal, check_macd_positive_cross_signal, check_h8_smi_macd_positive_signal, check_i9_smi_macd_positive_full_signal
)

logger = logging.getLogger(__name__)


class ScannerState:
    """Tarama durumunu yönetir (son gönderilen sinyalleri takip eder)."""
    
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()
    
    def _load_state(self) -> dict:
        """Durumu dosyadan yükle."""
        if not os.path.exists(self.state_file):
            logger.info(f"Durum dosyası bulunamadı: {self.state_file}. Yeni durum oluşturuluyor.")
            return {"last_sent_smi_macd": {}, "last_sent_rsi": {}, "last_sent_new_scan": {}, "last_sent_rsi_macd": {}, "last_sent_ema": {}, "last_sent_macd_cross": {}, "last_sent_h8": {}, "last_sent_i9": {}}
        
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
                # Eksik anahtarları tamamla
                for key in ["last_sent_smi_macd", "last_sent_rsi", "last_sent_new_scan", "last_sent_rsi_macd", "last_sent_ema", "last_sent_macd_cross", "last_sent_h8", "last_sent_i9"]:
                    if key not in state:
                        state[key] = {}
                return state
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.error(f"Durum dosyası yükleme hatası: {e}. Yeni durum oluşturuluyor.")
            return {"last_sent_smi_macd": {}, "last_sent_rsi": {}, "last_sent_new_scan": {}, "last_sent_rsi_macd": {}, "last_sent_ema": {}, "last_sent_macd_cross": {}, "last_sent_h8": {}, "last_sent_i9": {}}
    
    def save(self) -> None:
        """Durumu dosyaya kaydet."""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
            logger.info(f"Durum kaydedildi: {self.state_file}")
        except Exception as e:
            logger.error(f"Durum kaydetme hatası: {e}")
    
    def is_signal_sent(self, symbol: str, period: str, strategy: str, bar_time: str) -> bool:
        """Sinyal bu bar için daha önce gönderilmiş mi kontrol et."""
        key = f"{symbol}_{period}"
        strategy_map = {
            "smi_macd": "last_sent_smi_macd",
            "rsi": "last_sent_rsi",
            "new_scan": "last_sent_new_scan",
            "rsi_macd": "last_sent_rsi_macd",
            "ema": "last_sent_ema",
            "macd_cross": "last_sent_macd_cross", "h8": "last_sent_h8", "i9": "last_sent_i9"
        }
        
        state_key = strategy_map.get(strategy)
        if state_key:
            entry = self.state.get(state_key, {}).get(key)
            if isinstance(entry, dict):
                return entry.get("time") == bar_time
            return entry == bar_time
        return False
    
    def mark_signal_sent(self, symbol: str, period: str, strategy: str, bar_time: str, price: float = 0.0, **metadata) -> None:
        """Sinyali bu bar için gönderildi olarak işaretle ve fiyatı kaydet."""
        key = f"{symbol}_{period}"
        strategy_map = {
            "smi_macd": "last_sent_smi_macd",
            "rsi": "last_sent_rsi",
            "new_scan": "last_sent_new_scan",
            "rsi_macd": "last_sent_rsi_macd",
            "ema": "last_sent_ema",
            "macd_cross": "last_sent_macd_cross", "h8": "last_sent_h8", "i9": "last_sent_i9"
        }
        
        state_key = strategy_map.get(strategy)
        if state_key:
            entry = {
                "time": bar_time,
                "price": price
            }
            entry.update(metadata)
            self.state.setdefault(state_key, {})[key] = entry


class MarketScanner:
    """Pazar tarayıcı."""
    
    def __init__(self):
        self.tv = self._create_tv_connection()
        self.state = ScannerState()
    
    def _create_tv_connection(self) -> TvDatafeed:
        """TradingView bağlantısı oluştur."""
        if TV_USERNAME and TV_PASSWORD:
            try:
                logger.info(f"TradingView'a giriş yapılıyor: {TV_USERNAME}")
                # Hata durumunda programın çökmesini engellemek için try-except
                tv = TvDatafeed(username=TV_USERNAME, password=TV_PASSWORD)
                return tv
            except Exception as e:
                logger.warning(f"tvDatafeed giriş hatası: {e}. Anonim (nologin) modda devam ediliyor.")
                return TvDatafeed()
        
        logger.info("TradingView'a anonim (nologin) olarak bağlanılıyor.")
        return TvDatafeed()
    
    async def scan_symbol(
        self,
        symbol: str,
        exchange: str,
        interval: Interval,
        period_str: str,
        strategies: List[str] = None
    ) -> Dict[str, any]:
        """
        Bir sembolü tara.
        """
        if strategies is None:
            strategies = ["smi_macd", "rsi", "new_scan", "rsi_macd", "ema", "macd_cross", "h8", "i9"]
        
        try:
            # Veri çek
            df = self.tv.get_hist(symbol, exchange, interval=interval, n_bars=BARS_TO_FETCH)
            
            if df is None or df.empty or len(df) < 2:
                return None
            
            # Endeks kapalıyken (BIST için) son verinin güncelliğini kontrol et
            if exchange == "BIST":
                last_bar_time = df.index[-1]
                now = datetime.now(last_bar_time.tzinfo)
                # Hafta sonu boşluğunu kapsayacak şekilde 90 saate esnetildi.
                if interval in [Interval.in_15_minute, Interval.in_1_hour, Interval.in_4_hour]:
                    if (now - last_bar_time).total_seconds() > 324000: # 90 saat
                        return None
            
            # Son bar bilgisi
            last_bar = df.iloc[-1]
            prev_bar = df.iloc[-2] if len(df) > 1 else last_bar
            
            # Fiyat değişimi
            change_percent = ((last_bar['close'] - prev_bar['close']) / prev_bar['close']) * 100
            
            result = {
                "symbol": symbol,
                "exchange": exchange,
                "period": period_str,
                "close": last_bar['close'],
                "change": change_percent,
                "bar_time": df.index[-1].isoformat(),
                "signals": {}
            }
            
            # Stratejileri kontrol et
            if "smi_macd" in strategies:
                result["signals"]["smi_macd"] = check_smi_macd_signal(df)
            if "rsi" in strategies:
                result["signals"]["rsi"] = check_rsi_signal(df)
            if "new_scan" in strategies:
                result["signals"]["new_scan"] = check_new_scan_signal(df)
            if "rsi_macd" in strategies:
                result["signals"]["rsi_macd"] = check_rsi_macd_scan_signal(df)
            if "ema" in strategies:
                result["signals"]["ema"] = check_ema_scan_signal(df)
            if "macd_cross" in strategies:
                result["signals"]["macd_cross"] = check_macd_positive_cross_signal(df)
            if "h8" in strategies:
                result["signals"]["h8"] = check_h8_smi_macd_positive_signal(df)
            if "i9" in strategies:
                result["signals"]["i9"] = check_i9_smi_macd_positive_full_signal(df)
            
            return result
            
        except Exception as e:
            logger.error(f"Sembol tarama hatası ({symbol}): {str(e)}")
            return None
    
    async def scan_market(
        self,
        market_type: str = "bist",
        period: str = "1D",
        strategies: List[str] = None,
        use_state: bool = True
    ) -> Tuple[List[dict], List[dict], List[dict], List[dict], List[dict], List[dict], List[dict], List[dict], List[dict], int]:
        """
        Pazarı tara.
        """
        if strategies is None:
            strategies = ["smi_macd", "rsi", "new_scan", "rsi_macd", "ema", "macd_cross", "h8", "i9"]
        
        # Sembol listesini seç
        if market_type.lower() == "bist":
            symbols = [(s, "BIST") for s in BIST_STOCKS]
        elif market_type.lower() == "nasdaq":
            symbols = [(s, "NASDAQ") for s in NASDAQ_100]
        elif market_type.lower() == "sp500":
            symbols = [(s, "NYSE") for s in SP_500]
        elif market_type.lower() == "emtia":
            symbols = COMMODITIES
        elif market_type.lower() == "kripto":
            symbols = CRYPTO
        else:
            logger.error(f"Bilinmeyen pazar türü: {market_type}")
            return [], [], [], [], [], [], [], [], [], 0
        
        # Zaman dilimini TvDatafeed formatına çevir
        interval_map = {
            "15m": Interval.in_15_minute, "15M": Interval.in_15_minute,
            "1h": Interval.in_1_hour, "1H": Interval.in_1_hour,
            "4h": Interval.in_4_hour, "4H": Interval.in_4_hour,
            "1d": Interval.in_daily, "1D": Interval.in_daily,
            "1w": Interval.in_weekly, "1W": Interval.in_weekly,
            "1m": Interval.in_monthly, "1M": Interval.in_monthly
        }
        interval = interval_map.get(period, Interval.in_daily)
        
        logger.info(f"{market_type.upper()} pazarı taranıyor ({period})...")
        
        # Paralel tarama için sembolleri parçalara böl (TV rate limit koruması)
        chunk_size = 50
        all_results = []
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i:i+chunk_size]
            tasks = [self.scan_symbol(sym, exc, interval, period, strategies) for sym, exc in chunk]
            chunk_results = await asyncio.gather(*tasks)
            all_results.extend([r for r in chunk_results if r is not None])
            if i + chunk_size < len(symbols):
                await asyncio.sleep(1) # Chunklar arası kısa bekleme
        
        results = all_results
        total_scanned = len(results)
        
        # Sinyalleri kategorize et
        full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals, ema_signals, macd_cross_signals, h8_signals, i9_signals = [], [], [], [], [], [], [], [], []
        
        for result in results:
            sym, p, bar_time, close = result["symbol"], period, result["bar_time"], result["close"]
            
            # SMI/MACD
            if "smi_macd" in result["signals"]:
                smi_res = result["signals"]["smi_macd"]
                if smi_res["full_buy_signal"] and (not use_state or not self.state.is_signal_sent(sym, p, "smi_macd", bar_time)):
                    full_signals.append(result)
                    if use_state: self.state.mark_signal_sent(sym, p, "smi_macd", bar_time, close, is_full=True)
                elif smi_res["smi_macd_buy"] and (not use_state or not self.state.is_signal_sent(sym, p, "smi_macd", bar_time)):
                    smi_signals.append(result)
                    if use_state: self.state.mark_signal_sent(sym, p, "smi_macd", bar_time, close, is_full=False)
            
            # RSI
            if "rsi" in result["signals"] and result["signals"]["rsi"]["signal"] and (not use_state or not self.state.is_signal_sent(sym, p, "rsi", bar_time)):
                rsi_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "rsi", bar_time, close)
            
            # New Scan
            if "new_scan" in result["signals"] and result["signals"]["new_scan"]["signal"] and (not use_state or not self.state.is_signal_sent(sym, p, "new_scan", bar_time)):
                new_scan_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "new_scan", bar_time, close)
            
            # RSI MACD
            if "rsi_macd" in result["signals"] and result["signals"]["rsi_macd"]["signal"] and (not use_state or not self.state.is_signal_sent(sym, p, "rsi_macd", bar_time)):
                rsi_macd_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "rsi_macd", bar_time, close)
            
            # EMA
            if "ema" in result["signals"] and result["signals"]["ema"]["signal"] and (not use_state or not self.state.is_signal_sent(sym, p, "ema", bar_time)):
                ema_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "ema", bar_time, close)
            
            # MACD Cross
            if "macd_cross" in result["signals"] and result["signals"]["macd_cross"]["signal"] and (not use_state or not self.state.is_signal_sent(sym, p, "macd_cross", bar_time)):
                macd_cross_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "macd_cross", bar_time, close)
        
            # H8
            if "h8" in result["signals"] and result["signals"]["h8"]["signal"] and (not use_state or not self.state.is_signal_sent(sym, p, "h8", bar_time)):
                h8_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "h8", bar_time, close)
            
            # I9
            if "i9" in result["signals"]:
                i9_res = result["signals"]["i9"]
                if i9_res["full_signal"] and (not use_state or not self.state.is_signal_sent(sym, p, "i9", bar_time)):
                    i9_signals.append(result)
                    if use_state: self.state.mark_signal_sent(sym, p, "i9", bar_time, close)
                elif i9_res["h8_signal"] and (not use_state or not self.state.is_signal_sent(sym, p, "h8", bar_time)):
                    h8_signals.append(result)
                    if use_state: self.state.mark_signal_sent(sym, p, "h8", bar_time, close)
        
        if use_state:
            self.state.save()
        return full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals, ema_signals, macd_cross_signals, h8_signals, i9_signals, total_scanned
