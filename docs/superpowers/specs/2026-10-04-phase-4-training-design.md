# Phase 4 — Training, tracking, registry, gate: design

**Status:** approved in conversation 2026-10-04 (owner). Branch `phase-4/training`.
**Authority:** `docs/architecture.md` §3.2, §5, §6; `docs/prd.md` §8 (metrics, gate), F-10; `docs/phases.md` Phase 4.

## 1. Goal and success criteria

Train a calibrated LightGBM model on the gold snapshot, track it in MLflow, register it, and promote it only when
it passes the gate — locally, through the `training_pipeline` Airflow DAG and an equivalent `make train`.

Done when:
- A first run produces a champion: registered in MLflow (`puenktlich-delay`, alias `@champion`), exported to MinIO
  `models/<version>/` with a verified SHA-256 manifest, pointer `models/_pointer.json` set, and its test Brier beats
  the baseline by the configured margin (≥ 5 % relative; Phase 3 baseline test Brier 0.1520 → ≤ 0.1444).
- A second run with no improvement is **rejected** (alias `@challenger` only, rejection recorded) and the DAG run
  ends **green**.
- `make rollback` swaps the pointer and `@champion` back to the previous version (tested in integration).
- Real numbers (challenger vs baseline, per-slice) recorded in `CHANGELOG.md` and `Phases/phase-4-training.md`.

Out of scope: S3/SSM (Phase 6), live data sync (`sync_live_silver`, Phase 7), API smoke test (Phase 5),
drift-triggered runs (`drift_watch`, Phase 9), new features (v2), hyper-parameter search beyond the small grid.

## 2. Owner decisions (2026-10-04)

| Decision | Choice |
|---|---|
| New dependencies | `lightgbm` and `mlflow-skinny` (client only, same major/minor as the 3.16.1 server) in the `training` extra + dev group. No others. |
| Champion pointer | MinIO object `models/_pointer.json` behind a `ModelPointer` interface (SSM implementation in Phase 6). |
| Entry points | `make train` (host) and the `training_pipeline` DAG call the same step functions in `dbdelay`. |
| Smoke test | Artifact smoke test: load `models/<v>/` through the loader (all checksums verified), predict test rows, compare with the evaluated predictions. API check added in Phase 5. |
| Step hand-off | The MLflow run is the shared state; XCom carries only `snapshot_id`, `run_id`, `version`. |
| Gate tie | Challenger Brier must be **strictly lower** than the champion's; equal = no improvement = rejected. §5.4 config values unchanged. |

Assumptions confirmed with the design: `sync_live_silver` is skipped until Phase 7; risk thresholds 0.20 / 0.45 move
into `configs/training.yaml` and are exported in `feature_spec.json`; gate values are those of architecture §5.4;
the model is a native LightGBM Booster saved as `model.txt` (no pickle, no MLflow pyfunc).

## 3. Components

```text
src/dbdelay/training/
  config.py      + SeedConfig/LightGBMConfig/RiskThresholds/GateConfig/RegistryConfig/ReleaseConfig
  train.py       train_lightgbm(features, labels, cfg) -> TrainResult (booster, best params, grid scores)
  calibrate.py   IsotonicCalibrator: fit(raw, y) / apply(raw) via numpy.interp / to_json / from_json
  gate.py        evaluate_gate(challenger, baseline, champion | None, cfg) -> GateDecision (pure)
  tracking.py    Tracker protocol + MlflowTracker (start/resume run, log params/metrics/dicts/files,
                 log_input, register version, set alias, tag version)
  model_card.py  render_model_card(...) -> str (markdown draft)
  pipeline.py    step functions: build_training_set, validate_snapshot, train_baseline, train_model,
                 calibrate_model, evaluate_models, register_model, run_gate, record_rejection,
                 release_model, smoke_test; run_training_pipeline() chains them for `make train`
  run_train.py   CLI: `python -m dbdelay.training.run_train` (`make train`)
src/dbdelay/registry/
  artifacts.py   ModelBundle (booster + calibrator + spec): build files, manifest + SHA-256,
                 load_bundle(store, version) (verify every checksum, fail closed), bundle.predict(df)
  pointer.py     ModelPointer protocol + ObjectStorePointer (models/_pointer.json)
  release.py     release(...) and rollback(...)
src/dbdelay/features/spec.py   + optional risk_thresholds field on FeatureSpec
pipelines/airflow/dags/training_pipeline.py   thin TaskFlow DAG
scripts/rollback.py            CLI for `make rollback`
configs/training.yaml          new sections (§4)
notebooks/03_training.ipynb    reads MLflow metrics/JSON, draws calibration + importance plots (dev only)
```

