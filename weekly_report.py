"""
Taramabot Haftalık Performans Raporu (Strateji Bazlı)

Kullanım:
    python weekly_report.py                 # Tüm stratejileri tek bir raporda gönderir
    python weekly_report.py smi_macd        # Sadece SMI/MACD raporu
    python weekly_report.py rsi             # Sadece RSI raporu
    python weekly_report.py new_scan        # Sadece Özel Tarama raporu
    python weekly_report.py rsi_macd        # Sadece RSI+MACD+Hacim raporu
    python weekly_report.py ema             # Sadece EMA Dizilimi raporu
    python weekly_report.py macd_cross      # Sadece MACD Pozitif Kesişim raporu

Her stratejinin sonunda detaylı istatistik bloğu eklenir:
- Toplam sinyal sayısı
- Fiyatlı (ölçülebilir) sinyal sayısı
- Kazançlı / kayıplı sayısı + başarı oranı
- Ortalama getiri
- En iyi ve en kötü sinyal
"""

import json
import os
import asyncio
import time
import sys
from datetime import datetime

from telegram_sender import get_telegram_sender
from scanner import MarketScanner
from tvDatafeed import Interval  # interval enum, string DEĞİL


# Strateji etiketleri (mesajlarda gösterilecek isim ve emoji)
STRATEGY_META = {
    "smi_macd":   {"key": "last_sent_smi_macd",   "emoji": "🟡", "title": "SMI / MACD ALIM"},
    "rsi":        {"key": "last_sent_rsi",        "emoji": "🔵", "title": "RSI ALIM"},
    "new_scan":   {"key": "last_sent_new_scan",   "emoji": "🟣", "title": "ÖZEL TARAMA (SMA + MACD)"},
    "rsi_macd":   {"key": "last_sent_rsi_macd",   "emoji": "🟢", "title": "RSI + MACD + HACİM"},
    "ema":        {"key": "last_sent_ema",        "emoji": "🟠", "title": "EMA DİZİLİMİ + HACİM"},
    "macd_cross": {"key": "last_sent_macd_cross", "emoji": "💜", "title": "MACD POZİTİF KESİŞİM"},
}


async def get_current_price(symbol, scanner):
    """Sembolün güncel (son günlük kapanış) fiyatını çek."""
    try:
        df = scanner.tv.get_hist(symbol, "BIST", interval=Interval.in_daily, n_bars=5)
        if df is not None and not df.empty:
            return float(df.iloc[-1]["close"])
    except Exception as e:
        print(f"Fiyat çekme hatası ({symbol}): {e}")
    return None


