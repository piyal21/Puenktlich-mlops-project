# ADR-0003: Drift tests with scipy instead of the Evidently library

**Status:** Accepted (Claude under the owner's standing OK for non-cost decisions, 2026-11-18; owner may veto)
**Date:** 2026-11-18
**Evidence:** container measurement (spec `docs/superpowers/specs/2026-11-18-phase-9-monitoring-design.md` §9),
`services/api/requirements.txt`, Lambda limits in ADR 0002.

## Context

`rules.md` named Evidently (≥ 0.7) as the drift tool. Phase 9 needs a daily monitor that scores
yesterday's departures with the champion. Scoring needs LightGBM, which only the **API image** has, so
the monitor runs on that image. This account caps Lambda memory at **512 MB** (ADR 0002), and the same
image serves the public API, whose cold start is already 6.8 s at the origin (Phase 6).

Measured 2026-11-18 (python:3.12-slim, linux/amd64, `evidently` 0.7.23 + pandas 2.3.3 + lightgbm):

| | Evidently |
|---|---|
| Extra packages | litestar, uvicorn, nltk, plotly 5.24.1, scikit-learn 1.9.1, statsmodels 0.15.0, … |
| `import evidently` | **+177 MB RSS** (121 → 298 MB), **7.7 s** |
| `DataDriftPreset`, 14 k current / 50 k reference rows | 2.7 s, peak 344 MB |
| Tests it chose for this size | Wasserstein distance (normed) for numeric, Jensen–Shannon for categorical |

scipy 1.18.1 is already in the API image (a LightGBM dependency).

## Decision

Compute Evidently's own default large-sample tests with scipy in `dbdelay.monitoring.drift`:
`scipy.stats.wasserstein_distance / max(std(reference), 0.001)` for numeric inputs and
`scipy.spatial.distance.jensenshannon(base=2)` for categorical inputs; drifted when the score ≥ 0.1.
`rules.md` §2 now names this; Evidently stays out of every Lambda image.

## Consequences

- No new dependency, no larger image, no extra cold-start time for public users.
- Same statistics as Evidently's defaults, but no Evidently HTML report: the health page draws its own
  charts and a per-input drift table from the JSON results.
- If Evidently's defaults change, ours do not follow automatically (unit tests pin our formulas to scipy).
- Evidently can still be used in notebooks or locally for deeper investigations.