### Data flow

```text
gold/training_sets/<snapshot_id>/{train,valid,test}.parquet   (Phase 3; built or reused)
   │ fit_spec(train) + risk_thresholds → build_features per split
   ├─ baseline: BaselineModel.fit(train) ──────────────────────────────┐
   └─ LightGBM grid (8 combos), early stopping on valid ─► best booster │
         └─ isotonic fit on valid raw scores ─► calibrator            │
                                                                       ▼
   evaluate on test (same rows): challenger · baseline · champion (loaded from models/<champion>/,
                                  features built with the champion's own feature_spec)
   │
   MLflow run (experiment `puenktlich-delay`): params, metrics, JSON tables, model card, bundle files
   │ register bundle as model version N, alias @challenger
   ▼
   gate ── reject ─► version tag gate=rejected + gate.json, run tag; DAG ends green
        └─ pass ──► release: upload models/N/* → manifest.json → load_bundle(N) verify
                    → pointer {champion: N, previous: old} → alias @champion → smoke_test
```

## 4. Configuration — `configs/training.yaml` (additions)

```yaml
seed: 42
lightgbm:
  num_threads: 8              # fixed for reproducibility (deterministic=true needs a fixed thread count)
  num_boost_round: 2000
  early_stopping_rounds: 50
  params:                     # fixed for every grid point
    objective: binary
    feature_fraction: 0.9
    bagging_fraction: 0.8
    bagging_freq: 1
  grid:                       # 2 × 2 × 2 = 8 combos, selected by valid binary_logloss
    num_leaves: [31, 127]
    learning_rate: [0.05, 0.1]
    min_data_in_leaf: [100, 500]
risk_thresholds:              # prd §5: Low < 0.20 ≤ Medium < 0.45 ≤ High
  medium: 0.20
  high: 0.45
gate:                         # architecture §5.4
  min_brier_improvement_vs_baseline: 0.05   # relative
  max_brier_regression_vs_champion: 0.00    # and strictly better: tie → reject
  max_auc_drop_vs_champion: 0.005
  max_slice_auc_drop: 0.02                  # per train_type
  min_test_rows: 20000
registry:
  model_name: puenktlich-delay
  experiment: puenktlich-delay
release:
  reference_sample_rows: 50000
```

Validated by pydantic (`extra="forbid"`, ranges: thresholds in (0, 1) and `medium < high`, grid lists non-empty,
gate values ≥ 0). Bad config → `ConfigError` before any work.

## 5. Training (`training/train.py`)

- Native `lightgbm.train` on the `build_features` frame; categorical columns are pandas `category` with the levels
  frozen in the spec, passed as `categorical_feature` → LightGBM native categoricals.
- Fixed params + `seed`, `deterministic: true`, `force_row_wise: true`, `num_threads` from config, `verbose: -1`.
- For each grid combo: train with early stopping on valid (`binary_logloss`); keep best iteration and its valid
  log loss. Best combo = lowest valid log loss (ties → first in grid order). Grid scores logged.
- Output `TrainResult(booster, params, best_iteration, grid_scores)`; `booster.model_to_string(num_iteration=best)`
  is `model.txt`.
- Same data + config → identical `model.txt` (unit-tested on small synthetic data).

## 6. Calibration (`training/calibrate.py`)

- sklearn `IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1)` fitted on the booster's **valid** raw
  probabilities vs valid labels (architecture §5.2). Valid also drives early stopping; accepted (documented risk).
- Exported `calibrator.json = {"schema_version": 1, "method": "isotonic", "x": [...], "y": [...]}` from the fitted
  thresholds; `apply` = `numpy.clip(numpy.interp(raw, x, y), 0, 1)` → serving needs no sklearn.
- Test: `apply` equals `IsotonicRegression.predict` on random inputs; output monotone; JSON round-trip.

## 7. Evaluation and the gate

