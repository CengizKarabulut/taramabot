"""
Taramabot Haftalık Performans Raporu (Strateji + Zaman Dilimi Bazlı)
"""

import json
import os
import asyncio
import time
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram_sender import get_telegram_sender
from scanner import MarketScanner
from tvDatafeed import Interval


# --------------------------------------------------------------------------
# Sabitler
# --------------------------------------------------------------------------

STRATEGY_META = {
    "macd_cross": {
        "key": "last_sent_macd_cross", "emoji": "🟣", "title": "Tarama 1",
        "name": "MACD Pozitif Kesişim",
    },
    "h8": {
        "key": "last_sent_h8", "emoji": "🔷", "title": "Tarama 2",
        "name": "SMI/MACD Pozitif",
    },
    "i9": {
        "key": "last_sent_i9", "emoji": "🔹", "title": "Tarama 3",
        "name": "SMI/MACD Pozitif Full",
    },
    "ema": {
        "key": "last_sent_ema", "emoji": "🟠", "title": "Tarama 4",
        "name": "EMA Dizilimi",
    },
    "rsi_macd": {
        "key": "last_sent_rsi_macd", "emoji": "🟢", "title": "Tarama 5",
        "name": "RSI + MACD + Hacim",
    },
    "new_scan": {
        "key": "last_sent_new_scan", "emoji": "🟡", "title": "Tarama 6",
        "name": "SMA + MACD + Hacim",
    },
    "smi_macd_full": {
        "key": "last_sent_smi_macd", "emoji": "🚀", "title": "Tarama 7",
        "name": "SMI/MACD Full",
    },
    "smi_macd": {
        "key": "last_sent_smi_macd", "emoji": "⭐", "title": "Tarama 8",
        "name": "SMI/MACD",
    },
    "rsi": {
        "key": "last_sent_rsi", "emoji": "💎", "title": "Tarama 9",
        "name": "RSI",
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
REPORTS_DIR = Path("reports")
WEEKLY_IMAGE_MAX_ROWS = int(os.getenv("WEEKLY_IMAGE_MAX_ROWS", "30"))


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


def _safe_filename(value: str) -> str:
    import re
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value).strip())
    return cleaned.strip("-").lower() or "weekly-report"


def _balanced_chunks(items: list, max_size: int) -> list[list]:
    if not items:
        return [items]

    max_size = max(1, max_size)
    page_count = (len(items) + max_size - 1) // max_size
    base_size, extra = divmod(len(items), page_count)
    chunks = []
    start = 0
    for index in range(page_count):
        size = base_size + (1 if index < extra else 0)
        chunks.append(items[start:start + size])
        start += size
    return chunks


def _format_price(value: Optional[float]) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def _format_change(value: Optional[float]) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "-"


def _load_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    return plt, Rectangle


