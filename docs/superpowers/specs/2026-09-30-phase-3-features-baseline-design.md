# Phase 3 — Features & baseline: design

**Status:** approved in conversation 2026-09-30 (owner). Branch `phase-3/features-baseline`.
**Authority:** `docs/architecture.md` §3.4, §4, §5.3; ADR 0001 (label and leakage policy); `docs/phases.md` Phase 3.

## 1. Goal and success criteria

One feature module used everywhere, a reproducible time-based training snapshot, and a baseline to beat.

Done when:
- `make baseline` builds (or reuses) a gold snapshot from the 9 silver months, fits the feature spec and the baseline
  on train, and writes metrics for valid and test.
- The feature builder is deterministic, fully unit-tested, and a leakage-guard test fails if a forbidden column
  could influence the features.
- Building the snapshot twice from the same silver gives the same `snapshot_id` and identical bytes.
- Real baseline metrics on the test split are recorded in `CHANGELOG.md` and `Phases/phase-3-features-baseline.md`.

Out of scope (Phase 4): LightGBM, MLflow logging, the Airflow `training_pipeline` DAG, calibration, the promotion gate.

## 2. Owner decisions

| Decision | Choice |
|---|---|
| New dependencies | `holidays` (main deps; the API needs it later) and `scikit-learn` (new `training` extra + dev group; kept out of the Lambda image). No others. |
| Source-data gaps | **Excluded from all splits** and counted in `snapshot.json`. |
| Snapshot build | Download the silver day files of the window to a temp dir, query with DuckDB (same pattern as Phase 2; no `httpfs`). |
| Snapshot content | Cleaned silver rows per split, **no features**. Features are built at train time with a spec fitted on train only. |

## 3. Components

```text
src/dbdelay/features/
  calendar.py   Berlin local time parts; public holidays (national + state) via `holidays`
  spec.py       FeatureSpec (pydantic, JSON) + fit_spec(train_df, stations, cfg)
  build.py      build_features(df, spec) -> DataFrame   ← the only place features are made
src/dbdelay/training/
  config.py     TrainingConfig (pydantic) loaded from configs/training.yaml
  split.py      windows + build_snapshot(store, cfg, workdir) -> SnapshotManifest
  baseline.py   BaselineModel: fit / predict / to_json / from_json
  evaluate.py   evaluate(y_true, y_prob, slices) -> EvaluationReport
  run_baseline.py  CLI: `python -m dbdelay.training.run_baseline` (`make baseline`)
configs/training.yaml
notebooks/02_baseline.ipynb   reads metrics.json, draws the calibration plot (matplotlib, dev only)
```

### Data flow

```text
silver/departures/source=hf/date=*/part-0.parquet  (days in window)   silver/_quality/source=hf/month=*.json
        │ download to temp dir                                                │ gap hours + station-days
        ▼                                                                     ▼
DuckDB: window filter → drop unlabelled (cancelled) rows → drop gap rows → assign split by UTC date
        │  deterministic parquet (fixed arrow schema = silver schema, sorted by event_id, zstd)
        ▼
gold/training_sets/<end-date>_<hash8>/{train,valid,test}.parquet + snapshot.json
        │
        ├─ fit_spec(train) ─► feature_spec.json
        ├─ build_features(split, spec) for train / valid / test
        ├─ BaselineModel.fit(train features, train labels) ─► baseline.json
        └─ evaluate on valid and test ─► metrics.json
             all three under gold/training_sets/<id>/baseline/
```

## 4. Configuration — `configs/training.yaml`

```yaml
window_months: 9          # architecture §5.3
end_date: null            # null = latest silver day present; recorded in snapshot.json
test_days: 14
valid_days: 14
exclude_data_gaps: true
features:
  min_count: 200          # category levels with fewer train rows → OTHER (initial default, not tuned)
baseline:
  min_count: 50           # a lookup group needs this many train rows, else back off (initial default)
evaluation:
  ece_bins: 10
  slice_min_rows: 500     # smaller slices report null metrics
```

`TrainingConfig` validates it (positive ints, `end_date` ISO date or null). A bad file → `ConfigError`.

## 5. Snapshot (`training/split.py`)

- **Window:** `end = cfg.end_date or latest silver day`; `start = end − window_months months + 1 day`
  (end 2026-08-31, 9 months → start 2025-12-01). UTC dates of `planned_departure_utc`.
- **Splits (UTC date):** test = last `test_days` days, valid = the `valid_days` before, train = the rest.
  No shuffling, no overlap.
- **Rows kept:** `is_late` not null (cancelled rows have no label per ADR 0001 → excluded and counted).
- **Gap exclusion** (when `exclude_data_gaps`), using the monthly quality reports of every month in the window:
  - `low_volume_hours` (`YYYY-MM-DDTHH`, Berlin local hour, all stations): drop rows whose local planned hour matches.
  - `stations[].volume_drop_days` (UTC date per `eva`): drop that station's rows on that date.
- **Errors:** a missing silver day file or quality report in the window, a window longer than the data, or an
  empty split → `DataValidationError` (no silent fallback).
- **Output:** columns = silver columns (unchanged schema), sorted by `event_id`, written with the silver arrow schema.
  `content_hash` = sha256 over the three parquet files' bytes in order train, valid, test;
  `snapshot_id = f"{end:%Y-%m-%d}_{content_hash[:8]}"`.
