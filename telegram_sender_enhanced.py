"""Risk-aware Telegram rendering layered on the existing sender."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from telegram_sender_legacy import *  # noqa: F401,F403
from telegram_sender_legacy import TelegramSender as LegacyTelegramSender
from telegram_sender_legacy import SCREENSHOTS_DIR, SCAN_RESULT_IMAGE_MAX_ROWS


class TelegramSender(LegacyTelegramSender):
    @staticmethod
    def _is_15m(period: str) -> bool:
        return str(period or "").strip().lower() in {"15m", "15min", "15dk"}

    def _scan_result_rows(self, strategies):
        rows = []
        for strategy_index, strategy in enumerate(self._enabled_strategies(strategies)):
            for signal in strategy["signals"]:
                plan = signal.get("exit_plan") or {}
                rows.append({
                    "order": strategy_index,
                    "code": strategy["code"],
                    "name": strategy["name"],
                    "color": strategy["color"],
                    "symbol": signal.get("symbol", "-"),
                    "change": float(signal.get("change", 0) or 0),
                    "daily_change": float(signal.get("daily_change", signal.get("change", 0)) or 0),
                    "current_price": float(signal.get("current_price", signal.get("close", 0)) or 0),
                    "stop": signal.get("stop", plan.get("stop")),
                    "tp1": signal.get("tp1", plan.get("tp1")),
                    "tp2": signal.get("tp2", plan.get("tp2")),
                    "tp3": signal.get("tp3", plan.get("tp3")),
                    "stop_pct": signal.get("stop_pct", plan.get("stop_pct")),
                    "family": signal.get("exit_family", plan.get("family", "")),
                })
        rows.sort(key=lambda row: (row["order"], row["symbol"]))
        return rows

    def _write_scan_result_image(
        self,
        period: str,
        strategies,
        market_type: Optional[str],
        total_scanned: Optional[int],
        rows=None,
        page_number: int = 1,
        page_count: int = 1,
        total_signal_count: Optional[int] = None,
        strategy_label: Optional[str] = None,
    ):
        plt, Rectangle = self._load_matplotlib()
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        page_suffix = f"_{page_number:02d}_of_{page_count:02d}" if page_count > 1 else ""
        strategy_part = f"_{self._safe_filename_part(strategy_label)}" if strategy_label else ""
        path = os.path.join(
            SCREENSHOTS_DIR,
            f"scan_result_{self._safe_filename_part(market_type)}_{self._safe_filename_part(period)}{strategy_part}_{stamp}{page_suffix}.png",
        )
        visible_rows = rows if rows is not None else self._scan_result_rows(strategies)
        total_signal_count = total_signal_count if total_signal_count is not None else len(visible_rows)
        fig_height = min(18, max(8, 4.9 + len(visible_rows) * 0.27))
        fig, ax = plt.subplots(figsize=(14, fig_height), dpi=150)
        fig.patch.set_facecolor("#f8fafc")
        ax.set_axis_off()
        ax.add_patch(Rectangle((0, 0.89), 1, 0.11, transform=ax.transAxes, color="#0f172a"))
        is_15m = self._is_15m(period)
        title = "ERKEN UYARI + REFERANS SEVİYELER" if is_15m else "SİNYAL + RİSK PLANI"
        ax.text(0.045, 0.95, title, transform=ax.transAxes, color="white", fontsize=22, fontweight="bold", va="center")
        subtitle = f"{(market_type or 'BIST').upper()} | {self._period_name(period)}"
        if strategy_label:
            subtitle += f" | {strategy_label}"
        subtitle += f" | {total_signal_count} sinyal"
        if page_count > 1:
            subtitle += f" | Sayfa {page_number}/{page_count}"
        ax.text(0.045, 0.908, subtitle, transform=ax.transAxes, color="#cbd5e1", fontsize=11.5, va="center")

        if not visible_rows:
            ax.add_patch(Rectangle((0.045, 0.30), 0.91, 0.20, transform=ax.transAxes, color="white", ec="#d1d5db", lw=1.2))
            ax.text(0.50, 0.40, "Bu periyotta yeni sinyal yok.", transform=ax.transAxes, color="#334155", fontsize=18, fontweight="bold", ha="center", va="center")
        else:
            headers = [(0.055,"Hisse"),(0.18,"Değişim"),(0.30,"Fiyat"),(0.41,"Stop"),(0.535,"TP1"),(0.66,"TP2"),(0.785,"TP3"),(0.91,"Risk")]
            ax.add_patch(Rectangle((0.045,0.795),0.91,0.045,transform=ax.transAxes,color="#e5e7eb",ec="#d1d5db",lw=1))
            for x,label in headers:
                ax.text(x,0.819,label,transform=ax.transAxes,color="#111827",fontsize=9.5,fontweight="bold",ha="left" if x<0.9 else "right",va="center")
            row_step=min(0.043,0.70/max(len(visible_rows),1)); y=0.765
            for idx,row in enumerate(visible_rows):
                bg="#ffffff" if idx%2==0 else "#f1f5f9"
                ax.add_patch(Rectangle((0.045,y-row_step*.44),0.91,row_step*.88,transform=ax.transAxes,color=bg,ec="#e5e7eb",lw=.4))
                change_text,change_color=self._format_change_value(row.get("change"))
                def pv(value):
                    return self._format_price_value(value) if value is not None else "-"
                try:
                    risk_text=f"{float(row.get('stop_pct')):.1f}%" if row.get("stop_pct") is not None else "-"
                except (TypeError,ValueError):
                    risk_text="-"
                ax.text(.055,y,str(row["symbol"]),transform=ax.transAxes,color="#111827",fontsize=9.4,fontweight="bold",va="center")
                ax.text(.18,y,change_text,transform=ax.transAxes,color=change_color,fontsize=9.1,fontweight="bold",va="center")
                ax.text(.30,y,pv(row.get("current_price")),transform=ax.transAxes,color="#334155",fontsize=9.1,va="center")
                ax.text(.41,y,pv(row.get("stop")),transform=ax.transAxes,color="#b45309",fontsize=9.1,fontweight="bold",va="center")
                ax.text(.535,y,pv(row.get("tp1")),transform=ax.transAxes,color="#2563eb",fontsize=9.1,va="center")
                ax.text(.66,y,pv(row.get("tp2")),transform=ax.transAxes,color="#15803d",fontsize=9.1,va="center")
                ax.text(.785,y,pv(row.get("tp3")),transform=ax.transAxes,color="#166534",fontsize=9.1,fontweight="bold",va="center")
                ax.text(.94,y,risk_text,transform=ax.transAxes,color="#7c2d12",fontsize=8.9,ha="right",va="center")
                y-=row_step
        if is_15m:
            footer = "15 dk erken uyarı/izleme katmanıdır; Stop/TP seviyeleri takip referansıdır, doğrulanmış işlem planı değildir."
        else:
            footer = "Stop/TP seviyeleri başlangıç risk profilleridir; backtest optimizasyonu tamamlandıkça strateji/periyot bazında güncellenecektir."
        ax.text(.045,.045,footer,transform=ax.transAxes,color="#64748b",fontsize=8.6,va="center")
        fig.savefig(path,bbox_inches="tight",facecolor=fig.get_facecolor()); plt.close(fig)
        return path


_sender_instance = None


def get_telegram_sender() -> TelegramSender:
    global _sender_instance
    if _sender_instance is None:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID
        _sender_instance = TelegramSender(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID)
    return _sender_instance
