# Production Scanner Registry Integration v1

Date: 2026-09-08
Branch: `integration/production-registry-v1`
Research source: `CengizKarabulut/deneme`

## Purpose

This layer moves the frozen scanner research decisions into `taramabot` without changing the current A-I / KARAR live flow yet.

The integration deliberately separates two sources of truth:

1. **Common promotion registry** decides whether a family/timeframe is `CORE`, `ACTIVE`, `SECONDARY`, `FORWARD_WATCH`, `RESEARCH` or `REJECT`.
2. **Locked family research spec** defines the actual completed-bar entry semantics for that family/timeframe.

A scanner quality score is a historical ranking aid, not a predicted win probability.

## Routing policy

Normal production routing is limited to `CORE`, `ACTIVE` and `SECONDARY`.

- `CORE`: primary production evidence.
- `ACTIVE`: normal production evidence.
- `SECONDARY`: supporting / early-warning evidence. It receives a lower stock-confidence weight because the historical robustness materially depends on custom/adaptive trade management.
- `FORWARD_WATCH`: calculated only for future validation; never normal routing.
- `RESEARCH`: research-only.
- `REJECT`: excluded.
- `1M`: never normal production routing in v1.

The frozen registry currently contains **28 routable family/timeframe models**:

- 2H: 1
- 4H: 7
- 1D: 10
- 1W: 10

There are no promoted models from the new research set on 15m / 30m / 45m / 1H.

## Stock technical fingerprint

`production_scanners/fingerprint.py` combines triggered scanner evidence without directly summing historical scanner quality scores.

V1 rules:

- Tier multipliers: CORE 1.00, ACTIVE 0.85, SECONDARY 0.60.
- Timeframe multipliers: 1W 1.00, 1D 0.88, 4H 0.72, 2H 0.62.
- The strongest signal contributes its full adjusted strength.
- Additional evidence contributes at most 35% of its adjusted strength.
- Same scanner family across different timeframes is treated as highly dependent (`overlap=0.90`).
- Different families use feature-set Jaccard overlap as a transparent v1 dependence proxy.
- Redundant confirmation can still contribute a small amount, but cannot dominate the score by repetition.
- The result is capped at 100 and explicitly marked `is_probability=false`.

This overlap model is intentionally heuristic. It should later be calibrated with forward signal co-occurrence / return data instead of optimized on the same historical sample used to select the scanners.

## Signal implementation

`production_scanners/signals.py` is side-effect free. It does not write state and does not send Telegram messages. It evaluates only models that the frozen registry marks routable.

Important semantics carried from the research layer include:

- completed-bar signal generation;
- fresh crossover / false-to-true episode semantics where required;
- previous-bar volume averages that exclude the current signal bar;
- timeframe-specific trigger definitions rather than one rule reused across all timeframes;
- displayed Ichimoku Kumo semantics using the 26-bar displacement without look-ahead;
- original DMI/ADX trigger semantics for TavanTarama;
- management-dependent models remain `SECONDARY` even when their numeric historical quality score is high.

## Validation status

Contract tests cover:

- registry routing and monthly exclusion;
- exact count of the 28 routable models;
- mandatory management-dependence flag for all seven SECONDARY records;
- registry authority over caller-provided tier/quality values;
- overlap penalty behaviour;
- ignoring non-routable evidence;
- smoke evaluation of every promoted evaluator (1 + 7 + 10 + 10 models).

A local test run of the integration package passes all eight tests.

Research-code parity review has also confirmed the DMI/ADX formula and the displayed Ichimoku Kumo implementation against the corresponding `deneme` research scripts. BB-squeeze relative-percentile research uses a 120-bar window with `min_periods=60`; the current production evaluator waits for the full 120 observations. With the research workflow's normal 260-bar warm-up this does not change evaluated production bars, but exact early-warm-up parity should be normalized before live activation.

## Rollout rule

This branch is **shadow-ready, not live-ready**.

Before connecting the new layer to Telegram or replacing the legacy A-I scanner path:

1. Replay the same stored OHLCV snapshots used by research and compare signal timestamps family-by-family/timeframe-by-timeframe.
2. Investigate every material mismatch; do not tune parameters to make the outputs look better.
3. Freeze a parity report and version the registry/spec snapshot.
4. Run the new layer in shadow mode beside the existing bot and store forward signals/results.
5. Only after parity is accepted, connect CORE/ACTIVE/SECONDARY routing and the stock-level technical fingerprint to reports/Telegram.

No legacy live file is modified by this branch at the current stage.
