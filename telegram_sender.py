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
from typing import Optional, List
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID,
    EMOJI_FULL_SIGNAL, EMOJI_SMI_SIGNAL, EMOJI_RSI_SIGNAL, 
    EMOJI_NEW_SCAN_SIGNAL, EMOJI_RSI_MACD_SIGNAL, EMOJI_EMA_SIGNAL,
    SCREENSHOTS_DIR
)

logger = logging.getLogger(__name__)


class TelegramSender:
    """Telegram mesaj gönderici."""
    
    def __init__(self, bot_token: str, chat_id: str, thread_id: Optional[str] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
    
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
            "15m": "15 DAKİKA", "15M": "15 DAKİKA",
            "1h": "1 SAAT", "1H": "1 SAAT",
            "4h": "4 SAAT", "4H": "4 SAAT",
            "1d": "GÜNLÜK", "1D": "GÜNLÜK",
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
                "key": "macd_cross", "signals": macd_cross_signals or [], "emoji": "🟣", "code": "S1",
                "name": "MACD Pozitif Kesişim",
                "description": "RSI 50 üstü, MACD sinyal çizgisini yukarı keser ve MACD pozitif bölgede kalır.",
                "color": "#7c3aed",
            },
            {
                "key": "h8", "signals": h8_signals or [], "emoji": "🔷", "code": "H8",
                "name": "SMI/MACD Pozitif",
                "description": "SMI yukarı kesişim, MACD histogram pozitif ve güçlenen momentum koşullarını arar.",
                "color": "#2563eb",
            },
            {
                "key": "i9", "signals": i9_signals or [], "emoji": "🔹", "code": "I9",
                "name": "SMI/MACD Pozitif Full",
                "description": "H8 koşullarına ek olarak MA200 üstü fiyat ve güçlü hacim onayı ister.",
                "color": "#0891b2",
            },
            {
                "key": "ema", "signals": ema_signals or [], "emoji": EMOJI_EMA_SIGNAL, "code": "S2",
                "name": "EMA Dizilimi",
                "description": "Kısa EMA'ların uzun EMA'lar üstünde hizalandığı ve hacmin desteklediği trend düzenini tarar.",
                "color": "#f97316",
            },
            {
                "key": "rsi_macd", "signals": rsi_macd_signals or [], "emoji": EMOJI_RSI_MACD_SIGNAL, "code": "S3",
                "name": "RSI + MACD + Hacim",
                "description": "RSI güçlenirken MACD yukarı kesişim ve hacim artışı birlikte oluşur.",
                "color": "#16a34a",
            },
            {
                "key": "new_scan", "signals": new_scan_signals or [], "emoji": EMOJI_NEW_SCAN_SIGNAL, "code": "S4",
                "name": "SMA + MACD + Hacim",
                "description": "SMA 5/8/21 dizilimi, MACD pozitifliği, RSI aralığı ve hacim onayını birleştirir.",
                "color": "#ca8a04",
            },
            {
                "key": "full", "signals": full_signals or [], "emoji": EMOJI_FULL_SIGNAL, "code": "S5",
                "name": "SMI/MACD Full",
                "description": "SMI/MACD alım sinyaline MA200 üstü fiyat ve hacim filtresi ekler.",
                "color": "#dc2626",
            },
            {
                "key": "smi", "signals": smi_signals or [], "emoji": EMOJI_SMI_SIGNAL, "code": "S6",
                "name": "SMI/MACD",
                "description": "SMI ve MACD momentum kesişimlerini temel alan erken alım sinyalidir.",
                "color": "#9333ea",
            },
            {
                "key": "rsi", "signals": rsi_signals or [], "emoji": EMOJI_RSI_SIGNAL, "code": "S7",
                "name": "RSI",
                "description": "RSI'ın güç bölgesine geçişini ve yükseliş momentumunu izler.",
                "color": "#0ea5e9",
            },
        ]

        for group in groups:
            group["enabled"] = (
                not enabled
                or group["code"].lower() in enabled
                or group["key"].lower() in enabled
            )
        return groups

    def _format_strategy_legend(self, strategies: List[dict]) -> str:
        enabled = [s for s in strategies if s.get("enabled", True)]
        if not enabled:
            enabled = strategies

        lines = ["<b>📌 Tarama içeriği:</b>"]
        for strategy in enabled:
            lines.append(
                f"• <b>{strategy['code']} - {strategy['name']}</b>: "
                f"{strategy['description']}"
            )
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
        ax.text(0.055, 0.94, "TARAMA BİLGİSİ", transform=ax.transAxes, color="white",
                fontsize=23, fontweight="bold", va="center")
        ax.text(0.055, 0.895, datetime.now().strftime("%d.%m.%Y %H:%M"),
                transform=ax.transAxes, color="#cbd5e1", fontsize=12, va="center")

        cards = [
            ("Pazar", (market_type or "BIST").upper()),
            ("Periyot", self._period_name(period)),
            ("Taranan", str(total_scanned) if total_scanned is not None else "-"),
            ("Strateji", str(len(enabled))),
        ]
        for idx, (label, value) in enumerate(cards):
            x = 0.055 + idx * 0.235
            ax.add_patch(Rectangle((x, 0.76), 0.19, 0.075, transform=ax.transAxes,
                                   color="white", ec="#d1d5db", lw=1.2))
            ax.text(x + 0.015, 0.81, label, transform=ax.transAxes, color="#6b7280",
                    fontsize=10, va="center")
            ax.text(x + 0.015, 0.775, value, transform=ax.transAxes, color="#111827",
                    fontsize=16, fontweight="bold", va="center")

        ax.text(0.055, 0.70, "Tarama İçerikleri", transform=ax.transAxes, color="#111827",
                fontsize=18, fontweight="bold", va="center")

        y = 0.64
        for strategy in enabled:
            color = strategy["color"]
            ax.add_patch(Rectangle((0.06, y - 0.02), 0.075, 0.044, transform=ax.transAxes,
                                   color=color, ec=color))
            ax.text(0.0975, y + 0.002, strategy["code"], transform=ax.transAxes,
                    color="white", fontsize=12, fontweight="bold", ha="center", va="center")
            ax.text(0.155, y + 0.014, strategy["name"], transform=ax.transAxes,
                    color="#111827", fontsize=12.5, fontweight="bold", va="center")
            desc = "\n".join(textwrap.wrap(strategy["description"], width=88))
            ax.text(0.155, y - 0.014, desc, transform=ax.transAxes,
                    color="#4b5563", fontsize=9.5, va="top")
            y -= 0.067

        ax.text(0.055, 0.045, "Bu görsel taramanın kapsamını, periyodunu ve kullanılan kodların anlamını gösterir.",
                transform=ax.transAxes, color="#64748b", fontsize=9.5, va="center")
        fig.savefig(path, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return path

    def _write_scan_result_image(
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
            f"scan_result_{self._safe_filename_part(market_type)}_{self._safe_filename_part(period)}_{stamp}.png"
        )

        enabled = [s for s in strategies if s.get("enabled", True)] or strategies
        rows = []
        for strategy in enabled:
            for signal in strategy["signals"]:
                rows.append({
                    "code": strategy["code"],
                    "name": strategy["name"],
                    "color": strategy["color"],
                    "symbol": signal.get("symbol", "-"),
                    "change": float(signal.get("change", 0) or 0),
                    "close": float(signal.get("close", 0) or 0),
                })
        rows.sort(key=lambda row: (row["code"], row["symbol"]))
        visible_rows = rows[:80]
        extra_count = max(0, len(rows) - len(visible_rows))

        fig_height = min(18, max(8, 4.8 + len(visible_rows) * 0.22))
        fig, ax = plt.subplots(figsize=(12, fig_height), dpi=150)
        fig.patch.set_facecolor("#f8fafc")
        ax.set_axis_off()

        ax.add_patch(Rectangle((0, 0.89), 1, 0.11, transform=ax.transAxes, color="#0f172a"))
        ax.text(0.055, 0.95, "TARAMA SONUCU", transform=ax.transAxes, color="white",
                fontsize=23, fontweight="bold", va="center")
        subtitle = f"{(market_type or 'BIST').upper()} | {self._period_name(period)} | {len(rows)} sinyal"
        if total_scanned is not None:
            subtitle += f" | {total_scanned} sembol tarandı"
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
        if not rows:
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
            ax.text(0.64, table_top + 0.022, "Tarama", transform=ax.transAxes,
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
                ax.text(0.64, y, row["name"], transform=ax.transAxes,
                        color="#334155", fontsize=font_size, va="center")
                y -= row_step

            if extra_count:
                ax.text(0.055, max(y - 0.02, 0.04), f"+{extra_count} sinyal daha mesajlarda listelendi.",
                        transform=ax.transAxes, color="#64748b", fontsize=10, va="center")

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
        Tarama icin iki gorsel gonder: once bilgi, sonra sonuc.
        """
        try:
            info_path = self._write_scan_info_image(period, strategies, market_type, total_scanned)
            result_path = self._write_scan_result_image(period, strategies, market_type, total_scanned)
        except Exception as e:
            logger.warning(f"Tarama görselleri oluşturulamadı: {e}")
            return False

        p_name = self._period_name(period)
        ok_info = self.send_photo(info_path, caption=f"<b>Tarama Bilgisi</b> — {p_name}")
        time.sleep(1)
        ok_result = self.send_photo(result_path, caption=f"<b>Tarama Sonucu</b> — {p_name}")
        return ok_info and ok_result

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
        Tarama sonuçlarını stratejiye göre gruplayarak parçalı mesajlar halinde gönderir.
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

        if send_images:
            self.send_scan_visuals(
                period=period,
                strategies=strategies,
                market_type=market_type,
                total_scanned=total_scanned,
            )
            time.sleep(1)

        p_name = self._period_name(period)
        
        header = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        header += f"<b>🔍 {p_name}</b>\n"
        header += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        
        # Tüm sinyalleri kontrol et
        all_empty = not any(strategy["signals"] for strategy in strategies)
        
        if all_empty:
            empty_msg = header + "\n" + self._format_strategy_legend(strategies)
            empty_msg += f"\n\n<b>ℹ️</b>\n\n<i>Sinyal yok.</i>\n"
            empty_msg += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>"
            return self.send_message(empty_msg)

        # Başlık ve tarama içeriklerini gönder
        self.send_message(header + "\n" + self._format_strategy_legend(strategies))
        time.sleep(1)

        for strategy in strategies:
            signals = strategy["signals"]
            if not signals:
                continue
            
            # Her strateji için mesaj oluştur
            strat_header = (
                f"<b>{strategy['emoji']} {strategy['code']} - {strategy['name']}</b>\n"
                f"<i>{strategy['description']}</i>\n"
            )
            
            # Sinyalleri 20'şerli gruplara böl (Daha küçük gruplar 429 riskini azaltır)
            for i in range(0, len(signals), 20):
                chunk = signals[i:i+20]
                body = self._format_signal_list(chunk)
                
                msg = strat_header + body
                if i + 20 < len(signals):
                    msg += "\n\n<b>(Devamı...)</b>"
                
                self.send_message(msg)
                time.sleep(2) # Mesajlar arası daha uzun bekleme

        # Kapanış çizgisi
        self.send_message(f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>")
        return True
    
    def send_error(self, error_msg: str) -> bool:
        """
        Hata mesajı gönder.
        """
        message = f"<b>❌ HATA</b>\n\n{error_msg}"
        return self.send_message(message)


# Global singleton instance
_sender_instance: Optional[TelegramSender] = None


def get_telegram_sender() -> TelegramSender:
    """Singleton Telegram gönderici'yi al."""
    global _sender_instance
    if _sender_instance is None:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID
        _sender_instance = TelegramSender(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID)
    return _sender_instance
