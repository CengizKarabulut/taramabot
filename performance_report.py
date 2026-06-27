"""Daily and weekly signal performance reports."""

import html
import importlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from performance_image import create_performance_report_image
from scanner import MarketScanner
from tvDatafeed import Interval


TZ_TURKEY = ZoneInfo("Europe/Istanbul")
REPORTS_DIR = Path("reports")
REPORT_IMAGE_MAX_ROWS = int(os.getenv("REPORT_IMAGE_MAX_ROWS", "24"))
REPORT_PRICE_BARS = int(os.getenv("REPORT_PRICE_BARS", "750"))

STRATEGY_META = {
    "macd_cross": {"state_key": "last_sent_macd_cross", "title": "M-1", "history_strategy": "macd_cross"},
    "h8": {"state_key": "last_sent_h8", "title": "S-M-1", "history_strategy": "h8"},
    "i9": {"state_key": "last_sent_i9", "title": "S-M-V-1", "history_strategy": "i9"},
    "ema": {"state_key": "last_sent_ema", "title": "E-V-1", "history_strategy": "ema"},
    "rsi_macd": {"state_key": "last_sent_rsi_macd", "title": "R-M-V-1", "history_strategy": "rsi_macd"},
    "new_scan": {"state_key": "last_sent_new_scan", "title": "A-M-V-1", "history_strategy": "new_scan"},
    "smi_macd_full": {
        "state_key": "last_sent_smi_macd", "title": "S-M-V-2",
        "history_strategy": "smi_macd", "is_full": True,
    },
    "smi_macd": {
        "state_key": "last_sent_smi_macd", "title": "S-M-2",
        "history_strategy": "smi_macd", "is_full": False,
    },
    "rsi": {"state_key": "last_sent_rsi", "title": "R-V-1", "history_strategy": "rsi"},
}


def report_labels() -> tuple[str, str]:
    try:
        main_module = importlib.import_module("main")
        brand_name = getattr(main_module, "BRAND_NAME", "StockMarketLab")
        bot_label = getattr(main_module, "TARAMA_LABEL", "Ana Bot")
        return str(brand_name), str(bot_label)
    except Exception:
        return "StockMarketLab", "Ana Bot"


def report_window(report_type: str, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(TZ_TURKEY)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TZ_TURKEY)
    else:
        current = current.astimezone(TZ_TURKEY)

    if report_type == "daily":
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    elif report_type == "weekly":
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        start = day_start - timedelta(days=current.weekday())
    else:
        raise ValueError(f"Bilinmeyen rapor tipi: {report_type}")
    return start, current


def parse_signal_time(value) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TZ_TURKEY)
    return parsed.astimezone(TZ_TURKEY)


def should_include_event(event: dict, meta: dict) -> bool:
    if str(event.get("strategy", "")) != meta["history_strategy"]:
        return False
    if "is_full" not in meta:
        return True
    return bool(event.get("is_full")) is meta["is_full"]