def _format_time(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%d/%m %H:%M")
    except Exception:
        return ts


async def _build_strategy_report(strategy_key: str, state: dict, scanner, price_cache: dict) -> str | None:
    """
    Tek bir strateji için rapor bloğu üretir.
    Sinyali bulunmazsa None döner.
    """
    meta = STRATEGY_META[strategy_key]
    state_key = meta["key"]
    signals = state.get(state_key, {})

    if not signals:
        return None

    # Her sinyali işle
    rows = []        # (symbol, period, formatted_time, signal_price, current_price, change_or_None)
    changes = []     # sadece fiyatlı olanlar
    for sym_period, data in signals.items():
        symbol, period = sym_period.rsplit("_", 1)

        if isinstance(data, dict):
            signal_price = data.get("price", 0) or 0
            timestamp = data.get("time", "")
        else:
            signal_price = 0
            timestamp = data

        if symbol not in price_cache:
            price_cache[symbol] = await get_current_price(symbol, scanner)
        current_price = price_cache[symbol]

        change = None
        if signal_price and signal_price > 0 and current_price:
            change = ((current_price - signal_price) / signal_price) * 100
            changes.append((symbol, period, change))

        rows.append((symbol, period, _format_time(timestamp), signal_price, current_price, change))

    # Başlık
    header = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
    header += f"<b>{meta['emoji']} HAFTALIK RAPOR — {meta['title']}</b>\n"
    header += f"<b>📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}</b>\n"
    header += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"

    # Sinyal listesi (kazanç oranına göre büyükten küçüğe sıralı; fiyatsızlar sonda)
    def _sort_key(r):
        # change None ise -inf gibi davranmasın diye, fiyatsızları en sona at
        return (1 if r[5] is None else 0, -(r[5] if r[5] is not None else 0))

    rows.sort(key=_sort_key)

    body = ""
    for symbol, period, t_str, sp, cp, change in rows:
        if change is not None:
            emoji = "🟢" if change >= 0 else "🔴"
            body += (
                f"• <b>{symbol}</b> ({period}) — {t_str}\n"
                f"  └ {emoji} <b>%{change:+.2f}</b> "
                f"(Giriş: {sp:.2f} → Son: {cp:.2f})\n\n"
            )
        elif cp is not None:
            body += (
                f"• <b>{symbol}</b> ({period}) — {t_str}\n"
                f"  └ 💰 Güncel: {cp:.2f} <i>(giriş fiyatı kayıtlı değil)</i>\n\n"
            )
        else:
            body += (
                f"• <b>{symbol}</b> ({period}) — {t_str}\n"
                f"  └ ⚪ Veri alınamadı\n\n"
            )

    # İstatistik bloğu
    stats = "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
    stats += f"<b>📊 {meta['title']} İSTATİSTİKLERİ</b>\n"
    stats += f"• Toplam sinyal: <b>{len(rows)}</b>\n"
    stats += f"• Ölçülebilir (fiyatlı): <b>{len(changes)}</b>\n"

    if changes:
        wins = [c for c in changes if c[2] >= 0]
        losses = [c for c in changes if c[2] < 0]
        avg = sum(c[2] for c in changes) / len(changes)
        win_rate = len(wins) / len(changes) * 100

        best = max(changes, key=lambda c: c[2])
        worst = min(changes, key=lambda c: c[2])

        stats += f"• Kazançlı: <b>{len(wins)}</b> 🟢   |   Kayıplı: <b>{len(losses)}</b> 🔴\n"
        stats += f"• Başarı oranı: <b>%{win_rate:.1f}</b>\n"
        stats += f"• Ortalama getiri: <b>%{avg:+.2f}</b>\n"
        stats += f"• 🏆 En iyi: <b>{best[0]}</b> ({best[1]}) %{best[2]:+.2f}\n"
        stats += f"• 📉 En kötü: <b>{worst[0]}</b> ({worst[1]}) %{worst[2]:+.2f}\n"
    else:
        stats += "<i>Bu hafta fiyatlı (kâr/zarar ölçülebilir) sinyal yok.</i>\n"

    stats += "<b>━━━━━━━━━━━━━━━━━━━━━━</b>"

    return header + body + stats


async def generate_weekly_report(strategy_name: str | None = None) -> list[str]:
    """
    İstenen stratejinin (veya hepsinin) rapor metinlerini liste olarak döner.
    Her strateji ayrı bir mesaj olarak gönderilecek şekilde liste döner.
    """
    state_file = "state.json"
    if not os.path.exists(state_file):
        return ["<b>ℹ️ Henüz veri toplanmadı.</b>\nstate.json bulunamadı."]

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    scanner = MarketScanner()
    price_cache = {}

    if strategy_name and strategy_name in STRATEGY_META:
        targets = [strategy_name]
    else:
        targets = list(STRATEGY_META.keys())

    reports = []
    for s in targets:
        rep = await _build_strategy_report(s, state, scanner, price_cache)
        if rep:
            reports.append(rep)

    if not reports:
        if strategy_name:
            meta = STRATEGY_META[strategy_name]
            reports.append(
                f"<b>{meta['emoji']} HAFTALIK RAPOR — {meta['title']}</b>\n"
                f"<i>Bu hafta bu strateji için sinyal üretilmedi.</i>"
            )
        else:
            reports.append("<b>ℹ️ Bu hafta herhangi bir sinyal üretilmedi.</b>")

    return reports


def _send_chunked(sender, text):
    """Telegram 4096 karakter sınırına takılmamak için satır bazında parçala."""
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
    strategy = sys.argv[1] if len(sys.argv) > 1 else None

    async def main():
        reports = await generate_weekly_report(strategy)
        sender = get_telegram_sender()
        for i, rep in enumerate(reports):
            _send_chunked(sender, rep)
            # Mesajlar arasında küçük bir nefes payı
            if i < len(reports) - 1:
                time.sleep(1.5)

    asyncio.run(main())
