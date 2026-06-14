"""
Taramabot Haftalık Performans Raporu (Strateji + Zaman Dilimi Bazlı)
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
    "macd_cross": {
        "key": "last_sent_macd_cross", "emoji": "🟣", "title": "S1",
        "name": "MACD Pozitif Kesişim",
    },
    "ema": {
        "key": "last_sent_ema", "emoji": "🟠", "title": "S2",
        "name": "EMA Dizilimi",
    },
    "rsi_macd": {
        "key": "last_sent_rsi_macd", "emoji": "🟢", "title": "S3",
        "name": "RSI + MACD + Hacim",
    },
    "new_scan": {
        "key": "last_sent_new_scan", "emoji": "🟡", "title": "S4",
        "name": "SMA + MACD + Hacim",
    },
    "smi_macd_full": {
        "key": "last_sent_smi_macd", "emoji": "🚀", "title": "S5",
        "name": "SMI/MACD Full",
    },
    "smi_macd": {
        "key": "last_sent_smi_macd", "emoji": "⭐", "title": "S6",
        "name": "SMI/MACD",
    },
    "rsi": {
        "key": "last_sent_rsi", "emoji": "💎", "title": "S7",
        "name": "RSI",
    },
    "h8": {
        "key": "last_sent_h8", "emoji": "🔷", "title": "H8",
        "name": "SMI/MACD Pozitif",
    },
    "i9": {
        "key": "last_sent_i9", "emoji": "🔹", "title": "I9",
        "name": "SMI/MACD Pozitif Full",
    },
}

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


async def _build_strategy_messages(strategy_key: str, state: dict, scanner, price_cache: dict) -> list:
    meta = STRATEGY_META[strategy_key]
    state_key = meta["key"]
    signals = state.get(state_key, {})
    if not signals: return []

    grouped = {p: [] for p in PERIOD_ORDER}
    unknown_period = []
    for sym_period, data in signals.items():
        if "_" not in sym_period: continue
        symbol, period = sym_period.rsplit("_", 1)
        if isinstance(data, dict):
            signal_price = data.get("price", 0) or 0
            timestamp = data.get("time", "")
        else:
            signal_price = 0
            timestamp = data
        
        # S5 ve S6 ayrımı (SMI/MACD için)
        if strategy_key == "smi_macd_full" and not (isinstance(data, dict) and data.get("is_full")): continue
        if strategy_key == "smi_macd" and (isinstance(data, dict) and data.get("is_full")): continue

        if symbol not in price_cache:
            price_cache[symbol] = await get_current_price(symbol, scanner)
        current_price = price_cache[symbol]
        change = None
        if signal_price and signal_price > 0 and current_price:
            change = ((current_price - signal_price) / signal_price) * 100
        row = (symbol, period, _format_time(timestamp), signal_price, current_price, change)
        if period in grouped: grouped[period].append(row)
        else: unknown_period.append(row)

    if not any(grouped.values()) and not unknown_period: return []

    messages = []
    display_title = f"{meta['title']} - {meta['name']}"
    header = (f"{DIVIDER}\n<b>{meta['emoji']} HAFTALIK RAPOR — {display_title}</b>\n"
              f"<b>📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}</b>\n{DIVIDER}")
    messages.append(header)

    all_rows = []
    for period in PERIOD_ORDER:
        rows = grouped[period]
        if not rows: continue
        all_rows.extend(rows)
        rows.sort(key=lambda r: (1 if r[5] is None else 0, -(r[5] if r[5] is not None else 0)))
        msg = f"<b>⏱ {meta['emoji']} {display_title} — {PERIOD_NAMES[period]}</b>\n\n"
        msg += _format_signal_lines(rows) + "\n" + _stats_block(PERIOD_NAMES[period], rows)
        messages.append(msg)

    if unknown_period:
        unknown_period.sort(key=lambda r: (1 if r[5] is None else 0, -(r[5] if r[5] is not None else 0)))
        all_rows.extend(unknown_period)
        msg = f"<b>⏱ {meta['emoji']} {display_title} — DİĞER</b>\n\n"
        msg += _format_signal_lines(unknown_period) + "\n" + _stats_block("DİĞER", unknown_period)
        messages.append(msg)

    summary = f"{DIVIDER}\n<b>🎯 {meta['emoji']} {display_title} — GENEL TOPLAM</b>\n\n"
    summary += _stats_block("GENEL", all_rows)
    summary += "\n<b>📦 Zaman dilimi dağılımı:</b>\n"
    for period in PERIOD_ORDER:
        cnt = len(grouped[period])
        if cnt: summary += f"  • {PERIOD_NAMES[period]}: {cnt}\n"
    summary += DIVIDER
    messages.append(summary)
    return messages


async def generate_weekly_report(strategy_name: str | None = None) -> list:
    state_file = "state.json"
    if not os.path.exists(state_file): return ["<b>ℹ️ state.json bulunamadı.</b>"]
    with open(state_file, "r", encoding="utf-8") as f: state = json.load(f)
    scanner = MarketScanner()
    price_cache = {}
    targets = [strategy_name] if strategy_name in STRATEGY_META else list(STRATEGY_META.keys())
    all_messages = []
    for s in targets:
        msgs = await _build_strategy_messages(s, state, scanner, price_cache)
        all_messages.extend(msgs)
    return all_messages or ["<b>ℹ️ Bu hafta sinyal üretilmedi.</b>"]


def _send_chunked(sender, text):
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
    if buf.strip(): sender.send_message(buf)


if __name__ == "__main__":
    strategy = sys.argv[1] if len(sys.argv) > 1 else None
    async def main():
        messages = await generate_weekly_report(strategy)
        sender = get_telegram_sender()
        for i, msg in enumerate(messages):
            _send_chunked(sender, msg)
            if i < len(messages) - 1: time.sleep(1.2)
    asyncio.run(main())
