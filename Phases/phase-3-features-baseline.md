# Phase 3 — Features & baseline

**Goal:** turn the cleaned history into a fixed training dataset, build model inputs ("features") in exactly one
place, and measure a simple baseline that every later model has to beat.

**Status:** ✅ done on branch `phase-3/features-baseline`, waiting for the owner's merge. The branch is kept.

---

## 1. What was built

| Deliverable | File(s) | What it is |
|---|---|---|
| Training settings | [`configs/training.yaml`](../configs/training.yaml), [`src/dbdelay/training/config.py`](../src/dbdelay/training/config.py) | Window (9 months), test and valid days (14 each), gap exclusion on/off, thresholds. Validated on load. |
| Calendar features | [`src/dbdelay/features/calendar.py`](../src/dbdelay/features/calendar.py) | Berlin local hour, minute of day, weekday, weekend, month; public holidays (national and per federal state, `holidays` library). |
| Feature spec | [`src/dbdelay/features/spec.py`](../src/dbdelay/features/spec.py) | The frozen list of known values per category (station, train type, line, destination). Rare or unseen values become `OTHER`. It is fitted on the **train** split only. |
| Feature builder | [`src/dbdelay/features/build.py`](../src/dbdelay/features/build.py) | `build_features(rows, spec)`: the only function that makes model inputs. Training uses it now, and the API and monitoring will use it later. |
| Gold snapshot | [`src/dbdelay/training/split.py`](../src/dbdelay/training/split.py) | Picks the 9-month window, removes cancelled rows and flagged data gaps, splits by date, and writes `gold/training_sets/<id>/` with a manifest. |
| Baseline | [`src/dbdelay/training/baseline.py`](../src/dbdelay/training/baseline.py) | "How often was this kind of departure late before?" — a lookup table with fallback to coarser groups. |
| Evaluation | [`src/dbdelay/training/evaluate.py`](../src/dbdelay/training/evaluate.py) | Brier score, ROC-AUC, PR-AUC, log loss, calibration error (ECE), calibration curve, and the same per train type, station and time of day. |
| Command | [`src/dbdelay/training/run_baseline.py`](../src/dbdelay/training/run_baseline.py), `make baseline` | Runs the whole chain and stores the spec, the baseline and the metrics next to the snapshot. |
| Notebook | [`notebooks/02_baseline.ipynb`](../notebooks/02_baseline.ipynb) | Metrics table and calibration plot for the latest snapshot. |
| Tests | `tests/unit/test_{training_config,calendar,feature_spec,build_features,split_windows,snapshot,baseline,evaluate,run_baseline}.py`, `tests/integration/test_snapshot_minio.py` | Each rule tested on its own, including leakage, daylight saving and edge cases; end to end on real MinIO. |

## 2. How it works

```text
silver days 2025-12-01 … 2026-08-31 (MinIO)      quality reports (one per month)
        │ download, DuckDB keeps labelled rows             │ flagged hours and station-days
        ▼                                                  ▼
remove cancelled rows (they have no label)  →  remove rows inside flagged data gaps
        ▼
split by UTC date:  train 2025-12-01 … 2026-08-03 │ valid 2026-08-04 … 08-17 │ test 2026-08-18 … 08-31
        ▼
gold/training_sets/2026-08-31_38b45c7a/{train,valid,test}.parquet + snapshot.json
        ▼
fit feature spec on train → build features for all splits → fit baseline on train
        ▼
evaluate on valid and test → baseline/{feature_spec.json, baseline.json, metrics.json}
```

- **Why split by time, not at random:** the model will always predict the *future*. Random splits would let it
  learn from days that come after the ones it is tested on, so the scores would look better than reality.
- **Why the spec is fitted on train only:** if valid or test could decide which stations or lines are "known",
  information from the future would leak into training.
- **Why the snapshot has an id:** `2026-08-31_38b45c7a` = last day + the first 8 characters of a hash of the
  data. The same silver data always gives the same id and the same bytes, so a model can always be traced back
  to exactly what it was trained on.
- **Baseline fallback:** it first looks for (station, train type, hour, weekday). If that group had fewer than
  50 training rows, it tries (station, train type, hour), then (station, train type), then (train type), and
  finally the overall late rate.

## 3. Key decisions

