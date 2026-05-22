"""
Taramabot Haftalık Performans Raporu (Strateji + Zaman Dilimi Bazlı)

Her strateji için:
  - 15m / 1H / 4H / 1D / 1W / 1M zaman dilimleri ayrı ayrı raporlanır
  - Her zaman diliminin sonunda kendi istatistiği yer alır
  - En sonda o stratejinin TÜM zaman dilimlerini kapsayan genel toplam istatistiği gelir

Kullanım:
    python weekly_report.py                 # Tüm stratejileri raporlar
    python weekly_report.py smi_macd        # Sadece SMI/MACD
    python weekly_report.py rsi
    python weekly_report.py new_scan
    python weekly_report.py rsi_macd
    python weekly_report.py ema
    python weekly_report.py macd_cross
"""

import json
import os
import asyncio
import time
import sys
from datetime import datetime

from telegram_sender import get_telegram_sender
from scanner import MarketScanner
from tvDatafeed import Interval


# --------------------------------------------------------------------------
# Sabitler
# --------------------------------------------------------------------------

STRATEGY_META = {
    "smi_macd":   {"key": "last_sent_smi_macd",   "emoji": "🟡", "title": "SMI / MACD ALIM"},
    "rsi":        {"key": "last_sent_rsi",        "emoji": "🔵", "title": "RSI ALIM"},
    "new_scan":   {"key": "last_sent_new_scan",   "emoji": "🟣", "title": "ÖZEL TARAMA (SMA + MACD)"},
    "rsi_macd":   {"key": "last_sent_rsi_macd",   "emoji": "🟢", "title": "RSI + MACD + HACİM"},
    "ema":        {"key": "last_sent_ema",        "emoji": "🟠", "title": "EMA DİZİLİMİ + HACİM"},
    "macd_cross": {"key": "last_sent_macd_cross", "emoji": "💜", "title": "MACD POZİTİF KESİŞİM"},
}

# state.json içindeki periyot ekleri ile gösterim isimleri
PERIOD_ORDER = ["15m", "1H", "4H", "1D", "1W", "1M"]
PERIOD_NAMES = {
    "15m": "15 DAKİKA",
    "1H":  "1 SAAT",
    "4H":  "4 SAAT",
    "1D":  "GÜNLÜK",
    "1W":  "HAFTALIK",
    "1M":  "AYLIK",
}

DIVIDER = "<b>━━━━━━━━━━━━━━━━━━━━━━</b>"


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------

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


def _stats_block(title: str, rows: list) -> str:
    """
    rows: (symbol, period, t_str, signal_price, current_price, change_or_None) tuple listesi
    Tek bir istatistik bloğu döner.
    """
    changes = [r for r in rows if r[5] is not None]
    out = f"<b>📊 {title} İSTATİSTİĞİ</b>\n"
    out += f"• Toplam sinyal: <b>{len(rows)}</b>\n"
    out += f"• Ölçülebilir (fiyatlı): <b>{len(changes)}</b>\n"

    if changes:
        wins = [c for c in changes if c[5] >= 0]
        losses = [c for c in changes if c[5] < 0]
        avg = sum(c[5] for c in changes) / len(changes)
        win_rate = len(wins) / len(changes) * 100

        best = max(changes, key=lambda c: c[5])
        worst = min(changes, key=lambda c: c[5])

        out += f"• Kazançlı: <b>{len(wins)}</b> 🟢   |   Kayıplı: <b>{len(losses)}</b> 🔴\n"
        out += f"• Başarı oranı: <b>%{win_rate:.1f}</b>\n"
        out += f"• Ortalama getiri: <b>%{avg:+.2f}</b>\n"
        out += f"• 🏆 En iyi: <b>{best[0]}</b> ({best[1]}) %{best[5]:+.2f}\n"
        out += f"• 📉 En kötü: <b>{worst[0]}</b> ({worst[1]}) %{worst[5]:+.2f}\n"
    else:
        out += "<i>Bu dilimde fiyatlı (ölçülebilir) sinyal yok.</i>\n"

    return out


def _format_signal_lines(rows: list) -> str:
    """Sinyal satırlarını biçimle. rows kazanca göre azalan sıralı olmalı."""
    body = ""
    for symbol, period, t_str, sp, cp, change in rows:
        if change is not None:
            emoji = "🟢" if change >= 0 else "🔴"
            body += (
                f"• <b>{symbol}</b> — {t_str}\n"
                f"  └ {emoji} <b>%{change:+.2f}</b> "
                f"(Giriş: {sp:.2f} → Son: {cp:.2f})\n"
            )
        elif cp is not None:
            body += (
                f"• <b>{symbol}</b> — {t_str}\n"
                f"  └ 💰 Güncel: {cp:.2f} <i>(giriş fiyatı kayıtlı değil)</i>\n"
            )
        else:
            body += (
                f"• <b>{symbol}</b> — {t_str}\n"
                f"  └ ⚪ Veri alınamadı\n"
            )
    return body


# --------------------------------------------------------------------------
# Strateji rapor üretici
# --------------------------------------------------------------------------

