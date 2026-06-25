"""
Taramabot Telegram Gönderici Modülü
Telegram API üzerinden mesaj gönderir.
HTML formatında şık ve profesyonel mesajlar oluşturur.
"""

import logging
import os
import re
import requests
import textwrap
import time
from datetime import datetime
from typing import Optional, List, Dict
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID,
    EMOJI_FULL_SIGNAL, EMOJI_SMI_SIGNAL, EMOJI_RSI_SIGNAL, 
    EMOJI_NEW_SCAN_SIGNAL, EMOJI_RSI_MACD_SIGNAL, EMOJI_EMA_SIGNAL,
    SCREENSHOTS_DIR
)

logger = logging.getLogger(__name__)

SCAN_RESULT_IMAGE_MAX_ROWS = 30
COMMON_SIGNAL_IMAGE_MAX_ROWS = 24
PERIOD_ORDER = ["15m", "30m", "45m", "1H", "2H", "4H", "1D", "1W", "1M"]


class TelegramSender:
    """Telegram mesaj gönderici."""
    
    def __init__(self, bot_token: str, chat_id: str, thread_id: Optional[str] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.thread_id = self._normalize_thread_id(thread_id)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    @staticmethod
    def _normalize_thread_id(thread_id: Optional[str]) -> Optional[str]:
        if thread_id is None:
            return None

        value = str(thread_id).strip()
        if not value:
            return None

        # Telegram topic links usually look like https://t.me/c/<chat>/<topic_id>
        match = re.search(r"/c/\d+/(\d+)", value)
        if match:
            return match.group(1)

        # Telegram Web links usually look like https://web.telegram.org/a/#-100..._<topic_id>
        match = re.search(r"#-?\d+_(\d+)", value)
        if match:
            return match.group(1)

        match = re.fullmatch(r"\d+", value)
        if match:
            return match.group(0)

        logger.warning("TG_THREAD_ID sayisal degil veya Telegram konu linki degil: %s", value)
        return None
    
    def send_message(self, text: str, parse_mode: str = "HTML", retry_count: int = 3) -> bool:
        """
        Telegram'a mesaj gönder. 429 hatalarında otomatik bekler ve tekrar dener.
        """
        if not self.bot_token or not self.chat_id:
            logger.warning(f"Telegram konfigürasyonu eksik. Mesaj: {text[:50]}...")
            return False
        
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        
        if self.thread_id:
            payload["message_thread_id"] = self.thread_id
            
        for attempt in range(retry_count):
            try:
                response = requests.post(url, json=payload, timeout=15)
                
                if response.status_code == 200:
                    logger.info(f"Telegram mesajı gönderildi: {text[:50]}...")
                    return True
                
                if response.status_code == 429:
                    retry_after = response.json().get("parameters", {}).get("retry_after", 30)
                    logger.warning(f"Telegram 429 (Too Many Requests). {retry_after} saniye bekleniyor... (Deneme {attempt+1}/{retry_count})")
                    time.sleep(retry_after + 1)
                    continue
                
                if response.status_code != 200:
                    logger.error(f"Telegram API Hatası ({response.status_code}): {response.text}")
                    # Eğer HTML hatası ise düz metin olarak tekrar dene
                    if "can't parse entities" in response.text and parse_mode == "HTML":
                        logger.info("HTML ayrıştırma hatası, düz metin olarak tekrar deneniyor...")
                        payload["text"] = re.sub('<[^<]+?>', '', text)
                        payload["parse_mode"] = None
                        # Recursion with plain text
                        return self.send_message(payload["text"], parse_mode=None)
                
                response.raise_for_status()
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Telegram mesaj gönderme hatası (Deneme {attempt+1}): {str(e)}")
                if attempt < retry_count - 1:
                    time.sleep(2)
                else:
                    return False
        
        return False

    def send_photo(self, photo_path: str, caption: Optional[str] = None, parse_mode: str = "HTML", retry_count: int = 3) -> bool:
        """
        Telegram'a yerel bir gorsel dosyasi gonder. 429 hatalarinda otomatik bekler.
        """
        if not self.bot_token or not self.chat_id:
            logger.warning(f"Telegram konfigürasyonu eksik. Görsel gönderilemedi: {photo_path}")
            return False

        if not photo_path or not os.path.exists(photo_path):
            logger.warning(f"Görsel dosyası bulunamadı: {photo_path}")
            return False

        url = f"{self.base_url}/sendPhoto"
        payload = {"chat_id": self.chat_id}
        if caption:
            payload["caption"] = caption[:1024]
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if self.thread_id:
            payload["message_thread_id"] = self.thread_id

        for attempt in range(retry_count):
            try:
                with open(photo_path, "rb") as photo:
                    response = requests.post(
                        url,
                        data=payload,
                        files={"photo": photo},
                        timeout=30
                    )

                if response.status_code == 200:
                    logger.info(f"Telegram görseli gönderildi: {photo_path}")
                    return True

                if response.status_code == 429:
                    retry_after = response.json().get("parameters", {}).get("retry_after", 30)
                    logger.warning(f"Telegram 429 (Too Many Requests). {retry_after} saniye bekleniyor... (Deneme {attempt+1}/{retry_count})")
                    time.sleep(retry_after + 1)
                    continue

                if response.status_code != 200:
                    logger.error(f"Telegram görsel API hatası ({response.status_code}): {response.text}")
                    if "can't parse entities" in response.text and parse_mode == "HTML" and caption:
                        plain_caption = re.sub('<[^<]+?>', '', caption)
                        return self.send_photo(photo_path, caption=plain_caption, parse_mode=None)

                response.raise_for_status()

            except requests.exceptions.RequestException as e:
                logger.error(f"Telegram görsel gönderme hatası (Deneme {attempt+1}): {str(e)}")
                if attempt < retry_count - 1:
                    time.sleep(2)
                else:
                    return False

        return False

    def _period_name(self, period: str) -> str:
        period_names = {
            "15m": "15 DAKIKA", "15M": "15 DAKIKA",
            "30m": "30 DAKIKA", "30M": "30 DAKIKA",
            "45m": "45 DAKIKA", "45M": "45 DAKIKA",
            "1h": "1 SAAT", "1H": "1 SAAT",
            "2h": "2 SAAT", "2H": "2 SAAT",
            "4h": "4 SAAT", "4H": "4 SAAT",
            "1d": "GUNLUK", "1D": "GUNLUK",
            "1w": "HAFTALIK", "1W": "HAFTALIK",
            "1m": "AYLIK", "1M": "AYLIK"
        }
        return period_names.get(period, period)

    def _strategy_groups(
        self,
        full_signals: List[dict],
        smi_signals: List[dict],
        rsi_signals: List[dict],
        new_scan_signals: List[dict],
        rsi_macd_signals: List[dict],
        ema_signals: List[dict],
        macd_cross_signals: List[dict],
        h8_signals: List[dict],
        i9_signals: List[dict],
        enabled_strategy_codes: Optional[List[str]] = None
    ) -> List[dict]:
        enabled = {str(code).lower() for code in (enabled_strategy_codes or [])}
        groups = [
            {
                "key": "macd_cross", "signals": macd_cross_signals or [], "emoji": "🟣", "code": "M-1",
                "name": "MACD Pozitif Kesisim",
                "aliases": [],
                "description": "RSI 50 ustu, MACD sinyal cizgisini yukari keser ve MACD pozitif bolgede kalir.",
                "color": "#7c3aed",
            },
            {
                "key": "h8", "signals": h8_signals or [], "emoji": "🔷", "code": "S-M-1",
                "name": "SMI/MACD Momentum",
                "aliases": [],
                "description": "SMI yukari kesisim ve MACD histogram pozitif momentum kosullarini arar.",
                "color": "#2563eb",
            },
            {
                "key": "i9", "signals": i9_signals or [], "emoji": "🔹", "code": "S-M-V-1",
                "name": "SMI/MACD Guclu Onay",
                "aliases": [],
                "description": "S-M-1 kosullarina MA200 ustu fiyat ve guclu hacim onayi ekler.",
                "color": "#0891b2",
            },
            {
                "key": "ema", "signals": ema_signals or [], "emoji": EMOJI_EMA_SIGNAL, "code": "E-V-1",
                "name": "EMA Trend + Hacim",
                "aliases": [],
                "description": "Kisa EMA yapisi uzun EMA yapisinin ustundedir ve hacim trendi destekler.",
                "color": "#f97316",
            },
            {
                "key": "rsi_macd", "signals": rsi_macd_signals or [], "emoji": EMOJI_RSI_MACD_SIGNAL, "code": "R-M-V-1",
                "name": "RSI + MACD + Hacim",
                "aliases": [],
                "description": "RSI guclenirken MACD yukari kesisim ve hacim artisi birlikte olusur.",
                "color": "#16a34a",
            },
            {
                "key": "new_scan", "signals": new_scan_signals or [], "emoji": EMOJI_NEW_SCAN_SIGNAL, "code": "A-M-V-1",
                "name": "SMA + MACD + Hacim",
                "aliases": [],
                "description": "SMA 5/8/21 dizilimi, MACD pozitifligi, RSI araligi ve hacim onayini birlestirir.",
                "color": "#ca8a04",
            },
            {
                "key": "full", "signals": full_signals or [], "emoji": EMOJI_FULL_SIGNAL, "code": "S-M-V-2",
                "name": "SMI/MACD Full",
                "aliases": [],
                "description": "SMI/MACD al sinyaline MA200 ustu fiyat ve hacim filtresi ekler.",
                "color": "#dc2626",
            },
            {
                "key": "smi", "signals": smi_signals or [], "emoji": EMOJI_SMI_SIGNAL, "code": "S-M-2",
                "name": "SMI/MACD Erken",
                "aliases": [],
                "description": "SMI ve MACD momentum kesisimlerini temel alan erken sinyaldir.",
                "color": "#9333ea",
            },
            {
                "key": "rsi", "signals": rsi_signals or [], "emoji": EMOJI_RSI_SIGNAL, "code": "R-V-1",
                "name": "RSI Momentum",
                "aliases": [],
                "description": "RSI guc bolgesine gecisi ve yukselis momentumunu izler.",
                "color": "#0ea5e9",
            },
        ]

        for group in groups:
            aliases = {group["key"].lower(), group["code"].lower()}
            aliases.update(str(alias).lower() for alias in group.get("aliases", []))
            group["enabled"] = (
                not enabled
                or bool(enabled.intersection(aliases))
            )
        return groups

    def _format_strategy_legend(self, strategies: List[dict]) -> str:
        enabled = [s for s in strategies if s.get("enabled", True)]
        if not enabled:
            enabled = strategies

        lines = ["<b>Sinyal kodlari:</b>"]
        for strategy in enabled:
            lines.append(f"- <b>{strategy['code']}</b>")
        return "\n".join(lines)

    def _format_bar_time(self, value: Optional[str]) -> str:
        if not value:
            return ""
        try:
            return datetime.fromisoformat(str(value)).strftime("%d/%m %H:%M")
        except Exception:
            return str(value)

    def _format_signal_list(self, signals: List[dict]) -> str:
        """Sinyal listesini formatla."""
        lines = []
        for s in signals:
            change = float(s.get("change", 0) or 0)
            close = float(s.get("close", 0) or 0)
            c_str = f"🟢 +{change:.2f}%" if change >= 0 else f"🔴 {change:.2f}%"
            bar_time = self._format_bar_time(s.get("bar_time"))
            bar_part = f" | Bar: {bar_time}" if bar_time else ""
            lines.append(f"• <code>{s['symbol']:<7}</code> | {c_str} | Kapanış: {close:.2f}{bar_part}")
        return "\n".join(lines)

    def _safe_filename_part(self, value: Optional[str]) -> str:
        value = str(value or "scan").strip().lower()
        return re.sub(r"[^a-z0-9_-]+", "_", value)

    def _load_matplotlib(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
        return plt, Rectangle

    def _write_scan_info_image(
        self,
        period: str,
        strategies: List[dict],
        market_type: Optional[str],
        total_scanned: Optional[int]
    ) -> Optional[str]:
        plt, Rectangle = self._load_matplotlib()
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(
            SCREENSHOTS_DIR,
            f"scan_info_{self._safe_filename_part(market_type)}_{self._safe_filename_part(period)}_{stamp}.png"
        )

        enabled = [s for s in strategies if s.get("enabled", True)] or strategies
        fig, ax = plt.subplots(figsize=(12, 8), dpi=150)
        fig.patch.set_facecolor("#f8fafc")
        ax.set_axis_off()

        ax.add_patch(Rectangle((0, 0.87), 1, 0.13, transform=ax.transAxes, color="#111827"))
        ax.text(0.055, 0.94, "SINYAL BILGISI", transform=ax.transAxes, color="white",
                fontsize=23, fontweight="bold", va="center")
        ax.text(0.055, 0.895, datetime.now().strftime("%d.%m.%Y %H:%M"),
                transform=ax.transAxes, color="#cbd5e1", fontsize=12, va="center")

        cards = [
            ("Pazar", (market_type or "BIST").upper()),
            ("Periyot", self._period_name(period)),
            ("Sembol", str(total_scanned) if total_scanned is not None else "-"),
            ("Kod", str(len(enabled))),
        ]
        for idx, (label, value) in enumerate(cards):
            x = 0.055 + idx * 0.235
            ax.add_patch(Rectangle((x, 0.76), 0.19, 0.075, transform=ax.transAxes,
                                   color="white", ec="#d1d5db", lw=1.2))
            ax.text(x + 0.015, 0.81, label, transform=ax.transAxes, color="#6b7280",
                    fontsize=10, va="center")
            ax.text(x + 0.015, 0.775, value, transform=ax.transAxes, color="#111827",
                    fontsize=16, fontweight="bold", va="center")

        ax.text(0.055, 0.70, "Sinyal Kodlari", transform=ax.transAxes, color="#111827",
                fontsize=18, fontweight="bold", va="center")

        y = 0.64
        for strategy in enabled:
            color = strategy["color"]
            ax.add_patch(Rectangle((0.06, y - 0.02), 0.075, 0.044, transform=ax.transAxes,
                                   color=color, ec=color))
            ax.text(0.0975, y + 0.002, strategy["code"], transform=ax.transAxes,
                    color="white", fontsize=12, fontweight="bold", ha="center", va="center")
            ax.text(0.155, y + 0.002, strategy["code"], transform=ax.transAxes,
                    color="#111827", fontsize=12.5, fontweight="bold", va="center")
            y -= 0.067

        ax.text(0.055, 0.045, "Bu gorsel periyodu ve kullanilan sinyal kodlarinin anlamini gosterir.",
                transform=ax.transAxes, color="#64748b", fontsize=9.5, va="center")
        fig.savefig(path, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return path

    def _enabled_strategies(self, strategies: List[dict]) -> List[dict]:
        return [s for s in strategies if s.get("enabled", True)] or strategies

    def _balanced_chunks(self, items: List[dict], max_items_per_chunk: int) -> List[List[dict]]:
        if not items:
            return [items]

        max_items_per_chunk = max(1, max_items_per_chunk)
        page_count = (len(items) + max_items_per_chunk - 1) // max_items_per_chunk
        base_size, extra = divmod(len(items), page_count)
        chunks = []
        start = 0
        for idx in range(page_count):
            size = base_size + (1 if idx < extra else 0)
            chunks.append(items[start:start + size])
            start += size
        return chunks

    def _scan_result_rows(self, strategies: List[dict]) -> List[dict]:
        rows = []
        for strategy_index, strategy in enumerate(self._enabled_strategies(strategies)):
            for signal in strategy["signals"]:
                rows.append({
                    "order": strategy_index,
                    "code": strategy["code"],
                    "name": strategy["name"],
                    "color": strategy["color"],
                    "symbol": signal.get("symbol", "-"),
                    "change": float(signal.get("change", 0) or 0),
                    "close": float(signal.get("close", 0) or 0),
                })
        rows.sort(key=lambda row: (row["order"], row["symbol"]))
        return rows

    def _write_scan_result_image(
        self,
        period: str,
        strategies: List[dict],
        market_type: Optional[str],
        total_scanned: Optional[int],
        rows: Optional[List[dict]] = None,
        page_number: int = 1,
        page_count: int = 1,
        total_signal_count: Optional[int] = None,
        strategy_label: Optional[str] = None
    ) -> Optional[str]:
        plt, Rectangle = self._load_matplotlib()
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        page_suffix = f"_{page_number:02d}_of_{page_count:02d}" if page_count > 1 else ""
        strategy_part = f"_{self._safe_filename_part(strategy_label)}" if strategy_label else ""
        path = os.path.join(
            SCREENSHOTS_DIR,
            f"scan_result_{self._safe_filename_part(market_type)}_{self._safe_filename_part(period)}{strategy_part}_{stamp}{page_suffix}.png"
        )

        enabled = self._enabled_strategies(strategies)
        visible_rows = rows if rows is not None else self._scan_result_rows(strategies)
        total_signal_count = total_signal_count if total_signal_count is not None else len(visible_rows)

        fig_height = min(18, max(8, 4.8 + len(visible_rows) * 0.22))
        fig, ax = plt.subplots(figsize=(12, fig_height), dpi=150)
        fig.patch.set_facecolor("#f8fafc")
        ax.set_axis_off()

        ax.add_patch(Rectangle((0, 0.89), 1, 0.11, transform=ax.transAxes, color="#0f172a"))
        ax.text(0.055, 0.95, "SINYAL SONUCU", transform=ax.transAxes, color="white",
                fontsize=23, fontweight="bold", va="center")
        subtitle = f"{(market_type or 'BIST').upper()} | {self._period_name(period)}"
        if strategy_label:
            subtitle += f" | {strategy_label}"
        subtitle += f" | {total_signal_count} sinyal"
        if total_scanned is not None:
            subtitle += f" | {total_scanned} sembol tarandı"
        if page_count > 1:
            subtitle += f" | Sayfa {page_number}/{page_count} ({len(visible_rows)} sinyal)"
        ax.text(0.055, 0.908, subtitle, transform=ax.transAxes, color="#cbd5e1",
                fontsize=12, va="center")

        summary_x = 0.055
        summary_y = 0.81
        for idx, strategy in enumerate(enabled):
            if idx and idx % 5 == 0:
                summary_x = 0.055
                summary_y -= 0.07
            count = len(strategy["signals"])
            ax.add_patch(Rectangle((summary_x, summary_y), 0.16, 0.045, transform=ax.transAxes,
                                   color="white", ec="#d1d5db", lw=1))
            ax.add_patch(Rectangle((summary_x, summary_y), 0.035, 0.045, transform=ax.transAxes,
                                   color=strategy["color"]))
            ax.text(summary_x + 0.0175, summary_y + 0.023, strategy["code"],
                    transform=ax.transAxes, color="white", fontsize=8.5,
                    fontweight="bold", ha="center", va="center")
            ax.text(summary_x + 0.047, summary_y + 0.023, f"{count} sinyal",
                    transform=ax.transAxes, color="#111827", fontsize=9.5,
                    fontweight="bold", va="center")
            summary_x += 0.18

        table_top = summary_y - 0.07
        if not visible_rows:
            ax.add_patch(Rectangle((0.055, 0.24), 0.89, 0.22, transform=ax.transAxes,
                                   color="white", ec="#d1d5db", lw=1.2))
            ax.text(0.50, 0.35, "Bu periyotta yeni sinyal yok.",
                    transform=ax.transAxes, color="#334155", fontsize=18,
                    fontweight="bold", ha="center", va="center")
        else:
            ax.add_patch(Rectangle((0.055, table_top), 0.89, 0.04, transform=ax.transAxes,
                                   color="#e5e7eb", ec="#d1d5db", lw=1))
            ax.text(0.075, table_top + 0.022, "Kod", transform=ax.transAxes,
                    color="#111827", fontsize=10, fontweight="bold", va="center")
            ax.text(0.16, table_top + 0.022, "Sembol", transform=ax.transAxes,
                    color="#111827", fontsize=10, fontweight="bold", va="center")
            ax.text(0.34, table_top + 0.022, "Değişim", transform=ax.transAxes,
                    color="#111827", fontsize=10, fontweight="bold", va="center")
            ax.text(0.49, table_top + 0.022, "Kapanış", transform=ax.transAxes,
                    color="#111827", fontsize=10, fontweight="bold", va="center")
            row_step = min(0.034, 0.64 / max(len(visible_rows), 1))
            font_size = 9.5 if len(visible_rows) <= 40 else 8
            y = table_top - row_step
            for idx, row in enumerate(visible_rows):
                bg = "#ffffff" if idx % 2 == 0 else "#f1f5f9"
                ax.add_patch(Rectangle((0.055, y - row_step * 0.44), 0.89, row_step * 0.88,
                                       transform=ax.transAxes, color=bg, ec="#e5e7eb", lw=0.4))
                ax.add_patch(Rectangle((0.065, y - row_step * 0.30), 0.045, row_step * 0.60,
                                       transform=ax.transAxes, color=row["color"]))
                ax.text(0.0875, y, row["code"], transform=ax.transAxes, color="white",
                        fontsize=font_size, fontweight="bold", ha="center", va="center")
                ax.text(0.16, y, row["symbol"], transform=ax.transAxes, color="#111827",
                        fontsize=font_size, fontweight="bold", va="center")
                change_color = "#16a34a" if row["change"] >= 0 else "#dc2626"
                ax.text(0.34, y, f"{row['change']:+.2f}%", transform=ax.transAxes,
                        color=change_color, fontsize=font_size, fontweight="bold", va="center")
                ax.text(0.49, y, f"{row['close']:.2f}", transform=ax.transAxes,
                        color="#334155", fontsize=font_size, va="center")
                y -= row_step

        fig.savefig(path, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return path

    def _common_signal_rows(self, signal_map: Dict[str, dict]) -> List[dict]:
        rows = []
        for symbol in sorted(signal_map.keys()):
            periods = signal_map[symbol]
            details = []
            total_signals = 0
            ordered_periods = PERIOD_ORDER + sorted(p for p in periods if p not in PERIOD_ORDER)
            for period in ordered_periods:
                if period not in periods:
                    continue
                strategies = list(dict.fromkeys(periods[period]))
                total_signals += len(strategies)
                details.append(f"{period}: {', '.join(strategies)}")
            rows.append({
                "symbol": symbol,
                "count": total_signals,
                "details": " | ".join(details),
            })
        rows.sort(key=lambda row: (-row["count"], row["symbol"]))
        return rows

    def _write_common_signal_image(
        self,
        signal_rows: List[dict],
        page_number: int,
        page_count: int,
        total_symbol_count: int
    ) -> Optional[str]:
        plt, Rectangle = self._load_matplotlib()
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        page_suffix = f"_{page_number:02d}_of_{page_count:02d}" if page_count > 1 else ""
        path = os.path.join(SCREENSHOTS_DIR, f"common_signals_{stamp}{page_suffix}.png")

        wrapped_rows = []
        total_line_count = 0
        for row in signal_rows:
            detail_lines = textwrap.wrap(row["details"], width=92) or [""]
            wrapped_rows.append((row, detail_lines))
            total_line_count += max(1, len(detail_lines))

        fig_height = min(18, max(8, 4.4 + total_line_count * 0.31))
        fig, ax = plt.subplots(figsize=(12, fig_height), dpi=150)
        fig.patch.set_facecolor("#f8fafc")
        ax.set_axis_off()

        ax.add_patch(Rectangle((0, 0.89), 1, 0.11, transform=ax.transAxes, color="#312e81"))
        ax.text(0.055, 0.95, "ÇOKLU SİNYAL VEREN HİSSELER", transform=ax.transAxes,
                color="white", fontsize=22, fontweight="bold", va="center")
        subtitle = f"{total_symbol_count} hisse"
        if page_count > 1:
            subtitle += f" | Sayfa {page_number}/{page_count} ({len(signal_rows)} hisse)"
        ax.text(0.055, 0.908, subtitle, transform=ax.transAxes, color="#ddd6fe",
                fontsize=12, va="center")

        ax.add_patch(Rectangle((0.055, 0.80), 0.89, 0.045, transform=ax.transAxes,
                               color="#e5e7eb", ec="#d1d5db", lw=1))
        ax.text(0.075, 0.823, "Sembol", transform=ax.transAxes, color="#111827",
                fontsize=10, fontweight="bold", va="center")
        ax.text(0.22, 0.823, "Sinyal", transform=ax.transAxes, color="#111827",
                fontsize=10, fontweight="bold", va="center")
        ax.text(0.34, 0.823, "Periyot / Sinyal Kodu", transform=ax.transAxes, color="#111827",
                fontsize=10, fontweight="bold", va="center")

        if not wrapped_rows:
            ax.add_patch(Rectangle((0.055, 0.30), 0.89, 0.20, transform=ax.transAxes,
                                   color="white", ec="#d1d5db", lw=1.2))
            ax.text(0.50, 0.40, "Birden fazla sinyal alan hisse yok.",
                    transform=ax.transAxes, color="#334155", fontsize=17,
                    fontweight="bold", ha="center", va="center")
        else:
            row_units = max(total_line_count + len(wrapped_rows) * 0.2, 1)
            unit_step = min(0.035, 0.70 / row_units)
            y = 0.765
            for idx, (row, detail_lines) in enumerate(wrapped_rows):
                row_height = unit_step * max(1, len(detail_lines))
                bg = "#ffffff" if idx % 2 == 0 else "#f1f5f9"
                ax.add_patch(Rectangle((0.055, y - row_height + unit_step * 0.15), 0.89,
                                       row_height + unit_step * 0.25, transform=ax.transAxes,
                                       color=bg, ec="#e5e7eb", lw=0.4))
                ax.text(0.075, y, row["symbol"], transform=ax.transAxes, color="#111827",
                        fontsize=9.5, fontweight="bold", va="top")
                ax.text(0.23, y, str(row["count"]), transform=ax.transAxes, color="#312e81",
                        fontsize=9.5, fontweight="bold", va="top")
                for line in detail_lines:
                    ax.text(0.34, y, line, transform=ax.transAxes, color="#334155",
                            fontsize=8.8, va="top")
                    y -= unit_step
                y -= unit_step * 0.2

        fig.savefig(path, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return path

    def send_common_signal_visuals(self, signal_map: Dict[str, dict]) -> bool:
        rows = self._common_signal_rows(signal_map)
        chunks = self._balanced_chunks(rows, COMMON_SIGNAL_IMAGE_MAX_ROWS)

        try:
            image_paths = [
                self._write_common_signal_image(
                    signal_rows=chunk,
                    page_number=idx,
                    page_count=len(chunks),
                    total_symbol_count=len(rows),
                )
                for idx, chunk in enumerate(chunks, start=1)
            ]
        except Exception as e:
            logger.warning(f"Ortak sinyal görseli oluşturulamadı: {e}")
            return False

        ok = True
        for idx, image_path in enumerate(image_paths, start=1):
            caption = "<b>Çoklu Sinyal Özeti</b>"
            if len(image_paths) > 1:
                caption += f" — Sayfa {idx}/{len(image_paths)}"
            ok = self.send_photo(image_path, caption=caption) and ok
            time.sleep(1)
        return ok

    def _write_error_image(self, error_msg: str) -> Optional[str]:
        plt, Rectangle = self._load_matplotlib()
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(SCREENSHOTS_DIR, f"error_{stamp}.png")
        lines = textwrap.wrap(str(error_msg), width=84) or ["Bilinmeyen hata"]
        fig_height = min(14, max(6, 4.5 + len(lines) * 0.24))

        fig, ax = plt.subplots(figsize=(12, fig_height), dpi=150)
        fig.patch.set_facecolor("#fef2f2")
        ax.set_axis_off()

        ax.add_patch(Rectangle((0, 0.84), 1, 0.16, transform=ax.transAxes, color="#991b1b"))
        ax.text(0.055, 0.93, "HATA BILDIRIMI", transform=ax.transAxes,
                color="white", fontsize=23, fontweight="bold", va="center")
        ax.text(0.055, 0.875, datetime.now().strftime("%d.%m.%Y %H:%M"),
                transform=ax.transAxes, color="#fecaca", fontsize=12, va="center")

        ax.add_patch(Rectangle((0.055, 0.10), 0.89, 0.66, transform=ax.transAxes,
                               color="white", ec="#fecaca", lw=1.4))
        y = 0.69
        for line in lines[:26]:
            ax.text(0.08, y, line, transform=ax.transAxes, color="#7f1d1d",
                    fontsize=10.5, va="top")
            y -= 0.035

        fig.savefig(path, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return path

    def send_scan_visuals(
        self,
        period: str,
        strategies: List[dict],
        market_type: Optional[str] = None,
        total_scanned: Optional[int] = None
    ) -> bool:
        """
        Sinyal icin sadece sonuc gorselini gonder.
        """
        try:
            enabled_strategies = self._enabled_strategies(strategies)
            result_groups = []

            for strategy in enabled_strategies:
                rows = self._scan_result_rows([strategy])
                if not rows:
                    continue

                row_chunks = self._balanced_chunks(rows, SCAN_RESULT_IMAGE_MAX_ROWS)
                strategy_label = strategy["code"]
                image_paths = [
                    self._write_scan_result_image(
                        period=period,
                        strategies=[strategy],
                        market_type=market_type,
                        total_scanned=total_scanned,
                        rows=chunk,
                        page_number=idx,
                        page_count=len(row_chunks),
                        total_signal_count=len(rows),
                        strategy_label=strategy_label,
                    )
                    for idx, chunk in enumerate(row_chunks, start=1)
                ]
                result_groups.append((strategy, image_paths))

            if not result_groups:
                image_path = self._write_scan_result_image(
                    period=period,
                    strategies=enabled_strategies,
                    market_type=market_type,
                    total_scanned=total_scanned,
                    rows=[],
                    page_number=1,
                    page_count=1,
                    total_signal_count=0,
                )
                result_groups.append((None, [image_path]))
        except Exception as e:
            logger.warning(f"Sinyal gorselleri olusturulamadi: {e}")
            return False

        p_name = self._period_name(period)
        ok_results = True
        for strategy, result_paths in result_groups:
            for idx, result_path in enumerate(result_paths, start=1):
                caption = f"<b>Sinyal Sonucu</b> — {p_name}"
                if strategy:
                    caption += f" — {strategy['code']}"
                if len(result_paths) > 1:
                    caption += f" — Sayfa {idx}/{len(result_paths)}"
                ok_results = self.send_photo(result_path, caption=caption) and ok_results
                time.sleep(1)
        return ok_results

    def send_grouped_summary(
        self,
        period: str,
        full_signals: List[dict],
        smi_signals: List[dict],
        rsi_signals: List[dict],
        new_scan_signals: List[dict] = None,
        rsi_macd_signals: List[dict] = None,
        ema_signals: List[dict] = None,
        macd_cross_signals: List[dict] = None,
        h8_signals: List[dict] = None,
        i9_signals: List[dict] = None,
        market_type: Optional[str] = None,
        total_scanned: Optional[int] = None,
        enabled_strategy_codes: Optional[List[str]] = None,
        send_images: bool = True
    ) -> bool:
        """
        Sinyal sonuclarini kodlara gore gruplayarak parcali mesajlar halinde gonderir.
        """
        new_scan_signals = new_scan_signals or []
        rsi_macd_signals = rsi_macd_signals or []
        ema_signals = ema_signals or []
        macd_cross_signals = macd_cross_signals or []
        h8_signals = h8_signals or []
        i9_signals = i9_signals or []

        strategies = self._strategy_groups(
            full_signals=full_signals,
            smi_signals=smi_signals,
            rsi_signals=rsi_signals,
            new_scan_signals=new_scan_signals,
            rsi_macd_signals=rsi_macd_signals,
            ema_signals=ema_signals,
            macd_cross_signals=macd_cross_signals,
            h8_signals=h8_signals,
            i9_signals=i9_signals,
            enabled_strategy_codes=enabled_strategy_codes,
        )

        return self.send_scan_visuals(
            period=period,
            strategies=strategies,
            market_type=market_type,
            total_scanned=total_scanned,
        )
    
    def send_error(self, error_msg: str) -> bool:
        """
        Hata mesajı gönder.
        """
        try:
            image_path = self._write_error_image(error_msg)
            return self.send_photo(image_path, caption="<b>HATA</b>")
        except Exception as exc:
            logger.error(f"Hata görseli oluşturulamadı: {exc}")
            return False


# Global singleton instance
_sender_instance: Optional[TelegramSender] = None


def get_telegram_sender() -> TelegramSender:
    """Singleton Telegram gönderici'yi al."""
    global _sender_instance
    if _sender_instance is None:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID
        _sender_instance = TelegramSender(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID)
    return _sender_instance
