# Model card — `puenktlich-delay` v7 (champion since 2026-11-19)

Source of every number: the release bundle `models/7/` (`manifest.json`, `metrics.json`,
`model_card.md`) and the daily monitor (`GET /api/v1/monitoring`, read 2026-11-26).
Each release writes its own card into its bundle; this page describes the current champion.

## What it does

- **Predicts** the probability that a Deutsche Bahn departure at one of the 30 supported stations
  leaves **≥ 6 minutes late** (`p_late`), for departures in the next 3 hours.
- **Shown as** a risk badge: Low < 0.20 ≤ Medium < 0.45 ≤ High (calibrated `p_late`).
- **For** riders deciding how much buffer to plan. **Not for** operational, safety or compensation
  decisions.

## Model

| | |
|---|---|
| Algorithm | LightGBM binary classifier + isotonic calibration |
| Inputs (11) | station (`eva`), train type, line, destination, stop index, hour, minute of day, weekday, weekend, month, public holiday — timetable and calendar only, nothing live |
| Parameters | `num_leaves=127`, `learning_rate=0.05`, `min_data_in_leaf=100`, `best_iteration=846` |
| Label | `is_late = delay ≥ 6 min`; cancelled departures are excluded, not predicted |
| Lineage | MLflow run `96fda72da67b4b9e8cc55d1fcfd40ce9`, git `44471e0`, snapshot `2026-11-18_c0373622` |
| Integrity | SHA-256 for every file in `manifest.json`, checked before every load |

## Data

- **Train window:** 2026-02-19 → 2026-10-21 (HF history + our own live data; live wins on days both
  have). Valid = the 14 days after, test = the last 14 days (4–18 Nov 2026), 150,437 test rows.
- **Release path:** drift flag on 2026-11-19 → challenger v6 rejected by the slice rule → owner
  relaxed the slice rule (judge only train types with ≥ 2,000 test rows) → v7 trained and released.

## Test results (4–18 Nov 2026, same rows for all three)

| Metric | v7 | Baseline (late-rate lookup) | Previous champion v1 |
|---|---|---|---|
| Brier (lower is better) | **0.1500** | 0.1652 | 0.1586 |
| ROC-AUC | **0.7996** | 0.7481 | 0.7805 |
| PR-AUC | **0.5992** | 0.5275 | 0.5630 |
| ECE (calibration error) | **0.0158** | 0.0247 | 0.0469 |

**By train type (ROC-AUC, test):** best on long-distance and regional express (IC 0.84, RJ 0.84,
VLX 0.82, VIA 0.82, TR 0.81, NJ 0.81, NWB 0.80), mid-range on the large groups (ICE 0.75, RE 0.76,
S 0.76, RB 0.74). Weakest: **NBE 0.45** (worse than random; a small slice, listed but not judged by
the gate), EB 0.63, AVG 0.63, WFB 0.63, R 0.63. Types with fewer than 500 test rows (or only one outcome) get no number.
The full table is in `models/7/model_card.md`.

## Live results (daily monitor, all departures of the previous day)

| Day | Rows | Brier | ROC-AUC | ECE | Actual late | Predicted (mean) | Brier ÷ test Brier |
|---|---|---|---|---|---|---|---|
| 2026-11-19 | 13,574 | 0.1606 | 0.8102 | 0.0258 | 32.1 % | 30.4 % | 1.07 |
| 2026-11-20 | 13,534 | 0.1589 | 0.7979 | 0.0149 | 30.6 % | 30.8 % | 1.06 |
| 2026-11-21 | 12,246 | 0.1323 | 0.7744 | 0.0445 | 19.2 % | 23.5 % | 0.88 |
| 2026-11-22 | 11,624 | 0.1174 | 0.7897 | 0.0256 | 17.2 % | 19.5 % | 0.78 |
| 2026-11-23 | 13,334 | 0.1553 | 0.7922 | 0.0185 | 27.9 % | 27.3 % | 1.04 |
| 2026-11-24 | 13,249 | 0.1671 | 0.7907 | 0.0483 | 32.0 % | 27.2 % | 1.11 |
| 2026-11-25 | 13,293 | 0.1633 | 0.8020 | 0.0369 | 31.8 % | 28.2 % | 1.09 |

- 90,854 live departures over 7 days; ROC-AUC 0.77–0.81, close to the test result.
- The accuracy limit is Brier ÷ test Brier ≤ 1.10 on 3 days in a row. Only 24 Nov was above it (1.11),
  so no retrain was requested.
- **Pattern:** on bad days (≈ 32 % late) the model predicts too low (27–28 %); on quiet weekend days
  (21–22 Nov) it predicts too high. It follows each departure's usual risk, not the day's conditions.

## Limitations

- **Timetable and calendar only:** no weather, incidents, live delays upstream or construction work.
  A disrupted day is predicted like a normal one (see the pattern above).
- **Months never seen in training:** November, December, January — the `month` input is extrapolated
  for exactly the months it is serving now.
- **History may undercount lateness:** the HF dataset polls delay changes about 5 times a day;
  our live data shows later delays than HF in 95 % of differing rows (Phase 7 reconciliation).
- **Small train types are not judged** by the gate (< 2,000 test rows); NBE ranks worse than random.
- The valid split drives early stopping, grid selection and calibration; only the test split is
  untouched.

## Monitoring and retraining

Every night the monitor scores all of yesterday's departures. Data drift (share of drifted inputs
> 0.30) or accuracy (Brier ratio > 1.10) on 3 days in a row with the same model writes a retrain flag
and e-mails the owner; a challenger replaces v7 only if it passes the gate (Brier, AUC, calibration,
slices). See `architecture.md` §5 and §9, `docs/runbook.md`.
