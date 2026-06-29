"""PNG renderer for daily and weekly performance reports."""

import re
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


CANVAS_WIDTH = 1500
PADDING_X = 58
TOP_HEIGHT = 205
TABLE_HEADER_HEIGHT = 52
ROW_HEIGHT = 44
SUMMARY_HEIGHT = 138
BOTTOM_PADDING = 38

COLOR_BG = "#F4F7FB"
COLOR_CARD = "#FFFFFF"
COLOR_BRAND = "#0F2742"
COLOR_MUTED = "#667085"
COLOR_LINE = "#D8E0EA"
COLOR_HEADER = "#EAF1FF"
COLOR_ROW_ALT = "#F8FAFC"
COLOR_GREEN = "#137333"
COLOR_RED = "#B42318"
COLOR_TEXT = "#111827"
COLOR_BLUE = "#1D4ED8"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "Arial Bold.ttf" if bold else "Arial.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT_BRAND = _font(42, True)
FONT_TITLE = _font(30, True)
FONT_META = _font(21)
FONT_META_BOLD = _font(21, True)
FONT_HEAD = _font(20, True)
FONT_ROW = _font(20)
FONT_ROW_BOLD = _font(20, True)
FONT_SUMMARY = _font(21, True)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-").lower() or "report"


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _right_text(draw, x: int, y: int, text: str, font, fill: str) -> None:
    draw.text((x - _text_width(draw, text, font), y), text, font=font, fill=fill)


def _format_price(value) -> str:
    try:
        return f"{float(value):,.2f}".replace(",", " ")
    except (TypeError, ValueError):
        return "-"


def _format_change(value) -> tuple[str, str]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-", COLOR_MUTED
    return f"{number:+.2f}%", COLOR_GREEN if number >= 0 else COLOR_RED