- `evaluate_models`: on the **same test rows**, compute `evaluate_split` (Phase 3) for
  - challenger (calibrated), baseline (refit on this snapshot's train, as in Phase 3), and
  - champion, if the pointer has one: `load_bundle(store, champion)` → `bundle.predict(test_raw)` (its own spec).
  Valid metrics for the challenger are logged too. Slices = Phase 3 `slice_frame` (train_type, hour band, …).
- `gate.evaluate_gate` returns `GateDecision(passed, checks=[Check(name, passed, value, limit, detail)])`:

| Check | Rule | Applies |
|---|---|---|
| `min_test_rows` | test rows ≥ `min_test_rows` | always |
| `beats_baseline` | challenger Brier ≤ baseline Brier × (1 − `min_brier_improvement_vs_baseline`) | always |
| `brier_vs_champion` | challenger Brier < champion Brier + `max_brier_regression_vs_champion` (strict; with 0.00 a tie fails) | champion exists |
| `auc_vs_champion` | challenger AUC ≥ champion AUC − `max_auc_drop_vs_champion` | champion exists |
| `slice_auc` | per `train_type` slice: challenger AUC ≥ reference AUC − `max_slice_auc_drop`; reference = champion, or baseline when there is no champion; slices with null AUC on either side are skipped (listed in `detail`) | always |

- Decision logged as `gate.json` (run artifact) and as tags (run + model version: `gate=passed|rejected`).

## 8. Tracking and registry (`training/tracking.py`)

- `MlflowTracker` wraps the MLflow client; tracking URI from `Settings.mlflow_tracking_uri` (Airflow:
  `http://mlflow:5000`). Artifacts go through the server's proxy (`--artifacts-destination`) → no S3 creds in clients.
- One run per pipeline execution, created in `train_model`, resumed by `run_id` in later steps.
- Logged: params (fixed + chosen grid point, best iteration, seed, window), metrics (valid/test for challenger,
  test for baseline/champion, gate inputs), `mlflow.log_input` of the snapshot (`MetaDataset`, name = snapshot id,
  digest = content hash), tags `git_sha`, `snapshot_id`, `spec_hash`, `feature_version`.
- JSON tables instead of images (no matplotlib at train time): `grid_scores.json`, `feature_importance.json`
  (gain + split), `calibration_bins.json`, `metrics.json`, `gate.json`, and `model_card.md`. Plots are drawn in
  `notebooks/03_training.ipynb`.
- Bundle files logged under the run artifact path `bundle/`; registered with
  `register_model("runs:/<run_id>/bundle", "puenktlich-delay")` → version N; alias `@challenger` → N.
- `git_sha`: env `GIT_SHA` if set, else `git rev-parse --short HEAD`, else `"unknown"`. The Airflow image gets
  `GIT_SHA` as a build arg (Makefile passes it).

## 9. Artifacts, release, pointer, rollback (`registry/`)

- `models/<N>/` (bucket `puenktlich-local` locally): `model.txt`, `calibrator.json`, `feature_spec.json`
  (incl. `risk_thresholds`), `metrics.json`, `model_card.md`, `reference_sample.parquet`, `manifest.json`.
- `manifest.json` = architecture §6 (`schema_version: 1`, model name, version, run id, git sha, snapshot id,
  trained_at UTC, train window, `files` map `"<name>": "sha256:<hex>"`, metrics `test_brier`, `test_auc`,
  `baseline_brier`). `files` covers **every** file in the folder except `manifest.json`.
- `reference_sample.parquet`: ≤ `reference_sample_rows` train rows, seeded sample, feature columns + `is_late`
  + calibrated `p_late` (drift reference for Phase 9). Deterministic bytes.
- `load_bundle(store, version)`: read manifest → download each listed file → verify SHA-256 → parse
  (`lightgbm.Booster(model_str=…)`, calibrator, spec). Any missing file, mismatch, or unlisted file →
  `ArtifactIntegrityError` (new, in `errors.py`). `ModelBundle.predict(df)` = `build_features` → booster raw →
  calibrator.
- `ObjectStorePointer`: `models/_pointer.json = {"champion_version", "previous_version", "updated_at"}`; `get()`
  returns `None` when absent; `set(champion, previous)` overwrites the object (no delete — storage has none).
- `release(N)`: if `models/N/manifest.json` exists with identical hashes → reuse (idempotent rerun); if it exists
  with different hashes → error. Order: upload files → upload manifest → `load_bundle(N)` (verify) → pointer
  `{champion: N, previous: old champion}` → alias `@champion` → N. Pointer is the source of truth for serving;
  a failure after the pointer write is fixed by re-running release (idempotent).
- `rollback()`: needs a `previous_version` whose bundle verifies; sets pointer `{champion: previous,
  previous: current}` and alias `@champion` → previous. `make rollback` → `scripts/rollback.py` (prints old/new
  versions; no AWS).
- `smoke_test(N)`: `load_bundle(N)` → predict the first 1,000 test rows → max abs difference vs the evaluated
  challenger predictions ≤ 1e-9, and every `p_late` in [0, 1].

## 10. Orchestration

- `make train` → `python -m dbdelay.training.run_train [--config …]` → `run_training_pipeline()` runs every step
  in-process and logs one summary line (versions, gate result, test Brier/AUC — no row values).
- DAG `training_pipeline` (`pipelines/airflow/dags/training_pipeline.py`, TaskFlow, thin):
  `build_training_set → validate_snapshot → [train_baseline, train_lightgbm] → calibrate → evaluate → register →
  gate (@task.branch) → record_rejection | (release → smoke_test)`. Schedule `@monthly`, `catchup=False`,
  `max_active_runs=1`, retries 1 for I/O steps, `execution_timeout` on training. Rejection path ends green.
- `validate_snapshot`: Pandera `SilverDepartures` on each split + non-empty splits + split date ranges inside
  the manifest windows.
- `train_baseline` writes nothing new (Phase 3 artifacts are reused or rebuilt); its metrics are part of
  `evaluate`. XCom: `snapshot_id`, `run_id`, `version`, gate result.
- Airflow image: install `/opt/dbdelay[pipelines,training]` under the constraints file (drop conflicting pins
  only if needed, documented like pandas); add `libgomp1` if the LightGBM wheel needs it; `GIT_SHA` build arg;
  env `MLFLOW_TRACKING_URI=http://mlflow:5000`.
- New Makefile targets: `train`, `rollback`, `train-dag` (unpause + trigger `training_pipeline`).

## 11. Testing

- **Unit** (no network): config validation (each new section, bad values → `ConfigError`); train (deterministic
  `model.txt` for the same seed, grid selection picks lowest valid log loss, categorical handling, early stopping
  used); calibrate (equals sklearn, monotone, clip, JSON round-trip); gate (table-driven: each rule pass/fail,
  tie rejected, first run without champion, null slices skipped); artifacts (manifest hashes, tampered file →
  error, missing/unlisted file → error, round-trip predict) on moto S3; pointer (absent → None, set/get);
  release (order, idempotent rerun, conflicting hashes → error) and rollback (no previous → error) with a fake
  tracker; model card renders required sections; pipeline steps with a fake tracker on a tiny snapshot.
- **Integration** (compose stack: MinIO + MLflow): synthetic snapshot with learnable signal. Run 1 with a
  deliberately weak config (e.g. `num_boost_round: 5`) → champion v1; run 2 with the normal config → better →
  v2 released (pointer previous = v1); run 3 identical to run 2 → rejected, pointer unchanged; `rollback` →
  pointer and alias back to v1. Unique experiment/model names and root prefix per test run.
- **DAG**: `pipelines/airflow/tests/check_dags.py` parses `training_pipeline` (task ids + dependencies);
  `make test-dags`.
- **Real run**: `make train` on snapshot `2026-08-31_38b45c7a`, then the DAG twice → record measured numbers only.
- Gates: `make check`, `make test-integration`, `uv run pre-commit run --all-files`.

## 12. Risks

- **Margin may not be reached:** timetable-only features might not beat the baseline Brier by 5 %. Then the run is
  rejected; I report measured numbers and stop — no threshold change without the owner.
- Valid is used for early stopping, grid selection and calibration → mildly optimistic valid metrics; test stays
  untouched for the gate.
- `month` feature: train covers Dec–Jul only; Sep–Nov months are unseen (carried from Phase 3). LightGBM treats
  `month` as numeric → extrapolates flat; noted in the model card limitations.
- Snapshot id hashes parquet bytes incl. library versions → the Airflow image may compute a different id than the
  host for the same silver. Accepted for Phase 4 (documented); each run logs its own snapshot id.
- Airflow constraints vs `mlflow-skinny`/`lightgbm` pins could conflict → resolved like pandas in Phase 2,
  documented in the Dockerfile.
- PC sleep kills long Airflow tasks (Phase 2 lesson) → keep the PC awake during the real DAG runs.
- `min_count` thresholds stay at Phase 3 defaults; tuning is out of scope unless the margin is missed.
