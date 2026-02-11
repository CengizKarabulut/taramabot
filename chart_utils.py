import os
import mplfinance as mpf

def make_candle_chart(df, out_png: str, title: str):
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    
    addplots = []
    
    if 'MA200' in df.columns and not df['MA200'].isnull().all():
        addplots.append(mpf.make_addplot(df['MA200'], color='blue', width=1.5))

    if 'SMI' in df.columns and 'SMI_EMA' in df.columns:
        addplots.append(mpf.make_addplot(df['SMI'], panel=2, color='green', width=1.5, ylabel='SMI'))
        addplots.append(mpf.make_addplot(df['SMI_EMA'], panel=2, color='red', width=1.5))

    if 'MACD' in df.columns and 'Signal' in df.columns:
        addplots.append(mpf.make_addplot(df['MACD'], panel=3, color='blue', width=1.5, ylabel='MACD'))
        addplots.append(mpf.make_addplot(df['Signal'], panel=3, color='orange', width=1.5))
        if 'Hist' in df.columns:
             addplots.append(mpf.make_addplot(df['Hist'], panel=3, type='bar', color='gray', alpha=0.5))

    panels = 2 + (1 if 'SMI' in df.columns else 0) + (1 if 'MACD' in df.columns else 0)
    ratios = (4, 1)
    if panels == 3: ratios = (4, 1, 2)
    elif panels == 4: ratios = (4, 1, 1.5, 1.5)

    mpf.plot(
        df,
        type="candle",
        volume=True,
        title=title,
        style="yahoo",
        savefig=out_png,
        addplot=addplots,
        panel_ratios=ratios,
        tight_layout=True
    )
