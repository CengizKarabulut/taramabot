# NE ARARSAN VAR v1 — Timeframe-Specific SAT Walk-Forward Summary

Run: `34114084300`

Methodology:
- Previous final 20% holdout had already been observed, so this follow-up does **not** relabel it as untouched OOS.
- Five timeframes were retested with different exit logic families: 15m, 30m, 45m, 1H, 2H.
- Each timeframe had 12 pre-declared SAT candidates tailored to its speed.
- Four expanding walk-forward folds were used. For every fold, a profile was selected only from earlier data, then tested on the next chronological window.
- Costs: 10 bps commission + 10 bps slippage **per side**.

## Stitched walk-forward OOS results

| TF | Signals | OOS trades | PF | Expectancy (R) | Win rate | Positive folds | PF>1 folds | Classification |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 15m | 2528 | 1384 | 0.2274 | -0.5335 | 13.08% | 0/4 | 0/4 | REJECT |
| 30m | 2413 | 1314 | 0.4670 | -0.2773 | 20.02% | 0/4 | 0/4 | REJECT |
| 45m | 2236 | 1216 | 0.7042 | -0.1556 | 22.04% | 0/4 | 0/4 | REJECT |
| 1H | 1394 | 736 | 0.5759 | -0.2227 | 22.69% | 0/4 | 0/4 | REJECT |
| 2H | 1936 | 1049 | 1.1583 | +0.1085 | 32.70% | 2/4 | 2/4 | REJECT (near-miss / research) |

## Profiles repeatedly selected by walk-forward

### 15m
`15m_swing5_ema5` was selected in all four folds.
- 5-bar swing stop with 0.10 ATR buffer
- close below EMA5 technical exit
- maximum hold 12 bars
- Fold PFs: 0.302 / 0.213 / 0.199 / 0.193
- Fold expectancy: -0.438R / -0.554R / -0.562R / -0.602R

Conclusion: changing SAT logic did not rescue 15m. The problem is likely upstream signal edge rather than only exit design.

### 30m
`30m_swing5_ema8` was selected in all four folds.
- 5-bar swing stop with 0.15 ATR buffer
- close below EMA8 technical exit
- maximum hold 16 bars
- Fold PFs: 0.551 / 0.667 / 0.311 / 0.324
- Fold expectancy: -0.227R / -0.162R / -0.378R / -0.371R

Conclusion: dedicated 30m exits improved shape relative to some generic variants but remained consistently negative.

### 45m
`45m_swing5_twoof3` was selected in all four folds.
- 5-bar swing stop with 0.15 ATR buffer
- exit when at least two are true: close < EMA8, MACD < signal, RSI14 < 50
- maximum hold 16 bars
- Fold PFs: 0.656 / 0.927 / 0.700 / 0.474
- Fold expectancy: -0.181R / -0.038R / -0.155R / -0.292R

Conclusion: the second fold came close to neutral, but the rule was not stable enough across regimes.

### 1H
`1H_swing7_ema13macd` was selected in all four folds.
- 7-bar swing stop with 0.20 ATR buffer
- technical exit when close < EMA13 **and** MACD < signal
- maximum hold 24 bars
- Fold PFs: 0.357 / 0.941 / 0.486 / 0.515
- Fold expectancy: -0.351R / -0.028R / -0.277R / -0.264R

Conclusion: one regime was almost neutral, but the exit family did not produce stable positive edge.

### 2H
Selection changed across regimes:
- Fold 1: `2H_ema13macd_24`
- Folds 2–4: `2H_trail_12_23`

`2H_trail_12_23`:
- 1.20 ATR initial stop
- trailing starts after +1.2R MFE
- 2.3 ATR chandelier-style trailing stop
- no forced technical exit
- maximum hold 40 bars

Fold results:
- Fold 1: PF 0.983, expectancy -0.012R
- Fold 2: PF 1.597, expectancy +0.391R
- Fold 3: PF 1.187, expectancy +0.125R
- Fold 4: PF 0.772, expectancy -0.163R

Combined 2H walk-forward: PF 1.158, expectancy +0.108R over 1049 trades.

Conclusion: 2H has genuine signs of edge, but regime stability is insufficient for promotion. It is the strongest candidate for a second, more targeted SAT study.

## Research interpretation

1. A different SAT architecture per timeframe is justified; the preferred exit logic clearly changes with timeframe.
2. However, 15m–1H remained negative even after timeframe-specific SAT families. This suggests that exit optimization alone is unlikely to fix those timeframes; entry/filter logic likely needs to change.
3. 2H remains worth deeper work. A regime-aware combination of trend continuation, profit-lock activation, and volatility-based trailing should be tested next.
4. 4H/1D/1W remain the previously promoted timeframes and were not altered by this experiment.
