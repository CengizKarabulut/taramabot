"""
Taramabot Ana Modülü
Botun ana giriş noktasıdır. Tarama işlemlerini başlatır ve yönetir.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, time
from pathlib import Path

from config import (
    LOG_LEVEL, LOG_FORMAT, BIST_STOCKS, COMMODITIES, CRYPTO,
    EMOJI_INFO, EMOJI_ERROR, EMOJI_SUCCESS
)
from scanner import MarketScanner
from telegram_sender import get_telegram_sender
from scheduler import Scheduler, TZ_TURKEY

# Loglama konfigürasyonu
logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

BIST_OPEN_TIME = time(10, 0)
BIST_CLOSE_TIME = time(18, 10)

STRATEGY_NAMES = {
    "full": "S-M-V-2",
    "smi": "S-M-2",
    "rsi": "R-V-1",
    "new": "A-M-V-1",
    "rsi_macd": "R-M-V-1",
    "ema": "E-V-1",
    "macd_cross": "M-1",
    "h8": "S-M-1",
    "i9": "S-M-V-1",
}
STRATEGY_KEYS = tuple(STRATEGY_NAMES)
PERIOD_ORDER = ["15m", "30m", "45m", "1H", "2H", "4H", "1D", "1W", "1M"]
TELEGRAM_ENABLED = os.getenv("DISABLE_TELEGRAM", "").strip().lower() not in {
    "1", "true", "yes", "on"
}


def _is_bist_market_closed(market_type: str) -> bool:
    if market_type.lower() != "bist":
        return False

    now = datetime.now(TZ_TURKEY)
    if now.weekday() >= 5:
        return True
    return not (BIST_OPEN_TIME <= now.time() <= BIST_CLOSE_TIME)


def _signal_count(*signal_groups: list[dict]) -> int:
    return sum(len(group or []) for group in signal_groups)


def _build_common_signal_map(all_results: list[dict]) -> dict:
    """Birden fazla strateji/periyotta görülen sembolleri görsel formatına dönüştür."""
    symbol_map = {}

    for result in all_results:
        period = result["period"]
        for strategy_key in STRATEGY_KEYS:
            for item in result.get(strategy_key, []):
                symbol = item.get("symbol")
                if not symbol:
                    continue
                symbol_map.setdefault(symbol, {}).setdefault(period, []).append({
                    "code": STRATEGY_NAMES[strategy_key],
                    "change": item.get("change"),
                    "daily_change": item.get("daily_change", item.get("change")),
                    "current_price": item.get("current_price", item.get("close")),
                })

    filtered_map = {}
    for symbol, periods in symbol_map.items():
        signal_count = sum(
            len({entry["code"] for entry in entries})
            for entries in periods.values()
        )
        if signal_count > 1:
            filtered_map[symbol] = periods

    return filtered_map


def _write_scan_result(result: dict | None) -> None:
    """Bir periyot sonucunu paralel tarama birlestiricisi icin kucuk JSON olarak yaz."""
    output_path = os.getenv("SCAN_RESULT_FILE", "").strip()
    if not output_path:
        return

    payload = result or {}
    compact = {
        "period": payload.get("period"),
        "market_type": payload.get("market_type", "bist"),
        "total_scanned": payload.get("total_scanned", 0),
    }
    for strategy_key in STRATEGY_KEYS:
        compact[strategy_key] = [
            {
                "symbol": item.get("symbol"),
                "change": float(item.get("change", 0) or 0),
                "daily_change": float(item.get("daily_change", item.get("change", 0)) or 0),
                "current_price": float(item.get("current_price", item.get("close", 0)) or 0),
                "close": float(item.get("close", item.get("current_price", 0)) or 0),
            }
            for item in payload.get(strategy_key, [])
        ]

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(compact, ensure_ascii=False), encoding="utf-8")
    temporary.replace(destination)


def send_saved_results(paths: list[str]) -> None:
    """Paralel hesaplanan periyot raporlarini kronolojik sirayla Telegram'a gonder."""
    if not TELEGRAM_ENABLED:
        return

    results = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as file:
                result = json.load(file)
            if result.get("period"):
                results.append(result)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Paralel tarama sonucu okunamadi (%s): %s", path, exc)

    order = {period: index for index, period in enumerate(PERIOD_ORDER)}
    results.sort(key=lambda item: order.get(item.get("period"), len(order)))
    telegram_sender = get_telegram_sender()
    enabled_codes = ["M-1", "S-M-1", "S-M-V-1", "E-V-1", "R-M-V-1", "A-M-V-1", "S-M-V-2", "S-M-2", "R-V-1"]

    for result in results:
        telegram_sender.send_grouped_summary(
            period=result["period"],
            full_signals=result.get("full", []),
            smi_signals=result.get("smi", []),
            rsi_signals=result.get("rsi", []),
            new_scan_signals=result.get("new", []),
            rsi_macd_signals=result.get("rsi_macd", []),
            ema_signals=result.get("ema", []),
            macd_cross_signals=result.get("macd_cross", []),
            h8_signals=result.get("h8", []),
            i9_signals=result.get("i9", []),
            market_type=result.get("market_type", "bist"),
            total_scanned=result.get("total_scanned"),
            enabled_strategy_codes=enabled_codes,
        )
        period_signal_map = _build_common_signal_map([result])
        if period_signal_map:
            telegram_sender.send_common_signal_visuals(
                period_signal_map,
                title=f"{result['period']} Çoklu Sinyal Özeti",
            )

    send_final_summary(results)


