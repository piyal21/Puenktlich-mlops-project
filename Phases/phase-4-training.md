# Phase 4 — Training, tracking, registry, gate

**Goal:** train a real model on the Phase 3 snapshot, record everything about it in MLflow, and release it
only when it is clearly better than what we already have, with a one-step way back (rollback).

**Status:** ✅ done on branch `phase-4/training`, merged into `main` (owner instruction 2026-10-04). The branch is kept.

---

## 1. What was built

| Deliverable | File(s) | What it is |
|---|---|---|
| Settings | [`configs/training.yaml`](../configs/training.yaml), [`src/dbdelay/training/config.py`](../src/dbdelay/training/config.py) | New sections: seed, LightGBM grid, risk thresholds (0.20 / 0.45), gate rules, model name, reference-sample size. Typos fail before any work starts. |
| Model training | [`src/dbdelay/training/train.py`](../src/dbdelay/training/train.py) | LightGBM with native categorical features. Tries 8 settings (a small grid), stops each one early on the valid split and keeps the best. Same data + seed → byte-identical model. |
| Calibration | [`src/dbdelay/training/calibrate.py`](../src/dbdelay/training/calibrate.py) | Turns raw model scores into honest probabilities (isotonic regression fitted on valid). Saved as two number lists in `calibrator.json`; applying it needs only numpy. |
| Gate | [`src/dbdelay/training/gate.py`](../src/dbdelay/training/gate.py) | Five checks: enough test rows, ≥ 5 % better Brier than the baseline, strictly better Brier than the current champion, AUC not worse than the champion by > 0.005, no train type losing > 0.02 AUC. |
| Experiment tracking | [`src/dbdelay/training/tracking.py`](../src/dbdelay/training/tracking.py) | A small interface over MLflow (runs, files, registry, aliases). Unit tests use an in-memory fake. |
| Model card | [`src/dbdelay/training/model_card.py`](../src/dbdelay/training/model_card.py) | Markdown: intended use, data window, metrics vs baseline/champion, per-train-type AUC, risk levels, limitations. |
| Pipeline steps | [`src/dbdelay/training/pipeline.py`](../src/dbdelay/training/pipeline.py), [`run_train.py`](../src/dbdelay/training/run_train.py), `make train` | Each step is one function. `make train` runs them in one process; the DAG runs the same functions as tasks. |
| Model bundle | [`src/dbdelay/registry/artifacts.py`](../src/dbdelay/registry/artifacts.py) | `models/<version>/` with six files and a `manifest.json` listing a SHA-256 for each. The loader checks every hash and refuses to load anything that does not match. |
| Pointer, release, rollback | [`src/dbdelay/registry/pointer.py`](../src/dbdelay/registry/pointer.py), [`release.py`](../src/dbdelay/registry/release.py), [`scripts/rollback.py`](../scripts/rollback.py), `make rollback` | `models/_pointer.json` says which version is live (and the one before). Release uploads, verifies, moves the pointer, then the MLflow alias. Rollback swaps back. |
| Airflow DAG | [`pipelines/airflow/dags/training_pipeline.py`](../pipelines/airflow/dags/training_pipeline.py), `make train-dag` | 11 tasks, monthly schedule. A rejected model ends the run **green**. |
| Notebook | [`notebooks/03_training.ipynb`](../notebooks/03_training.ipynb), [`scripts/make_training_notebook.py`](../scripts/make_training_notebook.py) | Champion metrics vs baseline, per-train-type AUC, calibration curve, feature importance. |
| Tests | `tests/unit/test_{train,calibrate,gate,tracking,artifacts,pointer_release,model_card,pipeline}.py`, `tests/integration/test_training_mlflow.py` | Every rule on its own, the whole pipeline with a fake tracker, and the real MinIO + MLflow scenario. |

## 2. How it works

```text
gold snapshot (Phase 3)  ──►  build_training_set ─► validate_snapshot
                                        │
                     ┌──────────────────┴───────────────────┐
               train_baseline                         train_lightgbm  (new MLflow run;
          (late-rate lookup, Phase 3)                  model.txt + feature_spec.json)
                     │                                       │
                     │                                   calibrate   (calibrator.json)
                     └──────────────────┬───────────────────┘
                                        ▼
            evaluate: challenger, baseline and current champion on the SAME test rows
            (metrics.json, model_card.md, reference_sample.parquet)
                                        ▼
            register: MLflow model "puenktlich-delay", new version, alias @challenger
                                        ▼
                                      gate
                     ┌──────── rejected ──┴── passed ────────┐
              record_rejection                             release
              (run ends green)          models/<v>/ + manifest → verify → pointer → @champion
                                                              ▼
                                                         smoke_test
                                   (reload from MinIO, check hashes, same predictions)
```

The MLflow run is the "shared folder" between tasks: each task reads the files the previous task logged.
Airflow only passes three small ids (snapshot id, run id, model version).

## 3. Key decisions