| Decision | Why |
|---|---|
| Exclude flagged data gaps from all splits (owner) | During source outages, DB updates are missing, so rows can be wrongly labelled "on time". Excluded rows are counted in `snapshot.json`. |
| New dependencies: `holidays` (main) and `scikit-learn` (`training` extra) (owner) | Holidays are needed by the API later. Tested metric code instead of hand-written metrics. scikit-learn stays out of the API image. |
| The snapshot stores cleaned rows, not features | Features can change (Phase 4) without rebuilding the snapshot. |
| `line_key` = train type + line number; a missing line becomes `"<type>:none"` | About 25 % of rows have no line number. |
| Starting thresholds (not tuned): `OTHER` below 200 train rows, baseline group ≥ 50 rows, slice metrics only from 500 rows | Simple, safe defaults; Phase 4 revisits them. |
| Feature spec stores each station's federal state | The API will only need the spec file to compute holidays. |

## 4. Results (real data, measured)

**Snapshot `2026-08-31_38b45c7a`** (window 2025-12-01 … 2026-08-31, 274 silver days)

| Split | Dates | Rows | Late rate |
|---|---|---|---|
| train | 2025-12-01 … 2026-08-03 | 3,197,166 | 24.3 % |
| valid | 2026-08-04 … 2026-08-17 | 178,256 | 27.2 % |
| test | 2026-08-18 … 2026-08-31 | 176,628 | 24.7 % |

Excluded: 170,228 cancelled rows (no label), 494 rows in flagged low-volume hours, 1,236 rows on flagged
station-days. Kept + excluded = 3,724,008 = all silver rows from Phase 2.

**Baseline metrics**

| Metric | valid | test |
|---|---|---|
| Brier score (lower is better) | 0.1588 | 0.1520 |
| ROC-AUC | 0.7783 | 0.7714 |
| PR-AUC | 0.5739 | 0.5272 |
| Log loss | 0.4901 | 0.4704 |
| ECE (calibration error) | 0.0280 | 0.0124 |

For comparison, always predicting the base rate would give a Brier score of about 0.186 on test
(0.2473 × 0.7527). By train type on test, ICE and NX are the hardest (Brier 0.23–0.24); S-Bahn and RB the easiest
(0.10–0.12).

## 5. How it was tested

- `make check`: ruff + format + mypy `--strict` clean, **216 unit tests passed, 99 % coverage**; `make test-integration`: **5 passed** (real MinIO: snapshot built twice → same id and bytes; baseline end to end); `make test-dags`: **DAG check passed** after rebuilding the Airflow image with `holidays` 0.105
- Real run: `make baseline` → exit 0 in 96 s. A second run (91 s) gave **the same snapshot id and identical
  bytes for all 7 gold files**.
- Independent final review (most capable model): 0 Critical, 2 Important — both fixed with failing tests first (missing/out-of-range `stop_index` or missing time was silently turned into a wrong value; a partly collected newest month would have put the test split on empty days) — 7 Minor deferred. The reviewer also rebuilt the real snapshot independently and got the same id `2026-08-31_38b45c7a`.

## 6. How to run it

```bash
make up              # MinIO, Postgres, MLflow (silver from Phase 2 must exist)
make baseline        # build or reuse the snapshot, fit and evaluate the baseline
uv run jupyter lab notebooks/02_baseline.ipynb   # metrics table + calibration plot
```

Change the window or thresholds in `configs/training.yaml` (e.g. a fixed `end_date`) and run `make baseline` again.
Different data gives a new snapshot id; the old snapshot stays.

## 7. Known gaps and risks carried forward

- **Snapshot id depends on library versions:** the hash covers the parquet bytes, which include the pyarrow/pandas version.
  After a library upgrade (or inside the Airflow image in Phase 4) the same silver can get a new id — a duplicate, not wrong data.
- **Test metrics are on cleaned rows:** flagged data gaps are removed from test too; live serving will not have that cleanup.
- **`month` feature:** train covers December–August only; September–November values are never seen in training (Phase 4).
- **Holidays:** the library's Bavaria calendar has no Assumption Day (15 Aug) — only 2026-08-15 (a Saturday) is affected in the window.
- **Window edges:** 2025-12-01 and 2026-08-31 may miss a few rows at the UTC edges (no 2025-11 / 2026-09 bronze, see Phase 2).
- **Starting thresholds** (`OTHER` < 200 rows, baseline group ≥ 50, slices ≥ 500) are not tuned; Phase 4 revisits them.
- **Deferred review minors:** snapshot reuse trusts `snapshot.json` alone; the empty-split test does not assert that nothing was
  uploaded; `FeatureSpec.from_json` does not check version/feature list; the gap-hour parsing relies on flagged hours being 06–21;
  the notebook picks the latest snapshot by name, not by time.

## 8. What comes next

Phase 4 trains the real model (LightGBM) on this snapshot with the same `build_features`, calibrates it, tracks
experiments in MLflow and only promotes a model that beats this baseline. It starts only when the owner says go.
