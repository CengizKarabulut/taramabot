"""
Taramabot Tarama Motoru ModÃ¼lÃ¼
Asenkron olarak sembolleri tarar ve alÄ±m sinyallerini tespit eder.
SMI/MACD, RSI ve SMA tabanlÄ± Ã¼Ã§ farklÄ± strateji destekler.
"""

import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from zoneinfo import ZoneInfo
from tvDatafeed import TvDatafeed, Interval

from config import (
    TV_USERNAME, TV_PASSWORD, BIST_STOCKS, NASDAQ_100, SP_500, COMMODITIES, CRYPTO,
    BARS_TO_FETCH, STATE_FILE
)
from indicators import (
    check_smi_macd_signal, check_rsi_signal, check_new_scan_signal, 
    check_rsi_macd_scan_signal, check_ema_scan_signal, check_macd_positive_cross_signal, check_h8_smi_macd_positive_signal, check_i9_smi_macd_positive_full_signal
)
from filters import candidate_signal_exists, passes_strategy_filter, should_fetch_daily_limit_df

logger = logging.getLogger(__name__)

TZ_TURKEY = ZoneInfo("Europe/Istanbul")
SIGNAL_HISTORY_RETENTION_DAYS = int(os.getenv("SIGNAL_HISTORY_RETENTION_DAYS", "45"))
SIGNAL_HISTORY_MAX_ENTRIES = int(os.getenv("SIGNAL_HISTORY_MAX_ENTRIES", "25000"))


class ScannerState:
    """Tarama durumunu yÃ¶netir (son gÃ¶nderilen sinyalleri takip eder)."""
    
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()
    
    def _load_state(self) -> dict:
        """Durumu dosyadan yÃ¼kle."""
        if not os.path.exists(self.state_file):
            logger.info(f"Durum dosyasÄ± bulunamadÄ±: {self.state_file}. Yeni durum oluÅŸturuluyor.")
            return {"last_sent_smi_macd": {}, "last_sent_rsi": {}, "last_sent_new_scan": {}, "last_sent_rsi_macd": {}, "last_sent_ema": {}, "last_sent_macd_cross": {}, "last_sent_h8": {}, "last_sent_i9": {}, "signal_history": []}
        
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
                # Eksik anahtarlarÄ± tamamla
                for key in ["last_sent_smi_macd", "last_sent_rsi", "last_sent_new_scan", "last_sent_rsi_macd", "last_sent_ema", "last_sent_macd_cross", "last_sent_h8", "last_sent_i9"]:
                    if key not in state:
                        state[key] = {}
                if not isinstance(state.get("signal_history"), list):
                    state["signal_history"] = []
                return state
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.error(f"Durum dosyasÄ± yÃ¼kleme hatasÄ±: {e}. Yeni durum oluÅŸturuluyor.")
            return {"last_sent_smi_macd": {}, "last_sent_rsi": {}, "last_sent_new_scan": {}, "last_sent_rsi_macd": {}, "last_sent_ema": {}, "last_sent_macd_cross": {}, "last_sent_h8": {}, "last_sent_i9": {}, "signal_history": []}

    def _prune_signal_history(self) -> None:
        history = self.state.get("signal_history", [])
        if not isinstance(history, list):
            self.state["signal_history"] = []
            return

        cutoff = datetime.now(TZ_TURKEY) - timedelta(days=SIGNAL_HISTORY_RETENTION_DAYS)
        retained = []
        for event in history:
            if not isinstance(event, dict):
                continue
            try:
                detected_at = datetime.fromisoformat(str(event.get("detected_at", "")))
                if detected_at.tzinfo is None:
                    detected_at = detected_at.replace(tzinfo=TZ_TURKEY)
                else:
                    detected_at = detected_at.astimezone(TZ_TURKEY)
            except (TypeError, ValueError):
                continue
            if detected_at >= cutoff:
                retained.append(event)
        self.state["signal_history"] = retained[-SIGNAL_HISTORY_MAX_ENTRIES:]
    
    def save(self) -> None:
        """Durumu dosyaya kaydet."""
        try:
            self._prune_signal_history()
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
            logger.info(f"Durum kaydedildi: {self.state_file}")
        except Exception as e:
            logger.error(f"Durum kaydetme hatasÄ±: {e}")
    
    def is_signal_sent(self, symbol: str, period: str, strategy: str, bar_time: str) -> bool:
        """Sinyal bu bar iÃ§in daha Ã¶nce gÃ¶nderilmiÅŸ mi kontrol et."""
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
        """Sinyali bu bar iÃ§in gÃ¶nderildi olarak iÅŸaretle ve fiyatÄ± kaydet."""
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
            detected_at = datetime.now(TZ_TURKEY).isoformat(timespec="seconds")
            entry = {
                "time": bar_time,
                "price": price,
                "detected_at": detected_at,
            }
            entry.update(metadata)
            self.state.setdefault(state_key, {})[key] = entry
            history_entry = {
                "symbol": symbol,
                "period": period,
                "strategy": strategy,
                "bar_time": bar_time,
                "detected_at": detected_at,
                "price": price,
            }
            history_entry.update(metadata)
            self.state.setdefault("signal_history", []).append(history_entry)