| Decision | Why |
|---|---|
| New dependencies `lightgbm` and `mlflow-skinny` only (owner) | `mlflow-skinny` is the client without the server/UI dependencies; same version as the server (3.16.1). |
| Model saved as LightGBM text (`model.txt`), no pickle, no MLflow "pyfunc" | Text models are safe to load and readable; pickle can run code on load. |
| Pointer = an object in MinIO (owner) | Visible to both Airflow (in Docker) and `make rollback` on the laptop. Same interface as the AWS parameter store in Phase 6. |
| A tie with the champion is rejected (owner) | Otherwise retraining on the same data would "promote" an identical model every month. |
| Champion is re-scored on today's test rows with its own feature spec | A fair comparison; also proves the released files still load. |
| Smoke test = reload the released bundle and reproduce the evaluated predictions (owner) | The API comes in Phase 5; until then this proves the release is usable. |
| No matplotlib at training time | Plots would add a dependency to the Airflow image; MLflow gets JSON tables, the notebook draws the plots. |
| MLflow client defaults (set by the tracker): proxied artifact transfers, no stdout run-URL print | The server hands out download links for `minio:9000`, which only resolves inside Docker; MLflow's emoji print crashed the Windows console. |

## 4. Results (real data, measured)

Snapshot `2026-08-31_38b45c7a` (Phase 3; train 3,197,166 rows, valid 178,256, test 176,628).

| Test split (176,628 rows) | LightGBM v1 (champion) | Baseline |
|---|---|---|
| Brier (lower is better) | **0.1408** | 0.1520 |
| ROC-AUC | **0.8078** | 0.7714 |
| PR-AUC | **0.5959** | 0.5272 |
| Log loss | **0.4391** | 0.4704 |
| ECE (calibration error) | 0.0218 | 0.0124 |

- Gate limit for "beats baseline by 5 %": 0.1444 → v1 is **7.4 %** better and was promoted.
- Best grid point: `num_leaves=127`, `learning_rate=0.1`, `min_data_in_leaf=500`, 477 trees (valid log loss 0.4521).
- Per train type: the largest AUC drop vs the baseline is 0.008 (limit 0.02); 20 small train types have too few
  rows for an AUC and were skipped (listed in `gate.json`).
- Most important features (gain): train type, destination, stop index, line, station.
- `make train` takes about **20 minutes** on the laptop (16 cores, 8 grid points).
- Second `make train` on the same snapshot: identical model → **rejected** (`brier_vs_champion`, tie), exit 0.
- Airflow DAG: two runs ended **green** (about 21 minutes each). Both trained the same model again, the gate rejected it
  (versions 3 and 4, tie with the champion), `release` and `smoke_test` were skipped, and the champion stayed v1.
- `make rollback` on the real models refuses ("no previous model version") because only v1 was ever promoted; the
  pointer stays unchanged. The swap itself is tested in the integration test.

## 5. How it was tested

- Unit tests (no Docker): config validation, deterministic training, calibration vs scikit-learn, every gate rule
  (incl. tie, missing metrics, skipped slices), tamper/missing/unlisted bundle files, pointer/release/rollback
  incl. crash-and-rerun, the whole pipeline with a fake tracker (promote → identical rerun rejected; weak → better
  model replaces it; broken champion fails closed; smoke test catches different predictions).
- Integration (MinIO + MLflow): tracker round trip; weak model v1 → better v2 → identical v3 rejected → rollback.
- DAG: `make test-dags` checks the 11 tasks and the branch/edges.
- Real runs: `make train` twice and the DAG (section 4).

## 6. How to run it

```bash
make up                 # MinIO, Postgres, MLflow
make train              # snapshot → train → … → gate → release (≈ 20 min)
make airflow-up         # rebuilds the Airflow image (lightgbm + mlflow-skinny)
make train-dag          # unpause + trigger training_pipeline (Airflow UI :8080)
make rollback           # champion ↔ previous (pointer + MLflow alias)
```
MLflow UI: http://localhost:5000 (experiment and registered model `puenktlich-delay`).
Note: the first `make train-dag` unpauses the DAG, which also starts the run for the latest monthly interval.

## 7. Known gaps and risks carried forward

- The valid split is used for early stopping, grid selection and calibration; only test is untouched.
- ECE on test (0.022) is higher than the baseline's (0.012), although Brier is clearly better.
- `month` values September–November never appear in training (the model extrapolates).
- If an Airflow task is killed mid-run, its MLflow run stays RUNNING (only Python errors mark it FAILED).
- MLflow test experiments/models from integration tests stay in the local MLflow database.
- The MLflow client sends anonymous usage telemetry by default (`MLFLOW_DISABLE_TELEMETRY` turns it off) — owner
  to decide.
- The snapshot id can differ inside the Airflow image (library versions); the data is the same.

## 8. What comes next

Phase 5 — local serving and UI: a FastAPI app that loads the champion through `load_bundle` (checksums verified),
builds features with `build_features`, and a React board that shows the risk badge per departure.