async def _build_strategy_messages(strategy_key: str, state: dict, scanner, price_cache: dict) -> list[str]:
    """
    Tek bir strateji için, ZAMAN DİLİMİ bazında ayrılmış mesaj listesi döner.
    Sıra şöyle:
        1. Strateji başlık mesajı
        2..N. Her zaman dilimi için ayrı bir mesaj (sinyaller + o TF'in istatistiği)
        N+1. O stratejinin TÜM TF'lerini kapsayan toplam istatistik mesajı
    """
    meta = STRATEGY_META[strategy_key]
    state_key = meta["key"]
    signals = state.get(state_key, {})

    if not signals:
        return [
            f"{DIVIDER}\n"
            f"<b>{meta['emoji']} HAFTALIK RAPOR — {meta['title']}</b>\n"
            f"<b>📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}</b>\n"
            f"{DIVIDER}\n"
            f"<i>Bu hafta bu strateji için sinyal üretilmedi.</i>"
        ]

    # 1) Sinyalleri periyoda göre grupla
    grouped: dict[str, list] = {p: [] for p in PERIOD_ORDER}
    unknown_period: list = []

    for sym_period, data in signals.items():
        # "SYMBOL_PERIOD" formatı — son alttaki "_" üzerinden ayır
        if "_" not in sym_period:
            continue
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

        row = (symbol, period, _format_time(timestamp), signal_price, current_price, change)

        if period in grouped:
            grouped[period].append(row)
        else:
            unknown_period.append(row)

    # 2) Mesajları oluştur
    messages = []

    # (A) Başlık
    header = (
        f"{DIVIDER}\n"
        f"<b>{meta['emoji']} HAFTALIK RAPOR — {meta['title']}</b>\n"
        f"<b>📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}</b>\n"
        f"{DIVIDER}"
    )
    messages.append(header)

    # (B) Her zaman dilimi için ayrı mesaj
    all_rows = []  # genel toplam için
    for period in PERIOD_ORDER:
        rows = grouped[period]
        all_rows.extend(rows)
        if not rows:
            continue  # boş TF'i atla

        # Kazanca göre büyükten küçüğe sırala, fiyatsızları sona at
        rows.sort(key=lambda r: (1 if r[5] is None else 0, -(r[5] if r[5] is not None else 0)))

        period_name = PERIOD_NAMES[period]
        msg = f"<b>⏱ {meta['emoji']} {meta['title']} — {period_name}</b>\n\n"
        msg += _format_signal_lines(rows)
        msg += "\n"
        msg += _stats_block(period_name, rows)
        messages.append(msg)

    # Bilinmeyen periyot varsa onları da ekle
    if unknown_period:
        unknown_period.sort(key=lambda r: (1 if r[5] is None else 0, -(r[5] if r[5] is not None else 0)))
        all_rows.extend(unknown_period)
        msg = f"<b>⏱ {meta['emoji']} {meta['title']} — DİĞER</b>\n\n"
        msg += _format_signal_lines(unknown_period)
        msg += "\n"
        msg += _stats_block("DİĞER", unknown_period)
        messages.append(msg)

    # (C) Genel toplam (tüm TF'ler birlikte)
    summary = f"{DIVIDER}\n"
    summary += f"<b>🎯 {meta['emoji']} {meta['title']} — GENEL TOPLAM (TÜM ZAMAN DİLİMLERİ)</b>\n\n"
    summary += _stats_block("GENEL", all_rows)

    # Genel toplamda kullanılan TF dağılımı
    summary += "\n<b>📦 Zaman dilimi dağılımı:</b>\n"
    for period in PERIOD_ORDER:
        cnt = len(grouped[period])
        if cnt:
            summary += f"  • {PERIOD_NAMES[period]}: {cnt}\n"
    if unknown_period:
        summary += f"  • DİĞER: {len(unknown_period)}\n"

    summary += DIVIDER
    messages.append(summary)

    return messages


# --------------------------------------------------------------------------
# Ana akış
# --------------------------------------------------------------------------

async def generate_weekly_report(strategy_name: str | None = None) -> list[str]:
    """
    İstenen stratejinin (veya hepsinin) mesajlarını sıralı liste olarak döner.
    Bu liste sırasıyla Telegram'a gönderilir.
    """
    state_file = "state.json"
    if not os.path.exists(state_file):
        return ["<b>ℹ️ Henüz veri toplanmadı.</b>\nstate.json bulunamadı."]

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    scanner = MarketScanner()
    price_cache: dict = {}

    if strategy_name and strategy_name in STRATEGY_META:
        targets = [strategy_name]
    else:
        targets = list(STRATEGY_META.keys())

    all_messages: list[str] = []
    for s in targets:
        msgs = await _build_strategy_messages(s, state, scanner, price_cache)
        all_messages.extend(msgs)

    if not all_messages:
        all_messages.append("<b>ℹ️ Bu hafta herhangi bir sinyal üretilmedi.</b>")

    return all_messages


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
        messages = await generate_weekly_report(strategy)
        sender = get_telegram_sender()
        for i, msg in enumerate(messages):
            _send_chunked(sender, msg)
            if i < len(messages) - 1:
                time.sleep(1.2)  # Telegram rate-limit koruması

    asyncio.run(main())
