import os
import mplfinance as mpf

def make_candle_chart(df, out_png: str, title: str):
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    mpf.plot(
        df,
        type="candle",
        volume=True,
        title=title,
        style="yahoo",
        savefig=out_png
    )