def create_performance_report_image(
    brand_name: str,
    tarama_label: str,
    report_type: str,
    report_title: str,
    timestamp: str,
    rows: Iterable[dict],
    summary: dict,
    page: int,
    total_pages: int,
    total_rows: int,
    output_dir: Path | str,
) -> Path:
    table_rows = list(rows)
    visible_rows = max(len(table_rows), 1)
    height = (
        TOP_HEIGHT + TABLE_HEADER_HEIGHT + (visible_rows * ROW_HEIGHT)
        + SUMMARY_HEIGHT + BOTTOM_PADDING
    )
    image = Image.new("RGB", (CANVAS_WIDTH, height), COLOR_BG)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((28, 24, CANVAS_WIDTH - 28, height - 24), radius=28, fill=COLOR_CARD)
    draw.text((PADDING_X, 46), brand_name, font=FONT_BRAND, fill=COLOR_BRAND)
    report_name = "Gunluk" if report_type == "daily" else "Haftalik"
    draw.text(
        (PADDING_X, 104),
        f"{tarama_label} - {report_name} Performans",
        font=FONT_TITLE,
        fill=COLOR_BLUE,
    )

    badge = f"{total_rows} satir | {summary.get('event_count', total_rows)} gelis"
    badge_width = _text_width(draw, badge, FONT_META_BOLD) + 42
    draw.rounded_rectangle(
        (CANVAS_WIDTH - PADDING_X - badge_width, 50, CANVAS_WIDTH - PADDING_X, 92),
        radius=20,
        fill=COLOR_HEADER,
    )
    draw.text(
        (CANVAS_WIDTH - PADDING_X - badge_width + 21, 59),
        badge,
        font=FONT_META_BOLD,
        fill=COLOR_BLUE,
    )
    draw.text((PADDING_X, 153), f"{report_title}   |   {timestamp}", font=FONT_META, fill=COLOR_MUTED)
    _right_text(
        draw, CANVAS_WIDTH - PADDING_X, 153,
        f"Liste {page}/{total_pages}", FONT_META_BOLD, COLOR_MUTED,
    )

    header_y = TOP_HEIGHT
    draw.rounded_rectangle(
        (PADDING_X, header_y, CANVAS_WIDTH - PADDING_X, header_y + TABLE_HEADER_HEIGHT),
        radius=12,
        fill=COLOR_HEADER,
    )
    columns = {
        "symbol": 76,
        "time": 245,
        "entry": 590,
        "close": 790,
        "peak": 990,
        "close_return": 1230,
        "peak_return": 1440,
    }
    draw.text((columns["symbol"], header_y + 14), "Kod/Periyot", font=FONT_HEAD, fill=COLOR_BRAND)
    draw.text((columns["time"], header_y + 14), "Ilk Sinyal", font=FONT_HEAD, fill=COLOR_BRAND)
    _right_text(draw, columns["entry"], header_y + 14, "Giris", FONT_HEAD, COLOR_BRAND)
    _right_text(draw, columns["close"], header_y + 14, "Kapanis", FONT_HEAD, COLOR_BRAND)
    _right_text(draw, columns["peak"], header_y + 14, "Zirve", FONT_HEAD, COLOR_BRAND)
    _right_text(draw, columns["close_return"], header_y + 14, "Kapanis %", FONT_HEAD, COLOR_BRAND)
    _right_text(draw, columns["peak_return"], header_y + 14, "Zirve %", FONT_HEAD, COLOR_BRAND)

    row_y = header_y + TABLE_HEADER_HEIGHT
    if not table_rows:
        draw.rectangle((PADDING_X, row_y, CANVAS_WIDTH - PADDING_X, row_y + ROW_HEIGHT), fill=COLOR_ROW_ALT)
        draw.text((PADDING_X + 20, row_y + 11), "Bu donemde sinyal yok", font=FONT_ROW_BOLD, fill=COLOR_MUTED)
        row_y += ROW_HEIGHT
    else:
        for index, row in enumerate(table_rows):
            if index % 2:
                draw.rectangle((PADDING_X, row_y, CANVAS_WIDTH - PADDING_X, row_y + ROW_HEIGHT), fill=COLOR_ROW_ALT)
            close_return, close_color = _format_change(row.get("close_return"))
            peak_return, peak_color = _format_change(row.get("peak_return"))
            code = f"{row.get('symbol', '')}/{row.get('period', '')}"[:14]
            signal_time = str(row.get("time", ""))[:16]
            if row.get("occurrences", 1) > 1:
                signal_time += f" x{row['occurrences']}"
            draw.text((columns["symbol"], row_y + 11), code, font=FONT_ROW_BOLD, fill=COLOR_TEXT)
            draw.text((columns["time"], row_y + 11), signal_time, font=FONT_ROW, fill=COLOR_TEXT)
            _right_text(draw, columns["entry"], row_y + 11, _format_price(row.get("entry_price")), FONT_ROW, COLOR_TEXT)
            _right_text(draw, columns["close"], row_y + 11, _format_price(row.get("close_price")), FONT_ROW, COLOR_TEXT)
            _right_text(draw, columns["peak"], row_y + 11, _format_price(row.get("peak_price")), FONT_ROW, COLOR_TEXT)
            _right_text(draw, columns["close_return"], row_y + 11, close_return, FONT_ROW_BOLD, close_color)
            _right_text(draw, columns["peak_return"], row_y + 11, peak_return, FONT_ROW_BOLD, peak_color)
            draw.line((PADDING_X, row_y + ROW_HEIGHT, CANVAS_WIDTH - PADDING_X, row_y + ROW_HEIGHT), fill=COLOR_LINE)
            row_y += ROW_HEIGHT

    summary_y = row_y + 18
    draw.rounded_rectangle(
        (PADDING_X, summary_y, CANVAS_WIDTH - PADDING_X, summary_y + 104),
        radius=10,
        fill=COLOR_HEADER,
    )
    close_total, close_total_color = _format_change(summary.get("close_total"))
    peak_total, peak_total_color = _format_change(summary.get("peak_total"))
    close_average, close_average_color = _format_change(summary.get("close_average"))
    peak_average, peak_average_color = _format_change(summary.get("peak_average"))

    draw.text((PADDING_X + 22, summary_y + 17), "GENEL TOPLAM", font=FONT_SUMMARY, fill=COLOR_BRAND)
    draw.text((PADDING_X + 245, summary_y + 17), "Kapanis", font=FONT_META, fill=COLOR_MUTED)
    draw.text((PADDING_X + 340, summary_y + 17), close_total, font=FONT_SUMMARY, fill=close_total_color)
    draw.text((PADDING_X + 540, summary_y + 17), "Zirve", font=FONT_META, fill=COLOR_MUTED)
    draw.text((PADDING_X + 610, summary_y + 17), peak_total, font=FONT_SUMMARY, fill=peak_total_color)

    draw.text((PADDING_X + 22, summary_y + 61), "ESIT AGIRLIKLI", font=FONT_SUMMARY, fill=COLOR_BRAND)
    draw.text((PADDING_X + 245, summary_y + 61), "Kapanis", font=FONT_META, fill=COLOR_MUTED)
    draw.text((PADDING_X + 340, summary_y + 61), close_average, font=FONT_SUMMARY, fill=close_average_color)
    draw.text((PADDING_X + 540, summary_y + 61), "Zirve", font=FONT_META, fill=COLOR_MUTED)
    draw.text((PADDING_X + 610, summary_y + 61), peak_average, font=FONT_SUMMARY, fill=peak_average_color)
    measured = f"Olculebilir {summary.get('measured_count', 0)}/{summary.get('signal_count', 0)}"
    _right_text(draw, CANVAS_WIDTH - PADDING_X - 22, summary_y + 39, measured, FONT_META_BOLD, COLOR_MUTED)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    filename = "_".join([
        _safe_filename(tarama_label),
        _safe_filename(report_name),
        _safe_filename(report_title),
        f"p{page}",
    ]) + ".png"
    final_path = output_path / filename
    image.save(final_path, "PNG", optimize=True)
    return final_path
