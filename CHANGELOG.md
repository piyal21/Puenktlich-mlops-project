# Changelog

One entry per phase branch (owner rule, from Phase 2 on). Phases 0–1 are described in [`Phases/`](Phases/).
Only measured numbers; anything not measured is `TBD`.

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