async def main_scan_logic(market_type: str, period: str, use_state: bool = True):
    """
    Ana tarama mantığını çalıştırır.
    Sembolleri tarar, sinyalleri işler ve Telegram'a rapor gönderir.
    """
    scanner = MarketScanner()
    telegram_sender = get_telegram_sender() if TELEGRAM_ENABLED else None

    # Periyot ismini düzelt (TV formatına uygun hale getir)
    period_map = {
        "15m": "15m", "15M": "15m",
        "30m": "30m", "30M": "30m",
        "45m": "45m", "45M": "45m",
        "1h": "1H", "1H": "1H",
        "2h": "2H", "2H": "2H",
        "4h": "4H", "4H": "4H",
        "1d": "1D", "1D": "1D",
        "1w": "1W", "1W": "1W",
        "1m": "1M", "1M": "1M"
    }
    period = period_map.get(period, period)
    
    logger.info(f"Tarama başlatılıyor: Pazar={market_type.upper()}, Periyot={period}, State={use_state}")

    try:
        # scan_market çağrısı
        scan_results = await scanner.scan_market(
            market_type=market_type,
            period=period,
            strategies=["smi_macd", "rsi", "new_scan", "rsi_macd", "ema", "macd_cross", "h8", "i9"],
            use_state=use_state
        )
        full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals, ema_signals, macd_cross_signals, h8_signals, i9_signals, total_scanned = scan_results

        if (
            use_state
            and _is_bist_market_closed(market_type)
            and _signal_count(
                full_signals,
                smi_signals,
                rsi_signals,
                new_scan_signals,
                rsi_macd_signals,
                ema_signals,
                macd_cross_signals,
                h8_signals,
                i9_signals,
            ) == 0
        ):
            logger.info(
                "BIST kapalıyken state filtresi hiç sinyal bırakmadı; mevcut sinyaller için state filtresiz tekrar taranıyor."
            )
            scan_results = await scanner.scan_market(
                market_type=market_type,
                period=period,
                strategies=["smi_macd", "rsi", "new_scan", "rsi_macd", "ema", "macd_cross", "h8", "i9"],
                use_state=False
            )
            full_signals, smi_signals, rsi_signals, new_scan_signals, rsi_macd_signals, ema_signals, macd_cross_signals, h8_signals, i9_signals, total_scanned = scan_results

        # 1. Gruplanmış özet listeyi gönder (Sinyal olsun veya olmasın mesaj gönderilir)
        if telegram_sender:
            telegram_sender.send_grouped_summary(
            period=period, 
            full_signals=full_signals, 
            smi_signals=smi_signals, 
            rsi_signals=rsi_signals, 
            new_scan_signals=new_scan_signals, 
            rsi_macd_signals=rsi_macd_signals, 
            ema_signals=ema_signals,
            macd_cross_signals=macd_cross_signals,
            h8_signals=h8_signals,
            i9_signals=i9_signals,
            market_type=market_type,
            total_scanned=total_scanned,
            enabled_strategy_codes=["M-1", "S-M-1", "S-M-V-1", "E-V-1", "R-M-V-1", "A-M-V-1", "S-M-V-2", "S-M-2", "R-V-1"]
            )
        
        result = {
            "period": period,
            "market_type": market_type,
            "total_scanned": total_scanned,
            "full": full_signals,
            "smi": smi_signals,
            "rsi": rsi_signals,
            "new": new_scan_signals,
            "rsi_macd": rsi_macd_signals,
            "ema": ema_signals,
            "macd_cross": macd_cross_signals,
            "h8": h8_signals,
            "i9": i9_signals
        }
        _write_scan_result(result)

        # Aynı zaman diliminde birden fazla stratejiden sinyal alanları ayrıca özetle.
        period_signal_map = _build_common_signal_map([result])
        if telegram_sender and period_signal_map:
            telegram_sender.send_common_signal_visuals(
                period_signal_map,
                title=f"{period} Çoklu Sinyal Özeti",
            )

        # Sonuçları bir sonraki aşama (zaman dilimleri arası özet) için döndür.
        return result

    except Exception as e:
        logger.error(f"Ana tarama mantığında hata: {str(e)}", exc_info=True)
        if telegram_sender:
            telegram_sender.send_error(f"Tarama sırasında bir hata oluştu: {str(e)}")
        return None