class MarketScanner:
    """Pazar tarayÄ±cÄ±."""
    
    def __init__(self):
        self.tv = self._create_tv_connection()
        self.state = ScannerState()
        self._daily_limit_cache = {}
    
    def _create_tv_connection(self) -> TvDatafeed:
        """TradingView baÄŸlantÄ±sÄ± oluÅŸtur."""
        if TV_USERNAME and TV_PASSWORD:
            try:
                logger.info(f"TradingView'a giriÅŸ yapÄ±lÄ±yor: {TV_USERNAME}")
                # Hata durumunda programÄ±n Ã§Ã¶kmesini engellemek iÃ§in try-except
                tv = TvDatafeed(username=TV_USERNAME, password=TV_PASSWORD)
                return tv
            except Exception as e:
                logger.warning(f"tvDatafeed giriÅŸ hatasÄ±: {e}. Anonim (nologin) modda devam ediliyor.")
                return TvDatafeed()
        
        logger.info("TradingView'a anonim (nologin) olarak baÄŸlanÄ±lÄ±yor.")
        return TvDatafeed()
    
    @staticmethod
    def _percent_change(current, previous) -> float:
        try:
            current_value = float(current)
            previous_value = float(previous)
        except (TypeError, ValueError):
            return 0.0
        if previous_value == 0:
            return 0.0
        return ((current_value - previous_value) / previous_value) * 100

    def _daily_change_percent(self, df, fallback: float) -> float:
        try:
            last_index = df.index[-1]
            last_date = last_index.date() if hasattr(last_index, "date") else None
            if last_date is not None:
                for pos in range(len(df) - 2, -1, -1):
                    item_index = df.index[pos]
                    item_date = item_index.date() if hasattr(item_index, "date") else None
                    if item_date is not None and item_date < last_date:
                        return self._percent_change(df.iloc[-1]["close"], df.iloc[pos]["close"])
        except Exception:
            pass
        return fallback

    def _get_daily_limit_df(self, symbol: str, exchange: str):
        key = (symbol, exchange)
        if key not in self._daily_limit_cache:
            try:
                self._daily_limit_cache[key] = self.tv.get_hist(
                    symbol,
                    exchange,
                    interval=Interval.in_daily,
                    n_bars=max(BARS_TO_FETCH, 60),
                )
            except Exception as e:
                logger.debug("Gunluk tavan verisi alinamadi (%s): %s", symbol, e)
                self._daily_limit_cache[key] = None
        return self._daily_limit_cache[key]
    async def scan_symbol(
        self,
        symbol: str,
        exchange: str,
        interval: Interval,
        period_str: str,
        strategies: List[str] = None
    ) -> Dict[str, any]:
        """
        Bir sembolÃ¼ tara.
        """
        if strategies is None:
            strategies = ["smi_macd", "rsi", "new_scan", "rsi_macd", "ema", "macd_cross", "h8", "i9"]
        
        try:
            # Veri Ã§ek
            df = self.tv.get_hist(symbol, exchange, interval=interval, n_bars=BARS_TO_FETCH)
            
            if df is None or df.empty or len(df) < 2:
                return None
            
            stale_bar = False
            # Endeks kapalÄ±yken (BIST iÃ§in) son veri eski kalabilir; yine de rapor Ã¼ret.
            if exchange == "BIST":
                last_bar_time = df.index[-1]
                now = datetime.now(last_bar_time.tzinfo)
                # Hafta sonu boÅŸluÄŸunu kapsayacak ÅŸekilde 90 saate esnetildi.
                if interval in [Interval.in_15_minute, Interval.in_30_minute, Interval.in_45_minute, Interval.in_1_hour, Interval.in_2_hour, Interval.in_4_hour]:
                    if (now - last_bar_time).total_seconds() > 324000: # 90 saat
                        stale_bar = True
                        logger.debug(
                            "BIST verisi eski ama taramaya dahil ediliyor: %s %s son_bar=%s",
                            symbol,
                            period_str,
                            last_bar_time,
                        )
            
            # Son bar bilgisi
            last_bar = df.iloc[-1]
            prev_bar = df.iloc[-2] if len(df) > 1 else last_bar
            
            # Fiyat deÄŸiÅŸimi
            change_percent = ((last_bar['close'] - prev_bar['close']) / prev_bar['close']) * 100
            daily_change_percent = self._daily_change_percent(df, fallback=change_percent)
            
            result = {
                "symbol": symbol,
                "exchange": exchange,
                "period": period_str,
                "close": last_bar['close'],
                "change": change_percent,
                "daily_change": daily_change_percent,
                "current_price": last_bar['close'],
                "bar_time": df.index[-1].isoformat(),
                "stale_bar": stale_bar,
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
            
            daily_limit_df = None
            if should_fetch_daily_limit_df(exchange, period_str, candidate_signal_exists(result["signals"])):
                daily_limit_df = self._get_daily_limit_df(symbol, exchange)

            if "smi_macd" in result["signals"]:
                smi_filter = result["signals"]["smi_macd"]
                smi_filter["passes_full_filter"] = passes_strategy_filter(
                    df, "smi_macd", signal_kind="full", daily_df=daily_limit_df
                )
                smi_filter["passes_early_filter"] = passes_strategy_filter(
                    df, "smi_macd", signal_kind="early", daily_df=daily_limit_df
                )
            if "rsi" in result["signals"]:
                result["signals"]["rsi"]["passes_filter"] = passes_strategy_filter(df, "rsi", daily_df=daily_limit_df)
            if "new_scan" in result["signals"]:
                result["signals"]["new_scan"]["passes_filter"] = passes_strategy_filter(df, "new_scan", daily_df=daily_limit_df)
            if "rsi_macd" in result["signals"]:
                result["signals"]["rsi_macd"]["passes_filter"] = passes_strategy_filter(df, "rsi_macd", daily_df=daily_limit_df)
            if "ema" in result["signals"]:
                result["signals"]["ema"]["passes_filter"] = passes_strategy_filter(df, "ema", daily_df=daily_limit_df)
            if "macd_cross" in result["signals"]:
                result["signals"]["macd_cross"]["passes_filter"] = passes_strategy_filter(df, "macd_cross", daily_df=daily_limit_df)
            if "h8" in result["signals"]:
                result["signals"]["h8"]["passes_filter"] = passes_strategy_filter(df, "h8", daily_df=daily_limit_df)
            if "i9" in result["signals"]:
                i9_filter = result["signals"]["i9"]
                i9_filter["passes_filter"] = passes_strategy_filter(df, "i9", daily_df=daily_limit_df)
                i9_filter["passes_h8_filter"] = passes_strategy_filter(df, "h8", daily_df=daily_limit_df)
            return result
            
        except Exception as e:
            logger.error(f"Sembol tarama hatasÄ± ({symbol}): {str(e)}")
            return None
    
    async def scan_market(
        self,
        market_type: str = "bist",
        period: str = "1D",
        strategies: List[str] = None,
        use_state: bool = True
    ) -> Tuple[List[dict], List[dict], List[dict], List[dict], List[dict], List[dict], List[dict], List[dict], List[dict], int]:
        """
        PazarÄ± tara.
        """
        if strategies is None:
            strategies = ["smi_macd", "rsi", "new_scan", "rsi_macd", "ema", "macd_cross", "h8", "i9"]
        
        # Sembol listesini seÃ§
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
            logger.error(f"Bilinmeyen pazar tÃ¼rÃ¼: {market_type}")
            return [], [], [], [], [], [], [], [], [], 0
        
        # Zaman dilimini TvDatafeed formatÄ±na Ã§evir
        interval_map = {
            "15m": Interval.in_15_minute, "15M": Interval.in_15_minute,
            "1h": Interval.in_1_hour, "1H": Interval.in_1_hour,
            "4h": Interval.in_4_hour, "4H": Interval.in_4_hour,
            "1d": Interval.in_daily, "1D": Interval.in_daily,
            "1w": Interval.in_weekly, "1W": Interval.in_weekly,
            "1m": Interval.in_monthly, "1M": Interval.in_monthly
        }
        interval = interval_map.get(period, Interval.in_daily)
        
        logger.info(f"{market_type.upper()} pazarÄ± taranÄ±yor ({period})...")
        
        # Paralel tarama iÃ§in sembolleri parÃ§alara bÃ¶l (TV rate limit korumasÄ±)
        chunk_size = 50
        all_results = []
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i:i+chunk_size]
            tasks = [self.scan_symbol(sym, exc, interval, period, strategies) for sym, exc in chunk]
            chunk_results = await asyncio.gather(*tasks)
            all_results.extend([r for r in chunk_results if r is not None])
            if i + chunk_size < len(symbols):
                await asyncio.sleep(1) # Chunklar arasÄ± kÄ±sa bekleme
        
        results = all_results
        total_scanned = len(results)
        
        # Sinyalleri kategorize et
        full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals, ema_signals, macd_cross_signals, h8_signals, i9_signals = [], [], [], [], [], [], [], [], []
        
        for result in results:
            sym, p, bar_time, close = result["symbol"], period, result["bar_time"], result["close"]
            
            # SMI/MACD
            if "smi_macd" in result["signals"]:
                smi_res = result["signals"]["smi_macd"]
                if smi_res["full_buy_signal"] and smi_res.get("passes_full_filter", True) and (not use_state or not self.state.is_signal_sent(sym, p, "smi_macd", bar_time)):
                    full_signals.append(result)
                    if use_state: self.state.mark_signal_sent(sym, p, "smi_macd", bar_time, close, is_full=True)
                elif smi_res["smi_macd_buy"] and smi_res.get("passes_early_filter", True) and (not use_state or not self.state.is_signal_sent(sym, p, "smi_macd", bar_time)):
                    smi_signals.append(result)
                    if use_state: self.state.mark_signal_sent(sym, p, "smi_macd", bar_time, close, is_full=False)
            
            # RSI
            if "rsi" in result["signals"] and result["signals"]["rsi"]["signal"] and result["signals"]["rsi"].get("passes_filter", True) and (not use_state or not self.state.is_signal_sent(sym, p, "rsi", bar_time)):
                rsi_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "rsi", bar_time, close)
            
            # New Scan
            if "new_scan" in result["signals"] and result["signals"]["new_scan"]["signal"] and result["signals"]["new_scan"].get("passes_filter", True) and (not use_state or not self.state.is_signal_sent(sym, p, "new_scan", bar_time)):
                new_scan_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "new_scan", bar_time, close)
            
            # RSI MACD
            if "rsi_macd" in result["signals"] and result["signals"]["rsi_macd"]["signal"] and result["signals"]["rsi_macd"].get("passes_filter", True) and (not use_state or not self.state.is_signal_sent(sym, p, "rsi_macd", bar_time)):
                rsi_macd_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "rsi_macd", bar_time, close)
            
            # EMA
            if "ema" in result["signals"] and result["signals"]["ema"]["signal"] and result["signals"]["ema"].get("passes_filter", True) and (not use_state or not self.state.is_signal_sent(sym, p, "ema", bar_time)):
                ema_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "ema", bar_time, close)
            
            # MACD Cross
            if "macd_cross" in result["signals"] and result["signals"]["macd_cross"]["signal"] and result["signals"]["macd_cross"].get("passes_filter", True) and (not use_state or not self.state.is_signal_sent(sym, p, "macd_cross", bar_time)):
                macd_cross_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "macd_cross", bar_time, close)
        
            # S-M-1
            if "h8" in result["signals"] and result["signals"]["h8"]["signal"] and result["signals"]["h8"].get("passes_filter", True) and (not use_state or not self.state.is_signal_sent(sym, p, "h8", bar_time)):
                h8_signals.append(result)
                if use_state: self.state.mark_signal_sent(sym, p, "h8", bar_time, close)
            
            # S-M-V-1
            if "i9" in result["signals"]:
                i9_res = result["signals"]["i9"]
                if i9_res["full_signal"] and i9_res.get("passes_filter", True) and (not use_state or not self.state.is_signal_sent(sym, p, "i9", bar_time)):
                    i9_signals.append(result)
                    if use_state: self.state.mark_signal_sent(sym, p, "i9", bar_time, close)
                elif "h8" not in result["signals"] and i9_res["h8_signal"] and i9_res.get("passes_h8_filter", i9_res.get("passes_filter", True)) and (not use_state or not self.state.is_signal_sent(sym, p, "h8", bar_time)):
                    h8_signals.append(result)
                    if use_state: self.state.mark_signal_sent(sym, p, "h8", bar_time, close)
        
        if use_state:
            self.state.save()
        return full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals, ema_signals, macd_cross_signals, h8_signals, i9_signals, total_scanned

