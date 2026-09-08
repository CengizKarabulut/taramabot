# Production Scanner Registry Integration v1

Date: 2026-09-08
Branch: `integration/production-registry-v1`
Research source: `CengizKarabulut/deneme`

## Purpose

This layer moves the frozen scanner research decisions into `taramabot` without changing the current A-I / KARAR live flow yet.

The integration deliberately separates two sources of truth:

1. **Common promotion registry** decides whether a family/timeframe is `CORE`, `ACTIVE`, `SECONDARY`, `FORWARD_WATCH`, `RESEARCH` or `REJECT`.
2. **Locked family research spec / implementation** defines the actual completed-bar entry semantics for that family/timeframe.

A scanner quality score is a historical ranking aid, not a predicted win probability.

## Routing policy

Normal production routing is limited to `CORE`, `ACTIVE` and `SECONDARY`.

- `CORE`: primary production evidence.
- `ACTIVE`: normal production evidence.
- `SECONDARY`: supporting / early-warning evidence. It receives a lower stock-confidence weight because the historical robustness materially depends on custom/adaptive trade management.
- `FORWARD_WATCH`: future validation only; never normal routing.
- `RESEARCH`: research-only.
- `REJECT`: excluded.
- `1M`: never normal production routing in v1.

The frozen registry contains **28 routable family/timeframe models**:

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

The production layer is deliberately split in two:

- `production_scanners/history.py` is the authoritative locked entry-event engine used for historical parity and live/shadow trigger identity.
- `production_scanners/live.py` takes the real `triggered` flag from the locked event engine while preserving rich diagnostic components/values from `signals.py`.
- `production_scanners/signals.py` remains side-effect free and provides the human-readable diagnostic decomposition. It does not write state and does not send Telegram messages.

Important semantics carried from research include:

- completed-bar signal generation;
- fresh crossover / full-condition False-to-True episode semantics where required;
- timeframe-specific trigger definitions rather than one rule reused across all timeframes;
- displayed Ichimoku Kumo semantics using the 26-bar displacement without look-ahead;
- original DMI/ADX trigger semantics for TavanTarama;
- BB-squeeze `relative20` uses the research implementation's 120-bar reference with `min_periods=60`;
- family-specific volume semantics are preserved exactly. Most RVOL/volume filters exclude the current bar where their locked research specifies that behaviour. `NE ARARSAN VAR` intentionally retains its original research lineage's current-inclusive 20-bar RVOL denominator because the historical production decision was measured with that implementation;
- management-dependent models remain `SECONDARY` even when their numeric historical quality score is high.

## Entry parity — FROZEN PASS

The research-to-production entry parity gate is complete.

Reference research commit:

`5a25fbfb497f5b0a4a1ba8e527d24c77cb7d2aee`

Data source:

latest non-expired `historical-live-*` SQLite artifacts from `taramabot`, i.e. the same persistent historical datasets consumed by the research workflows.

Final result:

| Timeframe | Production models | Exact match | Status |
|---|---:|---:|---|
| 2H | 1 | 1 | PASS |
| 4H | 7 | 7 | PASS |
| 1D | 10 | 10 | PASS |
| 1W | 10 | 10 | PASS |
| **Total** | **28** | **28** | **PASS** |

A model passes only when there are **zero production-only events and zero research-only events** after that research module's own warm-up. The final historical bar is excluded because the frozen research execution reference is next-bar open.

The first real-data comparison found 26/28 exact models. Both mismatches were `NE ARARSAN VAR` (1D and 1W). The parity investigation identified an implementation-semantic difference: its research lineage used a current-inclusive RVOL20 denominator and its own RSI edge-case behaviour. Production was corrected to reproduce the actual measured research semantics rather than changing/tuning the strategy. The next run produced 28/28 exact parity.

Machine/human parity reports are frozen under:

`reports/production-parity-v1/`

## Validation status

Contract tests cover:

- registry routing and monthly exclusion;
- exact count of the 28 routable models;
- mandatory management-dependence flag for all seven SECONDARY records;
- registry authority over caller-provided tier/quality values;
- overlap penalty behaviour;
- ignoring non-routable evidence;
- smoke evaluation of every promoted evaluator (1 + 7 + 10 + 10 models).

The GitHub parity workflow compiles the production layer and passes all eight contract tests before it is allowed to read the historical artifacts.

## Remaining gates before live routing

This branch remains **shadow-ready, not live-routed**. Entry identity is now frozen and accepted, but entry parity alone is not enough for safe activation because several models — especially `SECONDARY` — derive their robustness from custom risk/exit management.

The remaining rollout order is:

1. Build and verify **exit / risk-management parity** against each locked research profile (initial stop, ATR/swing logic, partial targets, sticky tighten rules, technical hard exits, max-hold and same-bar STOP_FIRST semantics).
2. Freeze an exit-parity report; no parameter retuning is allowed during parity correction.
3. Run the 28-model layer in **shadow mode** beside the current bot and persist forward signal/result observations.
4. Use those forward observations to validate/calibrate the stock-level technical fingerprint and overlap assumptions without re-optimizing on the historical selection sample.
5. Only after those gates are accepted should CORE/ACTIVE/SECONDARY routing and technical fingerprint output be connected to reports/Telegram.

No legacy live file has been modified by this integration branch.