async def run_multi_scan(market_type: str = "bist", period: str = "1D", use_state: bool = True):
    """Tüm zaman dilimlerini KESİN SIRAYLA (Küçükten Büyüğe) tara."""
    # Sıralama: 15m -> 30m -> 45m -> 1H -> 2H -> 4H -> 1D -> 1W -> 1M
    periods = ["15m", "30m", "45m", "1H", "2H", "4H", "1D", "1W", "1M"]
    logger.info(f"Çoklu tarama başlatılıyor: {periods}, State={use_state}")
    
    all_results = []
    for p in periods:
        res = await main_scan_logic(market_type, p, use_state=use_state)
        if res:
            all_results.append(res)
        # Her periyot arasında güvenli bir bekleme
        await asyncio.sleep(5)
    
    # Tüm taramalar bitince BİRDEN FAZLA taramada çıkan hisseleri belirtecek özel özet raporu oluştur
    if all_results:
        send_final_summary(all_results)


def send_final_summary(all_results: list):
    """
    Tüm taramalar bittikten sonra BİRDEN FAZLA taramada çıkan hisseleri özetler.
    """
    if not TELEGRAM_ENABLED:
        return

    telegram_sender = get_telegram_sender()
    filtered_map = _build_common_signal_map(all_results)

    if not filtered_map:
        return

    telegram_sender.send_common_signal_visuals(
        filtered_map,
        title="Zaman Dilimleri Arası Çoklu Sinyal Özeti",
    )

async def run_bot():
    """
    Botu çalıştıran ana fonksiyon.
    """
    # Argümanları kontrol et
    force = "--force" in sys.argv
    nostate = "--nostate" in sys.argv # Daha önce gönderilenleri dikkate alma, hepsini tara
    
    if len(sys.argv) > 1 and sys.argv[1] not in ["--force", "--nostate"]:
        command = sys.argv[1]
        if command == "scan":
            period = sys.argv[2]
            market_type = sys.argv[3].lower() if len(sys.argv) > 3 else "bist"
            await main_scan_logic(market_type, period, use_state=(not nostate))
        elif command == "multi":
            market_type = sys.argv[2].lower() if len(sys.argv) > 2 else "bist"
            await run_multi_scan(market_type, use_state=(not nostate))
        elif command == "summary":
            send_saved_results(sys.argv[2:])
        elif command == "auto":
            from scheduler import get_scheduler
            scheduler = get_scheduler()
            auto_args = [arg for arg in sys.argv[2:] if not arg.startswith("--")]
            market_type = auto_args[0].lower() if auto_args else "bist"
            async def run_multi_scan_with_state(selected_market_type):
                await run_multi_scan(selected_market_type, use_state=(not nostate))
            # auto komutu ile birlikte --force gelirse scheduler'daki kontrolleri atla
            await scheduler.run_once_if_needed(run_multi_scan_with_state, market_type=market_type, force=force)
        else:
            logger.warning(f"Bilinmeyen komut: {command}")
            sys.exit(1)
    else:
        # Hiç komut yoksa veya sadece flaglar varsa normal scheduler başlat
        from scheduler import get_scheduler
        scheduler = get_scheduler()
        await scheduler.start(run_multi_scan, market_type="bist")


if __name__ == "__main__":
    asyncio.run(run_bot())
