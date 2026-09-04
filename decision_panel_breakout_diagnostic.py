"""Gate-by-gate diagnostic for Karar Paneli v6.4.4 BREAKOUT.

The purpose is not to loosen rules blindly.  It counts how many historical BIST
bars survive each exact Pine v6.4.4 breakout requirement and also evaluates a
few one-at-a-time relaxations so we can identify the real bottleneck before
changing Pine or production Python logic.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from historical_decision_panel_v644 import _adx, _atr, _normalize, _rsi
from market_data_store import MarketDataStore


def gate_frame(frame: pd.DataFrame, min_score: int = 75) -> pd.DataFrame:
    df = _normalize(frame)
    o,h,l,c,v = (df[x] for x in ("open","high","low","close","volume"))
    sma20,c50,c200 = c.rolling(20).mean(),c.rolling(50).mean(),c.rolling(200).mean()
    ema5 = c.ewm(span=5,adjust=False,min_periods=5).mean(); ema8=c.ewm(span=8,adjust=False,min_periods=8).mean(); ema13=c.ewm(span=13,adjust=False,min_periods=13).mean()
    slope20=(sma20/sma20.shift(5)-1)*100; slope50=(c50/c50.shift(10)-1)*100; slope200=(c200/c200.shift(20)-1)*100
    price20=c>sma20; s2050=sma20>c50; s50200=c50>c200; sl20=slope20>=0; sl50=slope50>=0; sl200=slope200>=-.5
    trend_core=price20&s2050&s50200&sl20&sl50&sl200
    e5up=ema5>ema5.shift(1); e8up=ema8>ema8.shift(1); e13up=ema13>ema13.shift(1)
    short_aligned=(ema5>ema8)&(ema8>ema13); short_core=short_aligned&e5up&e8up&e13up

    macd=c.ewm(span=12,adjust=False,min_periods=12).mean()-c.ewm(span=26,adjust=False,min_periods=26).mean(); sig=macd.ewm(span=9,adjust=False,min_periods=9).mean(); hist=macd-sig
    hist_pos=hist>0; hist_cross=hist_pos&(hist.shift(1)<=0); hist_strength=hist_pos&(hist>hist.shift(1))
    rsi=_rsi(c,14); rsi_ok=(rsi>=55)&(rsi<=72)
    plus,minus,adx=_adx(df,14,14); strength=(adx>20)&(plus>minus)
    atr=_atr(df,14); atr_pct=atr/c.replace(0,np.nan)*100; atr_ok=(atr_pct>=1.5)&(atr_pct<=7)

    avgvol=v.rolling(10).mean().shift(1); rvol=v/avgvol.replace(0,np.nan); turnover=c*v; relturn=turnover/turnover.rolling(20).mean().shift(1).replace(0,np.nan)
    high52=h.rolling(252).max(); high20=h.rolling(20).max(); prev20=high20.shift(1); pct52=c/high52.replace(0,np.nan)*100; pct20=c/high20.replace(0,np.nan)*100
    near52=pct52>=85; near20=pct20>=95; breakout20=c>prev20; participation=rvol>pd.Series(np.where(breakout20,1.5,1.2),index=df.index)
    dist_pct=(c/sma20.replace(0,np.nan)-1)*100; dist_atr=(c-sma20)/atr.replace(0,np.nan); distance=(dist_pct>=0)&(dist_pct<=7)&(dist_atr>=0)&(dist_atr<=1.5)

    bb_basis=c.rolling(20).mean(); dev=2*c.rolling(20).std(ddof=0); bb_u=bb_basis+dev; bb_l=bb_basis-dev; width=(bb_u-bb_l)/bb_basis.replace(0,np.nan)*100; width_rise=width>width.shift(1); width_avg=width.rolling(20).mean(); width_above=width>width_avg
    rng=(h-l).replace(0,np.nan); clv=((c-l)/rng).fillna(.5); clv_ok=clv>=.60; wick=h-pd.concat([o,c],axis=1).max(axis=1); wick_ok=wick/atr.replace(0,np.nan)<=.50
    breakout_momentum=(macd>sig)&(macd>0)&rsi_ok&(hist_strength|hist_cross); quality=width_rise&clv_ok&wick_ok

    trend_score=4*price20.astype(int)+4*s2050.astype(int)+4*s50200.astype(int)+3*sl20.astype(int)+3*sl50.astype(int)+2*(slope200>0).astype(int)+3*short_aligned.astype(int)+2*(e5up&e8up&e13up).astype(int)
    hist_score=pd.Series(np.select([hist_cross,hist_strength,(hist<0)&(hist>hist.shift(1)),hist_pos&(hist<hist.shift(1))],[6,6,4,2],default=0),index=df.index)
    momentum=4*(macd>0).astype(int)+5*(macd>sig).astype(int)+hist_score+5*((rsi>=55)&(rsi<=68)).astype(int)
    rvscore=pd.Series(np.select([rvol>=3,rvol>=2,rvol>=1.5,rvol>=1.2,rvol>=1],[18,16,12,7,3],default=0),index=df.index); volume=rvscore+2*(relturn>=1).astype(int)
    adxscore=pd.Series(np.select([adx>=40,adx>=30,adx>=25,adx>20],[7,6,5,3],default=0),index=df.index); strengthscore=adxscore+3*(plus>minus).astype(int)
    h52score=pd.Series(np.select([pct52>=95,pct52>=90,pct52>=85],[7,5,3],default=0),index=df.index); location=h52score+3*near20.astype(int)
    dps=pd.Series(np.select([((dist_pct>=0)&(dist_pct<=3)),((dist_pct>3)&(dist_pct<=5)),((dist_pct>5)&(dist_pct<=7))],[5,4,2],default=0),index=df.index)
    das=pd.Series(np.select([((dist_atr>=0)&(dist_atr<1)),((dist_atr>=1)&(dist_atr<=1.5)),((dist_atr>1.5)&(dist_atr<=2))],[5,4,2],default=0),index=df.index)
    volscore=2*width_rise.astype(int)+width_above.astype(int)+2*atr_ok.astype(int); score=(trend_score+momentum+volume+strengthscore+location+dps+das+volscore).astype(int); score_ok=score>=min_score
    pos=pd.Series(np.arange(len(df)),index=df.index); history=(pos>=251)&c200.shift(20).notna()&rsi.notna()&atr.notna()&high52.notna(); structure=trend_core&strength&near52

    out=pd.DataFrame(index=df.index)
    for name,series in {
        "history_ready":history,"structure_core":structure,"participation_ok":participation,"distance_core":distance,"score_ok":score_ok,
        "breakout20":breakout20,"short_trend_core":short_core,"breakout_momentum_ok":breakout_momentum,"breakout_quality_ok":quality,
        "bb_width_rising":width_rise,"clv_ok":clv_ok,"upper_wick_ok":wick_ok,"rsi_ok":rsi_ok,"near52":near52,
    }.items(): out[name]=series.fillna(False)
    out["score"]=score; out["rvol"]=rvol; out["dist_atr"]=dist_atr; out["rsi"]=rsi
    out["final_breakout"]=history&structure&participation&distance&score_ok&breakout20&short_core&breakout_momentum&quality
    return out


def diagnose(database: str, min_score: int=75) -> dict:
    gates=["history_ready","structure_core","participation_ok","distance_core","score_ok","breakout20","short_trend_core","breakout_momentum_ok","breakout_quality_ok"]
    standalone={g:0 for g in gates}; funnel={g:0 for g in gates}; eligible_breakout_bars=0; total_bars=0; score_relax={70:0,72:0,75:0}; distance_relax={1.5:0,1.75:0,2.0:0}; rvol_relax={1.2:0,1.35:0,1.5:0}
    with MarketDataStore(database,read_only=True) as store:
        symbols=store.list_symbols("BIST","1D")
        for n,symbol in enumerate(symbols,1):
            frame=store.load_dataframe(symbol,"BIST","1D",limit=0)
            if frame is None or frame.empty: continue
            g=gate_frame(frame,min_score=min_score); total_bars+=len(g)
            for name in gates: standalone[name]+=int(g[name].sum())
            running=pd.Series(True,index=g.index)
            for name in gates:
                running &= g[name]; funnel[name]+=int(running.sum())
            base_no_score=g["history_ready"]&g["structure_core"]&g["participation_ok"]&g["distance_core"]&g["breakout20"]&g["short_trend_core"]&g["breakout_momentum_ok"]&g["breakout_quality_ok"]
            for threshold in score_relax: score_relax[threshold]+=int((base_no_score&(g["score"]>=threshold)).sum())
            base_no_dist=g["history_ready"]&g["structure_core"]&g["participation_ok"]&g["score_ok"]&g["breakout20"]&g["short_trend_core"]&g["breakout_momentum_ok"]&g["breakout_quality_ok"]
            for lim in distance_relax: distance_relax[lim]+=int((base_no_dist&(g["dist_atr"]>=0)&(g["dist_atr"]<=lim)).sum())
            base_no_part=g["history_ready"]&g["structure_core"]&g["distance_core"]&g["score_ok"]&g["breakout20"]&g["short_trend_core"]&g["breakout_momentum_ok"]&g["breakout_quality_ok"]
            for rv in rvol_relax: rvol_relax[rv]+=int((base_no_part&(g["rvol"]>rv)).sum())
            eligible_breakout_bars+=int(g["final_breakout"].sum())
            if n%100==0: print(f"{n}/{len(symbols)}")
    drops=[]; previous=total_bars
    for name in gates:
        current=funnel[name]; drops.append({"gate":name,"survivors":current,"drop":previous-current,"survival_from_previous_pct":(current/previous*100 if previous else 0)}); previous=current
    return {"min_score":min_score,"symbols":len(symbols),"total_bars":total_bars,"final_breakout_bars":eligible_breakout_bars,"standalone_true_counts":standalone,"sequential_funnel":drops,"one_at_a_time_relaxations":{"score_threshold":score_relax,"max_distance_atr":distance_relax,"breakout_rvol_threshold":rvol_relax}}


def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--db",required=True); p.add_argument("--min-score",type=int,default=75); p.add_argument("--output",required=True); a=p.parse_args(); result=diagnose(a.db,a.min_score); dest=Path(a.output); dest.parent.mkdir(parents=True,exist_ok=True); dest.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(result,ensure_ascii=False,indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())
