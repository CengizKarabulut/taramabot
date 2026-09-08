# Scanner Production Parity v1 — Summary

**Overall status:** FAIL
**Exact models:** 26/28
**Research SHA:** `5a25fbfb497f5b0a4a1ba8e527d24c77cb7d2aee`
**Data source:** latest non-expired `historical-live-*` SQLite artifacts from `taramabot`.

| Timeframe | Models | Exact | Status |
|---|---:|---:|---|
| 2H | 1 | 1 | PASS |
| 4H | 7 | 7 | PASS |
| 1D | 10 | 9 | FAIL |
| 1W | 10 | 9 | FAIL |

## Models requiring correction

- **NE ARARSAN VAR / 1D** — FP 840, FN 218, agreement 93.034%
- **NE ARARSAN VAR / 1W** — FP 369, FN 98, agreement 93.108%

A model passes only when both production-only and research-only event counts are zero after the research warm-up.
This gate compares entry event identity only; exit/risk-management parity is a separate gate before live activation.