def _write_weekly_report_image(
    display_title: str,
    rows: list,
    page: int,
    total_pages: int,
    total_rows: int
) -> Path:
    plt, Rectangle = _load_matplotlib()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"weekly_{_safe_filename(display_title)}_{page:02d}_of_{total_pages:02d}.png"

    fig_height = min(18, max(8, 4.4 + len(rows) * 0.30))
    fig, ax = plt.subplots(figsize=(12, fig_height), dpi=150)
    fig.patch.set_facecolor("#f8fafc")
    ax.set_axis_off()

    ax.add_patch(Rectangle((0, 0.89), 1, 0.11, transform=ax.transAxes, color="#0f172a"))
    ax.text(0.055, 0.95, "HAFTALIK PERFORMANS RAPORU", transform=ax.transAxes,
            color="white", fontsize=22, fontweight="bold", va="center")
    subtitle = f"{display_title} | {total_rows} sinyal"
    if total_pages > 1:
        subtitle += f" | Sayfa {page}/{total_pages} ({len(rows)} sinyal)"
    ax.text(0.055, 0.908, subtitle, transform=ax.transAxes, color="#cbd5e1",
            fontsize=11.5, va="center")

    header_y = 0.80
    ax.add_patch(Rectangle((0.055, header_y), 0.89, 0.045, transform=ax.transAxes,
                           color="#e5e7eb", ec="#d1d5db", lw=1))
    for x, label in [
        (0.075, "Sembol"),
        (0.205, "Periyot"),
        (0.325, "Tarih"),
        (0.500, "Giris"),
        (0.635, "Son"),
        (0.770, "Getiri"),
    ]:
        ax.text(x, header_y + 0.023, label, transform=ax.transAxes,
                color="#111827", fontsize=9.5, fontweight="bold", va="center")

    if not rows:
        ax.add_patch(Rectangle((0.055, 0.32), 0.89, 0.20, transform=ax.transAxes,
                               color="white", ec="#d1d5db", lw=1.2))
        ax.text(0.50, 0.42, "Bu hafta sinyal yok.", transform=ax.transAxes,
                color="#334155", fontsize=17, fontweight="bold",
                ha="center", va="center")
    else:
        row_step = min(0.035, 0.70 / max(len(rows), 1))
        font_size = 9.2 if len(rows) <= 24 else 8.0
        y = header_y - row_step
        for index, row in enumerate(rows):
            symbol, period, t_str, signal_price, current_price, change = row
            bg = "#ffffff" if index % 2 == 0 else "#f1f5f9"
            ax.add_patch(Rectangle((0.055, y - row_step * 0.44), 0.89, row_step * 0.88,
                                   transform=ax.transAxes, color=bg, ec="#e5e7eb", lw=0.4))
            ax.text(0.075, y, str(symbol), transform=ax.transAxes, color="#111827",
                    fontsize=font_size, fontweight="bold", va="center")
            ax.text(0.205, y, str(period), transform=ax.transAxes, color="#334155",
                    fontsize=font_size, va="center")
            ax.text(0.325, y, str(t_str), transform=ax.transAxes, color="#334155",
                    fontsize=font_size, va="center")
            ax.text(0.500, y, _format_price(signal_price), transform=ax.transAxes,
                    color="#334155", fontsize=font_size, va="center")
            ax.text(0.635, y, _format_price(current_price), transform=ax.transAxes,
                    color="#334155", fontsize=font_size, va="center")
            if change is None:
                change_color = "#64748b"
            else:
                change_color = "#16a34a" if change >= 0 else "#dc2626"
            ax.text(0.770, y, _format_change(change), transform=ax.transAxes,
                    color=change_color, fontsize=font_size, fontweight="bold", va="center")
            y -= row_step

    fig.savefig(path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def _write_weekly_report_images(display_title: str, rows: list) -> list[Path]:
    chunks = _balanced_chunks(rows, WEEKLY_IMAGE_MAX_ROWS)
    return [
        _write_weekly_report_image(display_title, chunk, index, len(chunks), len(rows))
        for index, chunk in enumerate(chunks, start=1)
    ]


async def _build_strategy_messages(strategy_key: str, state: dict, scanner, price_cache: dict) -> tuple[list, list]:
    meta = STRATEGY_META[strategy_key]
    state_key = meta["key"]
    signals = state.get(state_key, {})
    if not signals: return [], []

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

    if not any(grouped.values()) and not unknown_period: return [], []

    messages = []
    display_title = meta["title"]
    header = (f"{DIVIDER}\n<b>{meta['emoji']} HAFTALIK RAPOR — {display_title}</b>\n"
              f"<b>📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}</b>\n{DIVIDER}")
    messages.append(header)

    all_rows = []
    for period in PERIOD_ORDER:
        rows = grouped[period]
        if not rows: continue
        rows.sort(key=lambda r: (1 if r[5] is None else 0, -(r[5] if r[5] is not None else 0)))
        all_rows.extend(rows)
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
    image_paths = _write_weekly_report_images(display_title, all_rows)
    return messages, image_paths


async def generate_weekly_report(strategy_name: str | None = None) -> list:
    messages, _ = await generate_weekly_report_with_images(strategy_name)
    return messages


async def generate_weekly_report_with_images(strategy_name: str | None = None) -> tuple[list, list]:
    state_file = "state.json"
    if not os.path.exists(state_file): return ["<b>ℹ️ state.json bulunamadı.</b>"], []
    with open(state_file, "r", encoding="utf-8") as f: state = json.load(f)
    scanner = MarketScanner()
    price_cache = {}
    targets = [strategy_name] if strategy_name in STRATEGY_META else list(STRATEGY_META.keys())
    all_messages = []
    all_image_paths = []
    for s in targets:
        msgs, image_paths = await _build_strategy_messages(s, state, scanner, price_cache)
        all_messages.extend(msgs)
        all_image_paths.extend(image_paths)
    return all_messages or ["<b>ℹ️ Bu hafta sinyal üretilmedi.</b>"], all_image_paths


if __name__ == "__main__":
    strategy = sys.argv[1] if len(sys.argv) > 1 else None
    async def main():
        _, image_paths = await generate_weekly_report_with_images(strategy)
        sender = get_telegram_sender()
        for i, image_path in enumerate(image_paths):
            caption = "<b>Haftalık Performans Raporu</b>"
            if len(image_paths) > 1:
                caption += f" — Görsel {i + 1}/{len(image_paths)}"
            sender.send_photo(str(image_path), caption=caption)
            time.sleep(1)
    asyncio.run(main())
