import json
import os
import asyncio
from datetime import datetime
from telegram_sender import get_telegram_sender
from scanner import MarketScanner

async def get_current_price(symbol, scanner):
    """Sembolün güncel fiyatını çek."""
    try:
        df = scanner.tv.get_hist(symbol, "BIST", interval="1D", n_bars=1)
        if df is not None and not df.empty:
            return df.iloc[-1]['close']
    except Exception as e:
        print(f"Fiyat çekme hatası ({symbol}): {e}")
    return None

async def generate_weekly_report(strategy_name=None):
    state_file = "state.json"
    if not os.path.exists(state_file):
        return "Henüz veri toplanmadı."

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    scanner = MarketScanner()
    
    strategy_keys = {
        "smi_macd": "last_sent_smi_macd",
        "rsi": "last_sent_rsi",
        "new_scan": "last_sent_new_scan",
        "rsi_macd": "last_sent_rsi_macd",
        "ema": "last_sent_ema",
        "macd_cross": "last_sent_macd_cross"
    }

    keys_to_process = [strategy_keys[strategy_name]] if strategy_name and strategy_name in strategy_keys else strategy_keys.values()

    header = f"<b>📊 HAFTALIK PERFORMANS KARNESİ</b>\n"
    header += f"<b>📅 Tarih: {datetime.now().strftime('%d.%m.%Y')}</b>\n"
    header += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"

    body = ""
    found_any = False

    for key in keys_to_process:
        if key in state and state[key]:
            display_name = key.replace("last_sent_", "").upper()
            signals = state[key]
            
            strategy_body = ""
            for symbol_period, data in sorted(signals.items()):
                found_any = True
                symbol, period = symbol_period.rsplit('_', 1)
                
                # Veri formatını kontrol et (eski vs yeni)
                if isinstance(data, dict):
                    signal_price = data.get("price", 0)
                    timestamp = data.get("time", "")
                else:
                    signal_price = 0
                    timestamp = data
                
                current_price = await get_current_price(symbol, scanner)
                
                try:
                    dt = datetime.fromisoformat(timestamp)
                    formatted_time = dt.strftime('%d/%m %H:%M')
                except:
                    formatted_time = timestamp
                
                perf_str = ""
                if signal_price > 0 and current_price:
                    change = ((current_price - signal_price) / signal_price) * 100
                    emoji = "🟢" if change >= 0 else "🔴"
                    perf_str = f"\n  └ {emoji} <b>%{change:.2f}</b> (Giriş: {signal_price:.2f} → Son: {current_price:.2f})"
                elif current_price:
                    perf_str = f"\n  └ 💰 Güncel Fiyat: {current_price:.2f} (Giriş fiyatı yok)"
                
                strategy_body += f"• <b>{symbol}</b> ({period}) - {formatted_time}{perf_str}\n\n"
            
            if strategy_body:
                body += f"<b>🎯 {display_name}</b>\n{strategy_body}"

    if not found_any:
        return "Bu hafta herhangi bir sinyal üretilmedi."

    footer = f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
    footer += f"<i>Kar/Zarar oranları sinyal anındaki fiyat ile güncel fiyat karşılaştırılarak hesaplanmıştır.</i>"

    return header + body + footer

if __name__ == "__main__":
    import sys
    strategy = sys.argv[1] if len(sys.argv) > 1 else None
    
    async def main():
        report = await generate_weekly_report(strategy)
        sender = get_telegram_sender()
        sender.send_message(report)
        
    asyncio.run(main())
