# ADR-0001: Label and leakage policy

**Status:** Proposed (awaiting owner approval at the end of Phase 1)
**Date:** 2026-09-25
**Deciders:** MD Piyal Ahmmed (owner)
**Evidence:** `notebooks/01_eda.ipynb` (HF months 2025-10, 2026-03, 2026-08; live API samples 2026-09-25)

## Context

Pünktlich predicts, for a planned departure at one of 30 stations, the probability that it departs late.
Everything downstream — silver schema, features, metrics, the promotion gate and the UI — depends on
(1) what "late" means, (2) what happens to cancelled departures and (3) which information the model may use.
These must be frozen before the ETL (Phase 2) is built, and they must be identical for the historical
(HF) and live (DB API) sources.

What the EDA showed:
- `delay_in_min` in HF is exactly `departure_change_time − departure_planned_time`. The change time is
  **always filled**: when DB sent no change message, the planned time is used, so the delay is 0
  (~37 % of departures are exactly 0).
- At the 30 stations, 22.5–26.3 % of non-cancelled departures are ≥ 6 min late (per month); 3.6–4.2 % are cancelled.
- Cancelled rows **still carry a delay value** (the last known change), mean 11 min.
- Timestamps are naive Europe/Berlin local time. The repeated autumn DST hour exists in the data
  (1.39× volume across 131 stations) and cannot be disambiguated reliably.
- Train type, station, hour and weekday shift the late rate strongly (e.g. ICE 46 % vs S-Bahn 16 %;
  stations 3 %–43 %) → timetable-only features carry real signal.

## Decision

1. **Label:** `is_late = delay_min >= 6` for the **departure** at the station, where
   `delay_min = changed_departure − planned_departure` in whole minutes. 6 minutes is DB's official
   punctuality threshold. Early departures (negative delay) are not late.
2. **No change information ⇒ on time.** If a source has no changed departure time, `changed = planned`
   and `delay_min = 0`. The live ETL must apply exactly this rule to stay consistent with HF.
3. **Cancelled departures are excluded from the label:** `is_cancelled = true` ⇒ `delay_min = null` and
   `is_late = null`. They stay in silver (for monitoring and the UI) but are never used to train or
   evaluate the model.
4. **Rows without a planned departure** (terminating trains) are dropped in silver.
5. **Unrepresentable local times** (ambiguous autumn hour, nonexistent spring hour) become `NaT`, are dropped
   and counted in the quality report — not guessed.
6. **Leakage policy:** features may use only information known from the timetable before departure
   (station, train type, line, destination, stop index, planned local time, calendar, public holidays).
   **Never** `changed_departure_utc`, `delay_min`, `is_cancelled`, arrival delays, change messages or anything
   derived from the outcome. Category levels and rare-level grouping are computed on the train split only.
   Any future live-context feature must be computed as of `planned_departure − 60 min` and pass a
   feature-parity test first (docs/rules.md §5.1).
7. The silver contract enforces 1–3 (`dbdelay.data.schemas.SilverDepartures`: `cancelled_has_no_delay_or_label`,
   `label_matches_delay`, `delay_matches_times` — the last one requires a changed time on every non-cancelled
   row and `delay_min == changed − planned`); the threshold is the constant `LATE_THRESHOLD_MIN = 6`.

## Options considered

### Label threshold

| Option | Assessment |
|---|---|
| **A. ≥ 6 min, binary (chosen)** | Matches DB's published punctuality metric and rider intuition; ≈ 25 % positives at the 30 stations (22.5–26.3 % per month) — balanced enough for calibration and Brier. |
| B. ≥ 3 min or ≥ 16 min, binary | 3 min: 36–40 % positives, but small delays are mostly dwell-time noise riders don't care about. 16 min: 9–12 % positives, fewer positives per station/hour cell; neither is a standard DB metric. |
| C. Regress delay minutes | Heavy-tailed target (p99 ≈ 60–70 min, max ≈ 24 h), harder to calibrate and explain; riders ask "will it be late?", not "how many minutes?". |

### Cancelled departures

| Option | Assessment |
|---|---|
| **A. Exclude from label (chosen)** | Label stays "late given it runs"; cancellations are shown separately in the UI. Keeps positives meaningful. |
| B. Count as late | Mixes two different events; cancellation drivers (strikes, construction) differ from delay drivers; inflates positives by ~4 pp. |
| C. Third class | Multiclass + calibration for a 4 % class adds complexity for little rider value in v1. |

### Leakage / feature scope

| Option | Assessment |
|---|---|
| **A. Timetable + calendar only in v1 (chosen)** | Zero training/serving skew by construction; EDA shows real signal. |
| B. Add live context (recent delays at the station) | Likely more accurate, but needs as-of joins, history at serving time and a parity test → stretch goal. |

## Trade-off analysis

We trade some accuracy (no live context, no cancellation risk) for a label that is standard, simple to
explain, identical across both sources and impossible to leak. Rule 2 ("no change ⇒ on time") is the one
assumption we inherit from the HF processing; a departure whose delay was never reported is recorded as on
time. This slightly under-counts lateness, but both sources do it the same way, so train and serve stay
consistent, and the Phase 7 reconciliation test will measure the effect on overlapping days.

## Consequences

- Easier: one boolean target, Brier/ECE/AUC directly meaningful; the contract check catches label bugs
  in either ETL.
- Harder: cancellation risk is not predicted (UI shows cancellations only once DB reports them).
- Revisit: the threshold only with a schema version bump; rule 2 after the Phase 7 reconciliation;
  live-context features in the stretch phase.

## Action items

1. [x] Encode rules 1–3 in the silver contract (`src/dbdelay/data/schemas.py`) with tests.
2. [ ] Phase 2: HF conform — drop rows without planned departure, cancelled ⇒ null delay/label,
   DST `NaT` drop + count.
3. [ ] Phase 3: leakage guard test — no forbidden column in the feature frame.
4. [ ] Phase 7: live ETL applies rule 2; reconciliation test against HF on overlapping days.
