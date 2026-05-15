import json
import os
from datetime import datetime
from telegram_sender import get_telegram_sender

def generate_weekly_report():
    state_file = "state.json"
    if not os.path.exists(state_file):
        return "Henüz veri toplanmadı."

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    strategy_names = {
        "last_sent_smi_macd": "SMI/MACD",
        "last_sent_rsi": "RSI",
        "last_sent_new_scan": "Özel Tarama",
        "last_sent_rsi_macd": "RSI+MACD+Hacim",
        "last_sent_ema": "EMA Dizilimi",
        "last_sent_macd_cross": "MACD Pozitif Kesişim"
    }

    header = f"<b>📊 HAFTALIK PERFORMANS KARNESİ (TÜMÜ)</b>\n"
    header += f"<b>📅 Tarih: {datetime.now().strftime('%d.%m.%Y')}</b>\n"
    header += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"

    body = ""
    found_any = False

    for key, display_name in strategy_names.items():
        if key in state and state[key]:
            found_any = True
            body += f"<b>🎯 {display_name}</b>\n"
            signals = state[key]
            # Sembolleri alfabetik sırala
            for symbol_period, timestamp in sorted(signals.items()):
                symbol, period = symbol_period.rsplit('_', 1)
                try:
                    dt = datetime.fromisoformat(timestamp)
                    formatted_time = dt.strftime('%d/%m %H:%M')
                except:
                    formatted_time = timestamp
                body += f"• {symbol} ({period}) - {formatted_time}\n"
            body += "\n"

    if not found_any:
        return "Bu hafta herhangi bir sinyal üretilmedi."

    footer = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
    footer += f"<i>Taramabot Ana Depo Haftalık Özetidir.</i>"

    return header + body + footer

if __name__ == "__main__":
    report = generate_weekly_report()
    sender = get_telegram_sender()
    sender.send_message(report)
