"""
Taramabot Tarama Motoru Modülü
Asenkron olarak sembolleri tarar ve alım sinyallerini tespit eder.
SMI/MACD ve RSI olmak üzere iki farklı strateji destekler.
"""

import asyncio
import logging
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from tvDatafeed import TvDatafeed, Interval

from config import (
    TV_USERNAME, TV_PASSWORD, BIST_STOCKS, COMMODITIES, CRYPTO,
    BARS_TO_FETCH, STATE_FILE
)
from indicators import check_smi_macd_signal, check_rsi_signal

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
            return {"last_sent_smi_macd": {}, "last_sent_rsi": {}}
        
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
                # Eski format uyumluluğu
                if "last_sent" in state and "last_sent_smi_macd" not in state:
                    state["last_sent_smi_macd"] = state.pop("last_sent", {})
                    state["last_sent_rsi"] = {}
                return state
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.error(f"Durum dosyası yükleme hatası: {e}. Yeni durum oluşturuluyor.")
            return {"last_sent_smi_macd": {}, "last_sent_rsi": {}}
    
    def save(self) -> None:
        """Durumu dosyaya kaydet."""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
            logger.info(f"Durum kaydedildi: {self.state_file}")
        except Exception as e:
            logger.error(f"Durum kaydetme hatası: {e}")
    
    def is_signal_sent(self, symbol: str, period: str, strategy: str) -> bool:
        """Sinyal daha önce gönderilmiş mi kontrol et."""
        key = f"{symbol}_{period}"
        if strategy == "smi_macd":
            return key in self.state.get("last_sent_smi_macd", {})
        elif strategy == "rsi":
            return key in self.state.get("last_sent_rsi", {})
        return False
    
    def mark_signal_sent(self, symbol: str, period: str, strategy: str, bar_time: str) -> None:
        """Sinyali gönderildi olarak işaretle."""
        key = f"{symbol}_{period}"
        if strategy == "smi_macd":
            self.state.setdefault("last_sent_smi_macd", {})[key] = bar_time
        elif strategy == "rsi":
            self.state.setdefault("last_sent_rsi", {})[key] = bar_time


class MarketScanner:
    """Pazar tarayıcı."""
    
    def __init__(self):
        self.tv = self._create_tv_connection()
        self.state = ScannerState()
    
    def _create_tv_connection(self) -> TvDatafeed:
        """TradingView bağlantısı oluştur."""
        if TV_USERNAME and TV_PASSWORD:
            try:
                return TvDatafeed(username=TV_USERNAME, password=TV_PASSWORD)
            except Exception as e:
                logger.warning(f"TradingView giriş başarısız: {e}. Anonim bağlantı kullanılıyor.")
                return TvDatafeed()
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
            strategies = ["smi_macd", "rsi"]
        
        try:
            # Veri çek
            df = self.tv.get_hist(symbol, exchange, interval=interval, n_bars=BARS_TO_FETCH)
            
            if df is None or df.empty:
                logger.warning(f"Veri çekilemedi: {symbol}")
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
            
            # SMI/MACD Stratejisi
            if "smi_macd" in strategies:
                smi_result = check_smi_macd_signal(df)
                result["signals"]["smi_macd"] = smi_result
            
            # RSI Stratejisi
            if "rsi" in strategies:
                rsi_result = check_rsi_signal(df)
                result["signals"]["rsi"] = rsi_result
            
            return result
            
        except Exception as e:
            logger.error(f"Tarama hatası ({symbol}): {str(e)}")
            return None
    
    async def scan_market(
        self,
        market_type: str = "bist",
        period: str = "1D",
        strategies: List[str] = None
    ) -> Tuple[List[dict], List[dict], List[dict], int]:
        """
        Pazarı tara.
        Returns: (full_signals, smi_signals, rsi_signals, total_scanned)
        """
        if strategies is None:
            strategies = ["smi_macd", "rsi"]
        
        # Sembol listesini seç
        if market_type.lower() == "bist":
            symbols = [(s, "BIST") for s in BIST_STOCKS]
        elif market_type.lower() == "emtia":
            symbols = COMMODITIES
        elif market_type.lower() == "kripto":
            symbols = CRYPTO
        else:
            logger.error(f"Bilinmeyen pazar türü: {market_type}")
            return [], [], [], 0
        
        # Zaman dilimini TvDatafeed formatına çevir
        interval_map = {
            "1H": Interval.in_1_hour,
            "4H": Interval.in_4_hour,
            "1D": Interval.in_daily,
            "1W": Interval.in_weekly
        }
        interval = interval_map.get(period, Interval.in_daily)
        
        logger.info(f"{market_type.upper()} pazarı taranıyor ({period})...")
        
        # Asenkron tarama
        tasks = [
            self.scan_symbol(sym, exc, interval, period, strategies)
            for sym, exc in symbols
        ]
        
        results = await asyncio.gather(*tasks)
        total_scanned = len([r for r in results if r is not None])
        results = [r for r in results if r is not None]
        
        # Sinyalleri kategorize et
        full_signals = []
        smi_signals = []
        rsi_signals = []
        
        for result in results:
            # SMI/MACD sinyalleri
            if "smi_macd" in result["signals"]:
                smi_result = result["signals"]["smi_macd"]
                
                if smi_result["full_buy_signal"]:
                    if not self.state.is_signal_sent(result["symbol"], period, "smi_macd"):
                        full_signals.append(result)
                        self.state.mark_signal_sent(result["symbol"], period, "smi_macd", result["bar_time"])
                
                elif smi_result["smi_macd_buy"]:
                    if not self.state.is_signal_sent(result["symbol"], period, "smi_macd"):
                        smi_signals.append(result)
                        self.state.mark_signal_sent(result["symbol"], period, "smi_macd", result["bar_time"])
            
            # RSI sinyalleri
            if "rsi" in result["signals"]:
                rsi_result = result["signals"]["rsi"]
                
                if rsi_result["signal"]:
                    if not self.state.is_signal_sent(result["symbol"], period, "rsi"):
                        rsi_signals.append(result)
                        self.state.mark_signal_sent(result["symbol"], period, "rsi", result["bar_time"])
        
        # Durumu kaydet
        self.state.save()
        
        logger.info(f"Tarama tamamlandı: {total_scanned} sembol tarandı. {len(full_signals)} tam sinyal, {len(smi_signals)} SMI sinyali, {len(rsi_signals)} RSI sinyali")
        
        return full_signals, smi_signals, rsi_signals, total_scanned
