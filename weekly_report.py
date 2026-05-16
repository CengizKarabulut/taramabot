"""
Taramabot Haftalık Performans Raporu

state.json'daki sinyalleri okur. Her sembol için tvdatafeed'den güncel günlük
kapanış fiyatını çeker. Sinyal anındaki fiyat state.json'a kaydedilmişse
(scanner.py yeni format) kâr/zarar yüzdesini hesaplar; kaydedilmemişse
(eski format) sadece güncel fiyatı gösterir.
"""

import json
import os
import asyncio
import time
from datetime import datetime

from telegram_sender import get_telegram_sender
from scanner import MarketScanner
from tvDatafeed import Interval  # interval string DEĞİL, enum olmalı


async def get_current_price(symbol, scanner):
    """Sembolün güncel (son günlük kapanış) fiyatını çek."""
    try:
        # ÖNEMLİ: interval bir Interval enum'ı olmalı, "1D" string'i DEĞİL.
        df = scanner.tv.get_hist(symbol, "BIST", interval=Interval.in_daily, n_bars=5)
        if df is not None and not df.empty:
            return float(df.iloc[-1]["close"])
    except Exception as e:
        print(f"Fiyat çekme hatası ({symbol}): {e}")
    return None


async def generate_weekly_report(strategy_name=None):
    state_file = "state.json"
    if not os.path.exists(state_file):
        return "Henüz veri toplanmadı."

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    scanner = MarketScanner()

    strategy_keys = {
        "smi_macd": "last_sent_smi_macd",
        "rsi": "last_sent_rsi",
        "new_scan": "last_sent_new_scan",
        "rsi_macd": "last_sent_rsi_macd",
        "ema": "last_sent_ema",
        "macd_cross": "last_sent_macd_cross",
    }

    keys_to_process = (
        [strategy_keys[strategy_name]]
        if strategy_name and strategy_name in strategy_keys
        else list(strategy_keys.values())
    )

    header = "<b>📊 HAFTALIK PERFORMANS KARNESİ</b>\n"
    header += f"<b>📅 Tarih: {datetime.now().strftime('%d.%m.%Y')}</b>\n"
    header += "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"

    body = ""
    found_any = False
    all_changes = []

    # Aynı sembolün fiyatını tekrar tekrar çekmemek için önbellek
    price_cache = {}

    for key in keys_to_process:
        if key in state and state[key]:
            display_name = key.replace("last_sent_", "").upper()
            signals = state[key]

            strategy_body = ""
            for symbol_period, data in sorted(signals.items()):
                found_any = True
                symbol, period = symbol_period.rsplit("_", 1)

                # Veri formatını kontrol et (eski string vs yeni dict)
                if isinstance(data, dict):
                    signal_price = data.get("price", 0) or 0
                    timestamp = data.get("time", "")
                else:
                    signal_price = 0
                    timestamp = data

                if symbol not in price_cache:
                    price_cache[symbol] = await get_current_price(symbol, scanner)
                current_price = price_cache[symbol]

                try:
                    dt = datetime.fromisoformat(timestamp)
                    formatted_time = dt.strftime("%d/%m %H:%M")
                except Exception:
                    formatted_time = timestamp

                perf_str = ""
                if signal_price and signal_price > 0 and current_price:
                    change = ((current_price - signal_price) / signal_price) * 100
                    all_changes.append(change)
                    emoji = "🟢" if change >= 0 else "🔴"
                    perf_str = (
                        f"\n  └ {emoji} <b>%{change:.2f}</b> "
                        f"(Giriş: {signal_price:.2f} → Son: {current_price:.2f})"
                    )
                elif current_price:
                    perf_str = (
                        f"\n  └ 💰 Güncel Fiyat: {current_price:.2f} "
                        f"(Giriş fiyatı kaydedilmemiş)"
                    )

                strategy_body += (
                    f"• <b>{symbol}</b> ({period}) - {formatted_time}{perf_str}\n\n"
                )

            if strategy_body:
                body += f"<b>🎯 {display_name}</b>\n{strategy_body}"

    if not found_any:
        return "Bu hafta herhangi bir sinyal üretilmedi."

    summary = ""
    if all_changes:
        total = len(all_changes)
        wins = len([c for c in all_changes if c >= 0])
        avg = sum(all_changes) / total
        summary = "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        summary += "<b>📈 ÖZET</b>\n"
        summary += f"• Fiyatlı sinyal: {total}\n"
        summary += f"• Kazançlı: {wins}/{total} (%{wins / total * 100:.0f})\n"
        summary += f"• Ortalama: %{avg:.2f}\n"

    footer = "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
    footer += "<i>Kâr/Zarar, sinyal anındaki fiyat ile güncel fiyat karşılaştırılarak hesaplanır.</i>"

    return header + body + summary + footer


def _send_chunked(sender, text):
    """Telegram 4096 karakter sınırına takılmamak için satır bazında parçalayıp gönderir."""
    limit = 3500
    if len(text) <= limit:
        sender.send_message(text)
        return
    buf = ""
    for ln in text.split("\n"):
        if len(buf) + len(ln) + 1 > limit:
            sender.send_message(buf)
            time.sleep(0.5)
            buf = ""
        buf += ln + "\n"
    if buf.strip():
        sender.send_message(buf)


if __name__ == "__main__":
    import sys

    strategy = sys.argv[1] if len(sys.argv) > 1 else None

    async def main():
        report = await generate_weekly_report(strategy)
        sender = get_telegram_sender()
        _send_chunked(sender, report)

    asyncio.run(main())
