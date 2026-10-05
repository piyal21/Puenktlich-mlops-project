# Changelog

One entry per phase branch (owner rule, from Phase 2 on). Phases 0–1 are described in [`Phases/`](Phases/).
Only measured numbers; anything not measured is `TBD`.

## Phase 5 — Local serving & UI (`phase-5/serving-ui`)

**Built**
- `dbdelay.serving`: `model_loader` (`ModelProvider`: pointer re-read every 5 min, bundles via the checksum loader,
  one load per version, integrity errors fail closed, storage blips keep the current model and retry after 15 s),
  `board` (`live/boards/latest.json.gz` contract v1, 60 s cache that keeps the last good board, station/window
  selection), `scoring` (risk level, board + single scoring, one JSON prediction log line per departure),
  `explain` (top-3 LightGBM contributions → plain sentences), `stations` (case/accent/umlaut-spelling search),
  `seed` (`make seed`: a real silver day with today's weekday replayed onto today, DST-safe).
- `dbdelay.training.report` (report models moved out of `evaluate`; the calibrator imports scikit-learn only to fit),
  so the serving import path never loads scikit-learn (guard test).
- `services/api`: FastAPI `create_app(deps)` with routes health / stations / departures / predict / model, OpenAPI at
  `/api/docs`, problem+json errors, `X-Request-ID`, 4 KB body limit, dev-only CORS; Mangum `lambda_handler`;
  Dockerfile with `local` (uvicorn) and default `lambda` targets; hash-pinned requirements (`make api-requirements`).
- `frontend/`: React 19 + Vite + TS strict + Tailwind v4 (design.md tokens), TanStack Query, React Router: board
  (combobox search, recent stations, 1/3/6 h, loading/empty/error/stale/no-model states, sample-data banner),
  detail sheet (probability bar with 20/45 % marks, reasons, focus trap, scroll lock), health (real champion test
  metrics), about; light/dark theme.
- Compose profile `app` (`api` :8000, `frontend` Node 24 + Vite :5173 proxying `/api`); `make app-up`,
  `app-down`, `api-dev`, `web-check`, `seed`. MLflow telemetry off (client default, server, Airflow, make).

**Key decisions**
- Owner: sample board from a real silver day; slim serving path without scikit-learn; compose profile with a Node
  container; health page shows only real metrics (charts wait for Phase 9); additive API fields `top_factors`,
  `data_source`, `replayed_from`; new board file contract; deps `fastapi`, `mangum` (extra `api`), `uvicorn`
  (group `api-local`), `httpx` (dev) + the npm list in the spec; MLflow telemetry off.
- Cancelled departures get `prediction: null`; no champion → board still served, `/predict` and `/model` 503.
- API times use the Berlin offset; `/predict` accepts years 2000–2099 only (422 otherwise).
- Light `--risk-low` darkened `#12805C` → `#117B58` (4.3:1 → 4.6:1 on its badge, WCAG AA); design.md updated.
- On phones the risk badge sits under the destination (long names were cut off at 360 px).

**Tested**
- `make check`: ruff + mypy strict (incl. `services/api`) clean, **402 passed**, coverage **97 %**.
- `make web-check`: ESLint + Prettier + `tsc` clean, **47 passed** (incl. token contrast test, 9 pairs × 2 themes).
- `make test-integration`: **8 passed** (71 s), incl. seed + API serving the real champion v1 from MinIO. One run
  failed with `RequestTimeTooSkewed` after the PC slept (Docker clock drift); re-run green.
- `make test-dags`: DAG check passed. `uv run pre-commit run --all-files`: clean. `npm audit --omit=dev`: 0.
- Real `make seed`: 3,376 departures at 30 stations, replayed from 2026-08-24.
- Lambda image in the runtime emulator: `statusCode` 200. Compose `api`: champion v1 loaded, e.g. NJ 402 →
  39.6 % Medium with 3 reasons. Image size 1.58 GB.
- No champion (API on an empty models bucket): board `model_version: null`, `/model` 503 problem+json.
- Headless Chromium at 360 / 390 / 1280 px: board, detail sheet (focus on Close, 3 reasons), health, about render.
- Accessibility audit (`design:accessibility-review`): 1 Major (Low badge contrast) fixed; design critique: no
  critical issues.
- Final whole-branch review (independent, opus): 0 Critical, 2 Important + 2 re-graded to Important (model dropped on
  a storage blip, `/predict` 500 on far dates, stale detail after Back, Dockerfile default target), all fixed
  test-first; remaining minors deferred below.

**Known gaps / open items**
- Sample data only until Phase 7; silver has no platform ("Platform not known yet"). Month Sep–Nov unseen in train.
- API image 1.58 GB (pandas, PyArrow, DuckDB, LightGBM + SciPy). Unit suite takes ≈ 4 min on this PC.
- Starlette's test client warns it wants `httpx2` (not added: new dependency).
- Phase 6 checks: API Gateway → Mangum must send `Content-Length` on POST (else 411); booster use from uvicorn's
  thread pool is untested under concurrency (Lambda serves one request at a time).
- Deferred review minors: search input that normalises to empty returns all stations; unknown station URL shows a
  generic error with a useless retry; Enter can pick a station from the previous query while searching; seed drops
  the ambiguous/nonexistent DST hour; `/health` and `/model` times are UTC; reason text for an OTHER-bucket
  station/line/destination; 411/413 lack CORS headers; problem handler drops HTTP headers (405 `Allow`);
  `train_type.upper()` lives in the predict router.
- Deferred a11y/design notes: card borders and probability-bar track below 3:1 (non-text; value also in text); page
  title not per route; no focus move on route change; theme button names the state; home could suggest popular
  stations; coupled trains look like duplicates.

**Docs touched**
- `docs/architecture.md` (§3.2, §5.2 note, §7 contract additions, board file, local serving, §13 settings, §14
  layout), `docs/design.md` (risk-low token), `docs/phases.md` (Phase 5 ticked), `README.md`, `CLAUDE.md`,
  `Phases/phase-5-serving-ui.md`, `Phases/README.md`, spec + plan under `docs/superpowers/`, this file.

## Phase 4 — Training, tracking, registry, gate (`phase-4/training`)

**Built**
- `dbdelay.training`: `train` (LightGBM Booster, native categoricals, 8-point grid, early stopping on valid,
  deterministic), `calibrate` (isotonic on valid → `calibrator.json`, applied with `numpy.interp`), `gate` (5 checks,
  tie with champion rejects), `tracking` (`Tracker` protocol + `MlflowTracker`), `model_card`, `pipeline` (one
  function per DAG step, MLflow run as hand-off), `run_train` (`make train`); `TrainingReport` in `evaluate`;
  `run_baseline.fit_baseline` extracted.
- `dbdelay.registry`: `artifacts` (6-file bundle + `manifest.json` with SHA-256, fail-closed loader, `ModelBundle.predict`),
  `pointer` (`models/_pointer.json` in MinIO), `release` (upload → verify → pointer → `@champion`, idempotent;
  `rollback`); `scripts/rollback.py` (`make rollback`).
- `configs/training.yaml`: seed, LightGBM grid, risk thresholds 0.20 / 0.45, gate (architecture §5.4), registry, release.
- Airflow: `training_pipeline` DAG (11 tasks, `@monthly`, gate branch; rejection ends green), image installs
  `dbdelay[pipelines,training]` + `libgomp1`, `GIT_SHA` build arg; `make train-dag`; `check_dags.py` checks the
  training DAG shape and that no TaskFlow argument uses an Airflow context name.
- `notebooks/03_training.ipynb` (+ `scripts/make_training_notebook.py`): champion vs baseline, slices, calibration,
  feature importance.

**Key decisions**
- Owner: deps `lightgbm` + `mlflow-skinny` (3.16, client only); pointer = MinIO object; `make train` + DAG share step
  functions; artifact smoke test until Phase 5; MLflow run = step hand-off; equal Brier vs champion → rejected.
- Model saved as LightGBM text, no pickle / pyfunc; champion re-scored on the same test rows with its own spec.
- Every run logs config contents, library versions and the `uv.lock` hash; every model version is tagged `git_sha`,
  `snapshot_id`, `gate_result` (rules.md §5.4).
- MLflow client defaults (set by `MlflowTracker`, explicit env wins): proxied artifact transfers (the server's presigned
  URLs point at `minio:9000`, unreachable from the host) and no stdout run-URL print (emoji crashed cp1252 consoles).
- MLflow server: `MLFLOW_SERVER_ALLOWED_HOSTS` lists localhost + `mlflow` (3.16's DNS-rebinding guard returned 403 to
  Airflow tasks). No matplotlib at training time (JSON tables in MLflow, plots in the notebook).

**Tested**
- `make lint` + `make typecheck` clean; `make test`: **292 passed**, coverage 97 %.
- `make test-integration`: **7 passed** (incl. MinIO + MLflow: weak v1 → better v2 → identical v3 rejected → rollback).
- `make test-dags`: **DAG check passed**.
- Real `make train` #1 (≈ 20 min, snapshot `2026-08-31_38b45c7a` reused): **v1 promoted**. Test (176,628 rows):
  Brier **0.1408** vs baseline 0.1520 (gate limit 0.1444, **7.4 %** better), ROC-AUC **0.8078** vs 0.7714, PR-AUC
  **0.5959** vs 0.5272, log loss **0.4391** vs 0.4704, ECE 0.0218 vs 0.0124. Best grid point `num_leaves=127`,
  `learning_rate=0.1`, `min_data_in_leaf=500`, 477 trees. Worst train-type AUC drop vs baseline 0.008; 20 small train
  types skipped.
- Real `make train` #2 (≈ 20 min): identical model → **rejected** (`brier_vs_champion`), exit 0.
- DAG `training_pipeline` (image `c1f7ecc`, Airflow): 2 runs **green** (≈ 21 min each; train_lightgbm ≈ 17 min). Both
  trained an identical model → v3 / v4 **rejected** (`brier_vs_champion`), `record_rejection` ran, `release` and
  `smoke_test` skipped; pointer and `@champion` stayed on v1; v3/v4 carry `git_sha`, `snapshot_id`, `gate_result` and
  the run carries library versions. Same snapshot id inside the image (`2026-08-31_38b45c7a`). Earlier DAG attempts
  failed before the fixes (MLflow 403 host guard, `run_id` arg clash) or were killed by PC standby.
- `make rollback` on the real models: refuses with "no previous model version to roll back to" (only v1 was ever
  promoted), exit 1, pointer unchanged. A real swap is covered by the integration test (v2 → v1 and alias moved).
- Final whole-branch review (independent, opus): 0 Critical, 1 Important (fixed test-first), 9 Minor (deferred below).

**Known gaps / open items**
- Valid split used for early stopping, grid selection and calibration; test ECE (0.022) above the baseline's (0.012).
- `month` Sep–Nov unseen in train. Killed Airflow tasks leave their MLflow run RUNNING (Python errors mark it FAILED).
- PC standby kills Airflow tasks (task token expires) — keep the PC awake during DAG runs.
- First `make train-dag` unpauses the DAG, which also starts the latest `@monthly` run.
- MLflow client telemetry was on by default (`MLFLOW_DISABLE_TELEMETRY`); turned off in Phase 5 (owner).
- Deferred review minors: LightGBM param aliases (e.g. `n_estimators`) not rejected in `lightgbm.params`; rollback
  re-run after an alias failure swaps again; no pointer lock (one trainer at a time); `is_late` cast before silver
  validation drops the dtype check; failed smoke test leaves the pointer on that version (run `make rollback`); an
  error in `finish_run(failed=True)` masks the original; `os.environ` touched in `MlflowTracker` / `current_git_sha`;
  fake vs real tracker differ on missing artifacts.

**Docs touched**
- `docs/architecture.md` (§3.2 pointer, §5.2 Phase 4 notes, §5.4 tie/skip rules, §6 manifest coverage),
  `docs/phases.md` (Phase 4 ticked), `README.md`, `CLAUDE.md`, `Phases/phase-4-training.md`, `Phases/README.md`,
  this file.

## Phase 3 — Features & baseline (`phase-3/features-baseline`)

**Built**
- `dbdelay.features`: `calendar` (Berlin local hour/minute/weekday/weekend/month, national + state holidays), `spec`
  (`FeatureSpec` fitted on train only; rare/unseen levels → `OTHER`; input checks), `build` (`build_features`, the only
  feature maker; leakage-guarded).
- `dbdelay.training`: `config` (`configs/training.yaml`), `split` (window, data-gap exclusion, UTC-date split,
  deterministic gold snapshot `gold/training_sets/<end>_<hash8>/` + `snapshot.json`, reuse if present), `baseline`
  (late-rate lookup with 4 back-off levels + global), `evaluate` (Brier, ROC-AUC, PR-AUC, log loss, ECE, calibration
  bins, slices by train type / station / hour band), `run_baseline` (`make baseline`).
- `notebooks/02_baseline.ipynb` (metrics table + calibration plot), `scripts/make_baseline_notebook.py`.

**Key decisions**
- Owner: new deps `holidays` (main) and `scikit-learn` (extra `training` + dev); flagged data gaps excluded from all splits.
- Snapshot = cleaned silver rows (no features); spec and baseline fitted on train only; split by UTC date
  (test = last 14 days, valid = 14 before); cancelled rows excluded (no label, ADR 0001).
- `line_key` = `"<train_type>:<line_number>"`, missing line → `"<train_type>:none"`; feature spec stores station states.
- Starting thresholds (not tuned): `OTHER` < 200 train rows, baseline group ≥ 50 rows, slice metrics from 500 rows.

**Tested**
- `make check`: ruff + format + mypy `--strict` clean, **216 unit tests passed, 99 % coverage**.
- `make test-integration`: **5 passed** (MinIO: snapshot twice → same id and bytes; baseline end to end).
- `make test-dags`: **DAG check passed** after rebuilding the Airflow image with `holidays` 0.105.
- Real run `make baseline`: exit 0 in **96 s**; snapshot **`2026-08-31_38b45c7a`** (2025-12-01 … 2026-08-31, 274 silver days):
  train **3,197,166** rows (late 24.3 %), valid **178,256** (27.2 %), test **176,628** (24.7 %); excluded **170,228**
  cancelled, **494** gap-hour and **1,236** gap-station-day rows (sum = all 3,724,008 silver rows).
- Baseline — valid: Brier **0.1588**, ROC-AUC **0.7783**, PR-AUC **0.5739**, log loss **0.4901**, ECE **0.0280**;
  test: Brier **0.1520**, ROC-AUC **0.7714**, PR-AUC **0.5272**, log loss **0.4704**, ECE **0.0124**.
- Determinism: second run → identical snapshot id and sha256 of all 7 gold files; the reviewer's independent rebuild and
  a third run after the fix pass → same id.
- Final whole-branch review (independent reviewer): 0 Critical, 2 Important (fixed with tests first), 7 Minor (deferred).

**Known gaps / open items**
- Snapshot id hashes parquet bytes incl. pyarrow/pandas version metadata → a library upgrade or another image gives a new
  id for the same silver (duplicate, not wrong data).
- Test metrics are on gap-cleaned rows; live serving will not have that cleanup. `month` values Sep–Nov never seen in train.
- Holidays: library's Bavaria calendar lacks Assumption Day (15 Aug); only 2026-08-15 (Saturday) affected in the window.
- Deferred review minors: snapshot reuse trusts `snapshot.json` alone (a deleted split parquet fails later with a raw
  error); empty-split test does not assert nothing was uploaded; `FeatureSpec.from_json` does not check version/feature
  list (spec JSON has no dtypes/hash); gap-hour parsing relies on flagged hours being 06–21; notebook picks the latest
  snapshot by key name, not time.
- Thresholds untuned; risk thresholds (0.20 / 0.45) still not in a config file.

**Docs touched**
- `docs/phases.md` (Phase 3 ticked), `docs/architecture.md` (§3.4 gold contents, §4 `line_key` rule), `README.md`
  (`make baseline`), `CLAUDE.md`, `Phases/phase-3-features-baseline.md`, `Phases/README.md`, this file.

## Phase 2 — Historical ETL with Airflow (`phase-2/hf-backfill`)

**Built**
- `dbdelay.data.months`, `dbdelay.data.stations`: month helpers and the typed station loader (Berlin alias map).
- `dbdelay.data.hf_backfill`: HF month → MinIO bronze (pinned dataset revision, SHA-256 manifest written last,
  streamed upload), and bronze M-1/M/M+1 → silver (DuckDB station filter, streamed download, checksum check first).
- `dbdelay.data.silver`: `conform_hf` (EVA/alias mapping, ride id, Berlin→UTC with DST NaT drops, label, `event_id`,
  de-duplication by latest `ingested_at`, contract check), deterministic parquet writer (one file per UTC day,
  empty days included).
- `dbdelay.data.quality`: quarantine with reasons, `content_hash`, monthly JSON quality report (volume drops,
  low-volume hours, drops by reason, edge completeness).
- `ObjectStore.upload_file` / `download_file` (streaming transfers).
- Airflow 3.3.2 stack (api-server, scheduler, dag-processor, init; LocalExecutor, `airflow` DB in the shared
  Postgres), image `pipelines/airflow/Dockerfile`, DAG `backfill_history`
  (`plan_months → ingest[] → build_silver[] → summarize`).
- `scripts/ensure_airflow_env.py` and Make targets `airflow-env`, `airflow-up`, `airflow-down`, `test-dags`, `backfill`.

**Key decisions**
- Split Airflow services + LocalExecutor; DuckDB for filtering, pandas for the rules.
- The image installs under Airflow's official constraints file, minus its `pandas==` line (the project stays on pandas < 3);
  `huggingface-hub>=0.30,<2` in both the venv and the image.
- Trains crossing the spring DST switch are quarantined as `delay_mismatch_utc`; `build_silver` has no retries.
- `# noqa: PLR0913` on `ingest_month` (keyword-only test seams); ruff `PLC0415` is ignored for `pipelines/airflow/dags/**`
  (lazy imports inside tasks) — both flagged for the owner.

**Tested**
- `make check`: ruff + format + mypy `--strict` clean, **151 unit tests passed, 99 % coverage**
- `make test-integration`: **3 passed** (real MinIO; a month built twice gives identical hash and bytes)
- `make test-dags`: **DAG check passed** inside the Airflow image
- Real backfill 2025-12 … 2026-08: run `manual__2026-09-27T14:21:12` → **success**: 9 bronze months (~650 MB each), **274 silver day files**, 9 quarantine files, 9 quality reports; **3,937,131 rows read → 3,724,008 silver rows**; late rate 22.2–27.9 % per month; 37 rows quarantined (all 2026-03, `delay_mismatch_utc`, the spring DST night); 2026-08 = 408,638 rows, identical to the reviewer's independent probe
- Determinism (second run, same `content_hash` per month): second run on the fixed image (`manual__2026-09-28T17:03:07`, ingest skipped, all silver rebuilt in 5 min 25 s) → **identical `rows_out`, late rate, quarantine, drops and `content_hash` for all 9 months**
- Final whole-branch review (independent reviewer): 0 Critical, 2 Important (fixed with tests first), 8 Minor (deferred).

**Known gaps / open items**
- **PC sleep kills running Airflow tasks** (containers freeze, the task's 10-minute token expires, heartbeat gets 403). It happened twice during the real run; failed tasks were cleared and finished. Keep the PC awake during backfills.
- **Data gaps flagged by the quality reports** (the source dataset, not our code): low-volume hours on 2026-02-02 18–21h, 2026-03-16 18–21h, 2026-04-08 06–11h, 2026-08-27/28 mornings; 18 station-days with a volume drop, e.g. Potsdam Hbf 2026-03-23…31. Phase 3 decides whether to mask them in training.
- **Month edges:** 2025-12 has no 2025-11 bronze and 2026-08 has no 2026-09 bronze (`edge_complete` false) — the first/last UTC hours of the window may miss a few rows.
- **Deferred review minors:** HF network errors (httpx) not wrapped as `ExternalServiceError` (Airflow still retries); Berlin alias duplicates tie-break on id text; the report lacks pre-filter counts (`not_supported_station` is always 0); the quarantine file has no fixed schema and can count overlap rows twice; `plan_months` retries bad input; `build_silver` does not retry transient MinIO errors; `force_download` of a month does not rebuild its neighbours.
- **Owner decisions flagged:** `# noqa: PLR0913` on `ingest_month`; ruff `PLC0415` off for `pipelines/airflow/dags/**`.
- Train-type-mix shift flag (suggested in Phase 1) not built; risk thresholds still not in a config file.

**Docs touched**
- `docs/architecture.md` (storage layout, §14 layout), `docs/phases.md` (Phase 2 ticked), `README.md` (backfill commands),
  Phase 2 spec (quarantine per month, reason names), `CLAUDE.md`, `Phases/phase-2-hf-backfill.md`, `Phases/README.md`.