def history_events(state: dict, strategy_key: str, start: datetime, end: datetime) -> list[dict]:
    meta = STRATEGY_META[strategy_key]
    raw_history = state.get("signal_history")
    events = []

    if isinstance(raw_history, list) and raw_history:
        source = raw_history
    else:
        source = []
        for symbol_period, data in state.get(meta["state_key"], {}).items():
            if "_" not in symbol_period:
                continue
            symbol, period = symbol_period.rsplit("_", 1)
            entry = data if isinstance(data, dict) else {"time": data}
            source.append({
                "symbol": symbol,
                "period": period,
                "strategy": meta["history_strategy"],
                "bar_time": entry.get("time", ""),
                "detected_at": entry.get("detected_at") or entry.get("time", ""),
                "price": entry.get("price", 0),
                "is_full": entry.get("is_full"),
            })

    seen = set()
    for event in source:
        if not isinstance(event, dict) or not should_include_event(event, meta):
            continue
        detected_at = parse_signal_time(event.get("detected_at") or event.get("bar_time"))
        if detected_at is None or detected_at < start or detected_at > end:
            continue
        key = (
            event.get("symbol"), event.get("period"), event.get("strategy"),
            event.get("bar_time"), bool(event.get("is_full")),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized = dict(event)
        normalized["detected_at_dt"] = detected_at
        events.append(normalized)

    events.sort(key=lambda event: event["detected_at_dt"])
    return events


def normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    normalized = frame.copy()
    index = pd.to_datetime(normalized.index)
    if index.tz is None:
        index = index.tz_localize(TZ_TURKEY)
    else:
        index = index.tz_convert(TZ_TURKEY)
    normalized.index = index
    return normalized.sort_index()


def fetch_price_frame(symbol: str, scanner: MarketScanner) -> pd.DataFrame:
    try:
        frame = scanner.tv.get_hist(
            symbol,
            "BIST",
            interval=Interval.in_15_minute,
            n_bars=REPORT_PRICE_BARS,
        )
        return normalize_price_frame(frame)
    except Exception as exc:
        print(f"Fiyat gecmisi alinamadi ({symbol}): {exc}")
        return pd.DataFrame()


def calculate_row(event: dict, frame: pd.DataFrame, report_end: datetime) -> dict:
    detected_at = event["detected_at_dt"]
    try:
        entry_price = float(event.get("price", 0) or 0)
    except (TypeError, ValueError):
        entry_price = 0.0

    close_price = None
    peak_price = None
    peak_time = None
    if not frame.empty:
        post_signal = frame[(frame.index >= detected_at) & (frame.index <= report_end)]
        if not post_signal.empty:
            close_price = float(post_signal.iloc[-1]["close"])
            peak_index = post_signal["high"].astype(float).idxmax()
            peak_price = float(post_signal.loc[peak_index, "high"])
            peak_time = peak_index.to_pydatetime()

    if entry_price > 0:
        if peak_price is None or peak_price < entry_price:
            peak_price = entry_price
            peak_time = detected_at
        close_return = ((close_price - entry_price) / entry_price * 100) if close_price is not None else None
        peak_return = ((peak_price - entry_price) / entry_price * 100) if peak_price is not None else None
    else:
        close_return = None
        peak_return = None

    return {
        "symbol": str(event.get("symbol", "")),
        "period": str(event.get("period", "")),
        "time": detected_at.strftime("%d/%m %H:%M"),
        "entry_price": entry_price or None,
        "close_price": close_price,
        "peak_price": peak_price,
        "peak_time": peak_time.strftime("%d/%m %H:%M") if peak_time else "-",
        "close_return": close_return,
        "peak_return": peak_return,
    }


def summarize_rows(rows: list[dict]) -> dict:
    close_rows = [row for row in rows if row["close_return"] is not None]
    peak_rows = [row for row in rows if row["peak_return"] is not None]
    close_values = [row["close_return"] for row in close_rows]
    peak_values = [row["peak_return"] for row in peak_rows]
    return {
        "signal_count": len(rows),
        "measured_count": len(close_rows),
        "close_total": sum(close_values) if close_values else None,
        "close_average": sum(close_values) / len(close_values) if close_values else None,
        "peak_total": sum(peak_values) if peak_values else None,
        "peak_average": sum(peak_values) / len(peak_values) if peak_values else None,
        "close_wins": sum(value > 0 for value in close_values),
        "close_win_rate": (sum(value > 0 for value in close_values) / len(close_values) * 100) if close_values else None,
        "best_close": max(close_rows, key=lambda row: row["close_return"]) if close_rows else None,
        "best_peak": max(peak_rows, key=lambda row: row["peak_return"]) if peak_rows else None,
    }


def format_change(value) -> str:
    return "-" if value is None else f"{value:+.2f}%"


def stats_block(summary: dict) -> str:
    lines = [
        f"- Toplam sinyal: <b>{summary['signal_count']}</b>",
        f"- Olculebilir: <b>{summary['measured_count']}</b>",
        f"- Kapanis toplam: <b>{format_change(summary['close_total'])}</b>",
        f"- Zirve toplam: <b>{format_change(summary['peak_total'])}</b>",
        f"- Esit agirlikli kapanis: <b>{format_change(summary['close_average'])}</b>",
        f"- Esit agirlikli zirve: <b>{format_change(summary['peak_average'])}</b>",
    ]
    if summary["close_win_rate"] is not None:
        lines.append(
            f"- Kapanista artida: <b>{summary['close_wins']}/{summary['measured_count']} "
            f"(%{summary['close_win_rate']:.1f})</b>"
        )
    return "\n".join(lines)


def signal_lines(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        lines.append(
            f"- <b>{html.escape(row['symbol'])}</b> | {row['time']}\n"
            f"  <code>Giris {row['entry_price'] or 0:.2f} | "
            f"Kapanis {row['close_price'] or 0:.2f} ({format_change(row['close_return'])}) | "
            f"Zirve {row['peak_price'] or 0:.2f} ({format_change(row['peak_return'])})</code>"
        )
    return "\n".join(lines)


def balanced_chunks(items: list, max_size: int) -> list[list]:
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


def write_report_images(
    report_type: str,
    report_title: str,
    rows: list[dict],
    summary: dict,
    report_end: datetime,
) -> list[Path]:
    brand_name, bot_label = report_labels()
    chunks = balanced_chunks(rows, REPORT_IMAGE_MAX_ROWS)
    return [
        create_performance_report_image(
            brand_name=brand_name,
            tarama_label=bot_label,
            report_type=report_type,
            report_title=report_title,
            timestamp=report_end.strftime("%d.%m.%Y %H:%M"),
            rows=chunk,
            summary=summary,
            page=index,
            total_pages=len(chunks),
            total_rows=len(rows),
            output_dir=REPORTS_DIR,
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


async def build_strategy_report(
    report_type: str,
    strategy_key: str,
    state: dict,
    scanner: MarketScanner,
    frame_cache: dict[str, pd.DataFrame],
    start: datetime,
    end: datetime,
) -> tuple[list[str], list[Path]]:
    meta = STRATEGY_META[strategy_key]
    events = history_events(state, strategy_key, start, end)
    if not events:
        return [], []

    rows = []
    for event in events:
        symbol = str(event.get("symbol", ""))
        if symbol not in frame_cache:
            frame_cache[symbol] = fetch_price_frame(symbol, scanner)
        rows.append(calculate_row(event, frame_cache[symbol], end))

    rows.sort(key=lambda row: (row["close_return"] is None, -(row["close_return"] or 0)))
    summary = summarize_rows(rows)
    report_name = "GUNLUK" if report_type == "daily" else "HAFTALIK"
    title = meta["title"]
    messages = [
        f"<b>{html.escape(title)} {report_name} PERFORMANS</b>\n"
        f"<code>{start.strftime('%d.%m.%Y %H:%M')} - {end.strftime('%d.%m.%Y %H:%M')}</code>\n\n"
        f"{signal_lines(rows)}\n\n{stats_block(summary)}"
    ]
    return messages, write_report_images(report_type, title, rows, summary, end)


async def generate_performance_report_with_images(
    report_type: str,
    strategy_name: Optional[str] = None,
    now: Optional[datetime] = None,
) -> tuple[list[str], list[Path]]:
    if not os.path.exists("state.json"):
        return ["<b>state.json bulunamadi.</b>"], []
    with open("state.json", "r", encoding="utf-8-sig") as file:
        state = json.load(file)

    start, end = report_window(report_type, now)
    scanner = MarketScanner()
    frame_cache = {}
    targets = [strategy_name] if strategy_name in STRATEGY_META else list(STRATEGY_META)
    all_messages = []
    all_image_paths = []
    for strategy_key in targets:
        messages, image_paths = await build_strategy_report(
            report_type, strategy_key, state, scanner, frame_cache, start, end
        )
        all_messages.extend(messages)
        all_image_paths.extend(image_paths)

    if not all_messages:
        report_name = "bugun" if report_type == "daily" else "bu hafta"
        return [f"<b>{report_name} sinyal uretilmedi.</b>"], []
    return all_messages, all_image_paths