- **`snapshot.json`** (`SnapshotManifest`): snapshot_id, content_hash, window start/end, split date ranges,
  rows per split, late rate per split, rows excluded (cancelled, gap_hours, gap_station_days), silver days used,
  quality months used, config values used.
- **Idempotent:** if `gold/training_sets/<id>/snapshot.json` already exists, nothing is uploaded again.

## 6. Features (`features/`)

Input: any frame with `eva, train_type, line_number, train_number, final_destination, stop_index,
planned_departure_utc` (extra columns ignored; missing required → `DataValidationError`).
Output columns, fixed order (architecture §4 v1):

| Feature | dtype | Rule |
|---|---|---|
| `eva` | category | frozen levels + `OTHER` |
| `train_type` | category | frozen levels + `OTHER` |
| `line_key` | category | `f"{train_type}:{line_number}"`, null line → `f"{train_type}:none"`; rare → `OTHER` |
| `destination_key` | category | `final_destination`; rare → `OTHER` |
| `stop_index` | int16 | as is |
| `hour_local` | int8 | Europe/Berlin hour of planned departure |
| `minute_of_day` | int16 | Berlin local |
| `weekday` | int8 | 0 = Monday, Berlin local date |
| `is_weekend` | bool | weekday ≥ 5 |
| `month` | int8 | Berlin local month |
| `is_public_holiday` | bool | `holidays` DE national + the station's state (`subdiv`), Berlin local date |

- **FeatureSpec** (`feature_spec.json`): version, feature names + dtypes, category levels per categorical
  (sorted, `OTHER` last), `min_count`, `station_states` (eva → state, frozen from `stations.yaml` so serving needs
  only the spec), spec hash.
- `fit_spec` uses **train rows only**. Levels with < `min_count` train rows map to `OTHER`; unseen levels at
  inference map to `OTHER` (never an error). Unknown station state → holidays from national list only.
- **Forbidden columns** (never read by `build_features`): `changed_departure_utc`, `delay_min`, `is_cancelled`,
  `is_late`, `event_id`, `ride_id`, `ingested_at`, `source`.

## 7. Baseline (`training/baseline.py`)

- Late-rate lookup over back-off levels, most specific first:
  `(eva, train_type, hour_local, weekday)` → `(eva, train_type, hour_local)` → `(eva, train_type)` →
  `(train_type,)` → global.
- A prediction uses the first level whose group has ≥ `baseline.min_count` train rows.
- Keys use the **feature** values (so `OTHER` groups are shared) — consumes `build_features` output.
- Serialized to `baseline.json` (levels, per-group counts and late counts, global rate); `from_json` restores it
  exactly (predictions identical).

## 8. Evaluation (`training/evaluate.py`)

Per split (valid, test): `n`, base rate, Brier, ROC-AUC, PR-AUC (average precision), log loss (probabilities
clipped to [1e-6, 1 − 1e-6]), ECE (`ece_bins` equal-width bins, weighted by bin size), calibration-curve points
(bin mean prediction, observed rate, count).
Slices: `train_type`, `eva`, `hour_band` (00–05, 06–09, 10–15, 16–19, 20–23). A slice with < `slice_min_rows`
rows or only one class reports `null` metrics with its `n`. Metrics use scikit-learn; ECE and bins are our code.
Output `metrics.json` (`EvaluationReport` per split + snapshot_id, spec hash, config used).

## 9. CLI — `make baseline`

`python -m dbdelay.training.run_baseline [--config configs/training.yaml]`: build or reuse the snapshot → fit spec →
build features → fit baseline → evaluate valid + test → write the three JSON files → log a one-line summary per
split (no row values in logs).

## 10. Testing

- **Leakage guard:** output columns of `build_features` ∩ forbidden = ∅, and scrambling every forbidden column
  leaves the feature frame byte-identical.
- **Unit:** calendar (DST spring/autumn local hours, national vs state holiday, e.g. a state-only holiday is true
  only for that state); spec (rare → OTHER, unseen → OTHER, fit uses only given rows, JSON round-trip);
  build (column order and dtypes, missing column error, deterministic); split (window math, boundaries, no overlap,
  cancelled excluded, gap hour and gap station-day excluded and counted, missing report/day → error, empty split →
  error, same input → same id); baseline (each back-off level, JSON round-trip); evaluate (metrics against
  hand-computed values, ECE bins, small/one-class slice → null); config (bad values → `ConfigError`).
- **Integration (MinIO):** synthetic silver days + quality reports → `build_snapshot` twice → same id and bytes;
  `run_baseline` end to end writes the three JSON files.
- **Real run:** 9 months → record snapshot id, rows per split, exclusions and baseline test metrics (measured only).
- Gates: `make check` (ruff, format, mypy `--strict`, tests, coverage ≥ project threshold), `make test-integration`.

## 11. Risks

- Test window (2026-08-18…31) loses the flagged 2026-08-27/28 morning hours — counted, and visible in `snapshot.json`.
- 2026-08 is the window edge (`edge_complete.next = false`): the last UTC hours of 2026-08-31 may miss rows.
- `min_count` defaults are initial choices, not tuned; Phase 4 revisits them.
