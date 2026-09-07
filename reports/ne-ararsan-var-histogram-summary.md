# NE ARARSAN VAR — MACD Histogram AL+SAT Walk-Forward Summary

Run: `34125847363`

## Method

Common entry skeleton:
- `Close > EMA5 > EMA8 > EMA13`
- `30 < RSI14 < 60`
- `RVOL20 > 1.50`
- StochRSI removed

MACD histogram entry candidates:
- `H[t] > H[t-1] > H[t-2]`, sign unrestricted
- same while `H < 0`
- same while `H > 0`
- each tested both with and without `-100 < CCI20 < 100`

A condition becoming true is counted only once (False -> True), and entry is at next-bar open.

Exit candidates compared structural 1R/2R/3R partial profit taking with histogram-aware hard exits and adaptive trailing variants. Costs were 10 bps commission + 10 bps slippage per side. Four expanding walk-forward next-window folds were used. The previous crossover holdout had already been observed, so these are walk-forward research results, not a new untouched holdout.

## Stitched walk-forward results

| TF | Trades | PF | Expectancy R | Win rate | Positive folds | Classification |
|---|---:|---:|---:|---:|---:|---|
| 15m | 4,815 | 0.222 | -0.914R | 25.52% | 0/4 | REJECT |
| 30m | 2,026 | 0.461 | -0.486R | 33.86% | 0/4 | REJECT |
| 45m | 4,402 | 0.636 | -0.287R | 37.16% | 0/4 | REJECT |
| 1H | 1,564 | 0.587 | -0.332R | 32.93% | 0/4 | REJECT |
| 2H | 1,446 | 1.037 | +0.023R | 40.11% | 1/4 | REJECT / research |
| 4H | 762 | 1.250 | +0.131R | 44.23% | 2/4 | UNSTABLE POSITIVE |
| 1D | 1,494 | 1.368 | +0.181R | 44.58% | 4/4 | STRONG WALK-FORWARD |
| 1W | 1,032 | 2.982 | +0.719R | 59.30% | 4/4 | STRONG WALK-FORWARD |
| 1M | 61 | 3.548 | +0.870R | 52.46% | 3/4 | INSUFFICIENT SAMPLE / promising |

## Timeframe details

### 15m
Selected entries: positive histogram rising + CCI in folds 1-3; negative histogram rising without CCI in fold 4. Histogram exits did not rescue the timeframe. Combined PF 0.222, expectancy -0.914R.

### 30m
Negative histogram rising was generally preferred, mostly without CCI. Structural `control_123` exit won every fold. Combined PF 0.461, expectancy -0.486R.

### 45m
Positive histogram rising + CCI won all four folds, but all folds remained negative. Structural `control_123` exit won every fold. Combined PF 0.636, expectancy -0.287R.

### 1H
Entry selection varied, mostly negative histogram rising without CCI. Structural `control_123` exit won every fold. Combined PF 0.587, expectancy -0.332R.

### 2H
Negative histogram rising without CCI won all four folds. Structural `control_123` exit won all folds. Fold PFs: 0.961 / 1.258 / 0.910 / 0.977. Combined PF 1.037, expectancy +0.023R. This is weaker than the earlier crossover-based 2H research and should not replace it.

### 4H
Negative histogram rising without CCI won all four folds. Structural `control_123` exit won all folds. Fold PFs: 0.704 / 2.045 / 1.199 / 0.825. Combined PF 1.250, expectancy +0.131R, but only 2/4 folds were positive. Keep as research/secondary confirmation rather than replacing the previously validated crossover 4H profile.

### 1D
Strong result. Fold entries: positive+CCI / negative+CCI / positive+CCI / positive+CCI. Every fold selected the structural `control_123` exit. Fold PFs: 1.571 / 1.117 / 1.380 / 1.174. Combined PF 1.368, expectancy +0.181R, 4/4 positive folds.

Current forward candidate:
- histogram positive and rising for two consecutive steps: `H[t] > H[t-1] > H[t-2]` and `H[t] > 0`
- `-100 < CCI20 < 100`
- structural stop: 7-bar swing low minus 0.20 ATR buffer
- TP1 +1R: sell 30%
- TP2 +2R: sell 30%
- TP3 +3R: sell 20%
- runner: 20%
- trailing after TP2: 2.3 ATR
- max hold: 40 bars
- no histogram hard-exit rule

### 1W
Very strong result. Entry sign changed with regime: positive/no-CCI in folds 1-2, negative/no-CCI in fold 3, negative+CCI in fold 4. Structural `control_123` exit won all four folds. Fold PFs: 1.728 / 5.818 / 4.539 / 1.911. Combined PF 2.982, expectancy +0.719R, 4/4 positive folds.

Current forward candidate from latest fold:
- negative histogram but rising for two consecutive steps
- CCI -100..100
- structural stop: 9-bar swing low minus 0.15 ATR buffer
- TP1/TP2/TP3: 1R/2R/3R with 30%/30%/20%
- 20% runner
- trailing after TP2: 2.3 ATR
- max hold: 80 bars
- no histogram hard-exit rule

Because histogram sign changed across folds, a fixed production rule should probably use histogram slope as the core condition and treat sign as regime/context rather than hard-code the latest sign without another focused robustness test.

### 1M
Only 61 stitched trades. PF 3.548 and expectancy +0.870R look attractive, but one tiny fold generated an extreme PF and the latest fold was negative (PF 0.479). Positive histogram rising + CCI was selected in all folds. Keep research-only until sample grows.

## Main conclusions

1. Histogram entry materially improved the higher-timeframe picture, especially 1D and 1W.
2. Histogram entry did not rescue 15m-1H; these versions should not be promoted.
3. 2H is weaker with histogram than in the earlier crossover research.
4. 4H is positive in aggregate but regime-unstable; keep crossover as the safer primary candidate for now.
5. Histogram hard SAT rules were almost never selected. Structural stop + 1R/2R/3R partials + runner + ATR trailing remained superior.
6. CCI is not universally useful: it looks helpful on 1D, mixed on 1W, and unnecessary on 2H/4H. Do not force one CCI policy across all timeframes.
7. The most defensible next design is timeframe-specific: crossover/structure for some TFs, histogram-slope entry for 1D/1W, and structural SAT rather than histogram hard exits.
