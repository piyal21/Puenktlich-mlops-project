# Phase 9 — Monitoring & continuous training Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily AWS monitor scores all of yesterday's live departures, records performance and drift, raises a retrain flag after 3 bad days, and the laptop retrain loop (sync → Airflow → gate → publish) closes it; the Model Health page shows all of it.

**Architecture:** New package `dbdelay.monitoring` (pure functions + one job), run by a `monitor` Lambda on the existing API image (no new runtime dependency; scipy is already there). Results are JSON in the data lake, served by a new `GET /api/v1/monitoring`. Retraining stays on the laptop: `make retrain-sync` (S3 → MinIO), Airflow `drift_watch` → `training_pipeline` (reads HF + live silver), `make retrain-publish` (MinIO → S3 + SSM).

**Tech Stack:** Python 3.12, numpy/pandas/scipy, LightGBM bundle loader, Powertools Metrics (EMF), FastAPI, Airflow 3 TaskFlow, Terraform (AWS provider ~> 6), React 19 + TanStack Query + Recharts.

**Spec:** `docs/superpowers/specs/2026-11-18-phase-9-monitoring-design.md`

## Global Constraints

- No new Python runtime dependency in the API image; `dbdelay.monitoring` must not import sklearn, duckdb or mlflow (extend `tests/unit/test_serving_imports.py`).
- Drift: numeric = `scipy.stats.wasserstein_distance(cur, ref) / max(std(ref), 0.001)`; categorical = `scipy.spatial.distance.jensenshannon(p, q, base=2)`; drifted when score ≥ `drift_threshold` (0.1).
- `DriftShare` counts only the 7 content columns: `eva`, `train_type`, `line_key`, `destination_key` (categorical), `stop_index`, `hour_local`, `minute_of_day` (numeric). Calendar columns `weekday`, `is_weekend`, `month`, `is_public_holiday` are reported, never counted. `p_late` drift is reported separately.
- `configs/monitoring.yaml`: `drift_threshold: 0.1`, `drift_share_max: 0.30`, `brier_ratio_max: 1.10`, `consecutive_days: 3`, `min_labeled_rows: 2000`, `ece_bins: 10`, `history_max_days: 366`.
- Keys (data bucket): `monitoring/daily/date=YYYY-MM-DD.json`, `monitoring/history.json`, `monitoring/retrains.json`, `control/retrain_requested.json`. Local (MinIO) only: `control/decisions/<request_id>.json`.
- EMF namespace `Puenktlich`, dimension `service=monitor`; metrics `LabeledEvents`, `BrierDaily`, `AUCDaily`, `DriftShare`, `BrierRatio`. Alarms total ≤ 10, custom metrics total ≤ 10.
- Monitor schedule 04:30 Europe/Berlin; Lambda 512 MB, timeout 300 s, async retries 1.
- UTC everywhere; Berlin only for features/display. Missing S3 key may surface as 403 (`NotFoundError` handling as Phase 7).
- No AWS credentials in Airflow; AWS access only from laptop `make` targets (`puenktlich-py` profile).
- `ObjectStore` has no delete (by design): flags change state instead of being deleted.
- Commits: Conventional Commits, no Claude co-author trailer. `make lint typecheck test` green before each commit.

## Review Focus

1. A day with silver but every row cancelled or gap-flagged (0 labelled rows) → status `too_few_rows`, no division by zero, AUC `null` when one class only. (Task 1, Task 5 tests)
2. A category in the current day that never occurs in the reference (and vice versa) → JS distance over the union of categories, no KeyError. (Task 2 test)
3. Re-running the monitor for the same day (backfill, retry) → history keeps one entry per day, sorted, flag not written twice. (Task 4, Task 5 tests)
4. The champion changes mid-streak → streak breaks (entries scored by another model version don't count). (Task 4 test)
5. `GET /api/v1/monitoring` before the first monitor run, or with a corrupt JSON file → 200 with empty lists / `latest: null` for missing, 502-style problem only for corrupt data (not a 500 stack trace). (Task 7 test)

---

### Task 1: Monitoring config + performance metrics

**Files:**
- Create: `configs/monitoring.yaml`, `src/dbdelay/monitoring/__init__.py`, `src/dbdelay/monitoring/config.py`, `src/dbdelay/monitoring/performance.py`
- Test: `tests/unit/test_monitoring_performance.py`, `tests/unit/test_monitoring_config.py`

**Interfaces:**
- Produces: `MonitoringConfig` (Pydantic, frozen, `extra="forbid"`, fields as in Global Constraints) and `load_monitoring_config(path: Path = Path("configs/monitoring.yaml")) -> MonitoringConfig`; `PerformanceMetrics(BaseModel)`: `rows: int, late_rate: float | None, mean_p_late: float | None, brier: float | None, log_loss: float | None, roc_auc: float | None, ece: float | None`; `performance(y: NDArray[np.bool_], p: NDArray[np.float64], ece_bins: int) -> PerformanceMetrics`.

- [ ] **Step 1: Failing tests** — `test_metrics_match_sklearn` (seeded 5 000 rows: brier/log_loss/roc_auc equal sklearn's to 1e-9; ece equals `dbdelay.training.evaluate`'s ECE with 10 bins); `test_auc_none_for_one_class`; `test_empty_input_all_none_rows_zero`; `test_log_loss_clips_probabilities` (p = 0 and 1 give a finite value, eps 1e-15 like sklearn); config: `test_loads_repo_config` (values above), `test_rejects_unknown_key`.
- [ ] **Step 2:** `uv run pytest tests/unit/test_monitoring_performance.py tests/unit/test_monitoring_config.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement.** AUC via ranks (Mann–Whitney with average ranks for ties, `scipy.stats.rankdata`). ECE: same binning as `training/evaluate.py` (read it; equal-width bins on p).
- [ ] **Step 4:** tests PASS; `make lint typecheck`.
- [ ] **Step 5: Commit** `feat(monitoring): config and numpy/scipy performance metrics`.

### Task 2: Drift

**Files:**
- Create: `src/dbdelay/monitoring/drift.py`
- Test: `tests/unit/test_monitoring_drift.py`

**Interfaces:**
- Consumes: `MonitoringConfig` (Task 1).
- Produces: `CONTENT_NUMERIC = ("stop_index", "hour_local", "minute_of_day")`, `CONTENT_CATEGORICAL = ("eva", "train_type", "line_key", "destination_key")`, `CALENDAR_COLUMNS = ("weekday", "is_weekend", "month", "is_public_holiday")`; `ColumnDrift(BaseModel)`: `column: str, method: Literal["wasserstein_normed", "jensen_shannon"], score: float, drifted: bool, counted: bool`; `DriftResult(BaseModel)`: `columns: list[ColumnDrift], drift_share: float, prediction: ColumnDrift, reference_rows: int, current_rows: int, day_type: Literal["weekday", "weekend_or_holiday"]`; `day_type(features: pd.DataFrame) -> Literal[...]` (majority of rows: `is_weekend or is_public_holiday`); `compute_drift(current: pd.DataFrame, reference: pd.DataFrame, current_p: NDArray, reference_p: NDArray, cfg: MonitoringConfig) -> DriftResult` — inputs are `build_features` output; reference is filtered to the current day type inside.

- [ ] **Step 1: Failing tests** — `test_identical_samples_no_drift` (share 0.0); `test_shifted_numeric_drifts` (minute_of_day +300 → drifted, score equals scipy formula); `test_new_category_union` (current has "X" not in reference → finite JS score, no error); `test_calendar_never_counted` (only `month` differs → `drift_share == 0`, month column `counted=False, drifted=True`); `test_share_is_over_seven_content_columns` (2 drifted → 2/7); `test_reference_filtered_by_day_type` (weekend current uses only weekend reference rows: `reference_rows` equals their count); `test_constant_reference_std_floor` (std 0 → divides by 0.001, no inf/nan).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.** Categorical probabilities over the union of observed levels (as strings). If the filtered reference is empty, fall back to the full reference and record `day_type` anyway.
- [ ] **Step 4:** PASS, lint/typecheck.
- [ ] **Step 5: Commit** `feat(monitoring): Evidently-default drift tests with scipy (content-column share)`.

### Task 3: Gap masks shared module + reference sample loader

**Files:**
- Create: `src/dbdelay/data/gaps.py` (move `GapReport` and `gap_masks` from `training/split.py`; `split.py` re-imports them, behaviour unchanged)
- Modify: `src/dbdelay/registry/artifacts.py` (add loader), `src/dbdelay/training/split.py`
- Test: `tests/unit/test_artifacts.py` (add), existing `tests/unit/test_snapshot.py` stays green; `tests/unit/test_serving_imports.py` (add `dbdelay.monitoring.*`, `dbdelay.data.gaps` to the no-sklearn/no-duckdb guard)

**Interfaces:**
- Produces: `dbdelay.data.gaps.gap_masks(df, reports) -> tuple[Series[bool], Series[bool]]` (same signature); `load_reference_sample(store: ObjectStore, version: str, root: str = "") -> pd.DataFrame` — reads `manifest.json`, downloads `reference_sample.parquet`, verifies its SHA-256 against the manifest (`ArtifactIntegrityError` on mismatch), returns the frame.

- [ ] **Step 1: Failing tests** — `test_load_reference_sample_verifies_checksum` (tampered bytes → `ArtifactIntegrityError`), `test_load_reference_sample_returns_rows`; guard test lists the new modules.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement** (move + loader). `gaps.py` imports only pandas/numpy/pydantic types and `features.calendar.BERLIN`.
- [ ] **Step 4:** `make test` (full, the move touches training) PASS.
- [ ] **Step 5: Commit** `refactor(data): gap masks in their own module; registry reads the reference sample`.

### Task 4: History + triggers

**Files:**
- Create: `src/dbdelay/monitoring/history.py`, `src/dbdelay/monitoring/triggers.py`
- Test: `tests/unit/test_monitoring_history.py`, `tests/unit/test_monitoring_triggers.py`

**Interfaces:**
- Consumes: `PerformanceMetrics`, `DriftResult`, `MonitoringConfig`.
- Produces:
  - `DayStatus = Literal["ok", "no_data", "too_few_rows"]`
  - `Verdict(BaseModel)`: `drift_bad: bool, performance_bad: bool, drift_share_max: float, brier_ratio_max: float`
  - `DailyResult(BaseModel)`: `day: date, status: DayStatus, model_version: str, champion_test_brier: float, performance: PerformanceMetrics, brier_ratio: float | None, drift: DriftResult | None, verdict: Verdict, excluded_rows: dict[str, int], computed_at: datetime`
  - `HistoryEntry(BaseModel)`: `day, status, model_version, rows, brier, roc_auc, ece, late_rate, mean_p_late, brier_ratio, drift_share, prediction_drift, verdict` and `HistoryEntry.from_daily(r: DailyResult) -> HistoryEntry`
  - `verdict_for(perf, brier_ratio, drift, status, cfg) -> Verdict` (no_data/too_few_rows → both False)
  - `upsert(history: list[HistoryEntry], entry: HistoryEntry, max_days: int) -> list[HistoryEntry]` (replace same day, sort by day, keep newest `max_days`)
  - `RetrainRequest(BaseModel)`: `request_id: str` (`"<YYYYMMDDTHHMMSSZ>-<reason>"`), `state: Literal["requested", "picked_up", "done"]`, `reason: Literal["drift", "performance"]`, `days: list[date]`, `model_version: str`, `metrics: dict[str, float | None]`, `forced: bool`, `thresholds: dict[str, float]`, `requested_at: datetime`, `picked_up_at: datetime | None = None`
  - `ThresholdOverrides(BaseModel, extra="forbid")`: `drift_share_max: float | None = None, brier_ratio_max: float | None = None`
  - `evaluate_trigger(history, cfg, *, now: datetime, overrides: ThresholdOverrides | None = None) -> RetrainRequest | None` — looks at the newest `consecutive_days` entries; they must be consecutive calendar days, status `ok`, same `model_version`, all `drift_bad` (reason drift, wins ties) or all `performance_bad`. With overrides, bad-ness is recomputed from stored metrics with the override values and the request has `forced=True`.

- [ ] **Step 1: Failing tests** — history: `test_upsert_replaces_same_day`, `test_upsert_sorts_and_caps`, `test_verdict_none_for_too_few_rows`; triggers: `test_three_bad_days_performance`, `test_gap_day_breaks_streak` (missing calendar day), `test_no_data_day_breaks_streak`, `test_model_change_breaks_streak`, `test_two_days_not_enough`, `test_drift_wins_when_both`, `test_overrides_force_and_mark` (`forced is True`, thresholds recorded, stored verdicts untouched).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(monitoring): daily results, history and 3-day retrain triggers`.

### Task 5: Monitor job

**Files:**
- Create: `src/dbdelay/monitoring/job.py`
- Test: `tests/unit/test_monitoring_job.py` (in-memory/moto `ObjectStore` like `test_live_etl.py`; a tiny real bundle like `test_scoring.py` fixtures)

**Interfaces:**
- Consumes: Tasks 1–4, `load_bundle`, `ModelPointer`, `live_silver_key`, `live_quality_key`, `LiveQualityReport`, `gap_masks`.
- Produces: key helpers `daily_key(day) -> str`, `HISTORY_KEY`, `RETRAINS_KEY`, `FLAG_KEY`; `MonitorOutcome(BaseModel)`: `result: DailyResult, flag_written: RetrainRequest | None`; `run_monitor(*, data: ObjectStore, models: ObjectStore, pointer: ModelPointer, day: date, cfg: MonitoringConfig, now: datetime, overrides: ThresholdOverrides | None = None) -> MonitorOutcome`.
- Behaviour: no champion → `ModelNotAvailableError` (raise; handler fails, alarm fires). Missing silver → `no_data`. Labelled rows after dropping cancelled + gap masks < `min_labeled_rows` → `too_few_rows` (performance still computed, drift skipped). Writes daily file, upserts history, then `evaluate_trigger`; writes `FLAG_KEY` only if none exists or the existing one's state is `done`/`picked_up`.

- [ ] **Step 1: Failing tests** — `test_normal_day_writes_daily_and_history`, `test_no_silver_is_no_data`, `test_gap_rows_excluded` (rows in a `low_volume_hours` hour counted in `excluded_rows["gap_hour"]`), `test_rerun_same_day_idempotent`, `test_flag_written_after_three_bad_days` (pre-seeded history with 2 bad days), `test_flag_not_overwritten_while_requested`, `test_forced_flag_keeps_history_verdicts`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS; import guard still green.
- [ ] **Step 5: Commit** `feat(monitoring): daily monitor job (score D-1, metrics, drift, flag)`.

### Task 6: Monitor Lambda handler + image + local target

**Files:**
- Create: `services/api/monitor_handler.py`
- Modify: `services/api/Dockerfile` (COPY handler + `configs/monitoring.yaml`), `Makefile` (`monitor-local DAY=…`: run the job against MinIO via `uv run python -m dbdelay.monitoring.cli --day`), create `src/dbdelay/monitoring/cli.py`
- Test: `tests/unit/test_monitor_handler.py`

**Interfaces:**
- Consumes: `run_monitor`, `get_settings`, `make_pointer`, `load_monitoring_config`.
- Produces: `handler(event: Mapping[str, Any], context: object) -> dict[str, Any]`; `MonitorEvent(BaseModel, extra="forbid")`: `day: date | None = None, threshold_overrides: ThresholdOverrides | None = None` (scheduler sends `{}`; default day = yesterday UTC). Emits EMF metrics (Task constraints) — `BrierDaily/AUCDaily/DriftShare/BrierRatio` only when not null; `LabeledEvents` always. Returns `{day, status, rows, flag}`.

- [ ] **Step 1: Failing tests** — `test_default_day_is_yesterday`, `test_rejects_unknown_event_key`, `test_emits_labeled_events_zero_on_no_data` (capture EMF via Powertools `metrics` fixture/`capsys`-free: patch `Metrics.add_metric`), `test_overrides_passed_through`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS; `docker build --target lambda -f services/api/Dockerfile .` OK; emulator run against MinIO for one live day (after Task 11's sync, or with a seeded live day) — record duration and `Max Memory Used`.
- [ ] **Step 5: Commit** `feat(monitoring): monitor Lambda handler on the API image`.

### Task 7: `GET /api/v1/monitoring`

**Files:**
- Create: `src/dbdelay/serving/monitoring.py`, `services/api/app/routers/monitoring.py`
- Modify: `services/api/app/schemas.py`, `services/api/app/deps.py` (`monitoring: MonitoringSource`), `services/api/app/main.py` (router list if not auto-discovered)
- Test: `tests/unit/test_api_routes.py` (add), `tests/unit/test_serving_monitoring.py`

**Interfaces:**
- Produces: `MonitoringSource(store: ObjectStore, ttl_s: float = 300)` with `.get() -> MonitoringView`; `MonitoringView(BaseModel)`: `latest: DailyResult | None, history: list[HistoryEntry], retrains: list[RetrainRecord]`; `RetrainRecord(BaseModel)` (Task 11 writes it): `request_id: str, reason: str, forced: bool, requested_at: datetime, decided_at: datetime, decision: Literal["released", "rejected", "failed"], challenger_version: str | None, champion_before: str | None, champion_after: str | None, failed_checks: list[str]`. Missing file → empty/None; invalid JSON → `ExternalServiceError` → existing problem handler (502). Response `MonitoringOut` mirrors `MonitoringView`; header `Cache-Control: public, max-age=300`. `latest` = daily file of the newest history day.

- [ ] **Step 1: Failing tests** — `test_monitoring_empty_before_first_run` (200, `latest is None`, `[]`), `test_monitoring_full`, `test_monitoring_corrupt_history_is_problem`, `test_monitoring_cache_header`, `test_missing_as_403_treated_as_missing`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS; `make api-requirements` unchanged.
- [ ] **Step 5: Commit** `feat(api): GET /api/v1/monitoring (daily results, history, retrains)`.

### Task 8: Training reads HF + live silver

**Files:**
- Modify: `src/dbdelay/training/split.py` (`latest_silver_day`, report loading, `_read_labelled_silver`, `SnapshotManifest`), `src/dbdelay/training/pipeline.py` only if signatures move
- Test: `tests/unit/test_snapshot.py`, `tests/unit/test_split_windows.py`

**Interfaces:**
- Consumes: `live_silver_key`, `live_quality_key`, `LiveQualityReport`, `gap_masks` (Task 3).
- Produces: `day_sources(store, days, root="") -> dict[date, Literal["hf", "live"]]` (live wins when its file exists; neither → `DataValidationError("silver day … is missing")`); `latest_silver_day` = newest non-empty day of either source; reports = monthly HF reports for months that have ≥ 1 `hf` day + daily live reports for `live` days; `SnapshotManifest.rows_by_source: dict[str, int]` (new field, default `{}` so old snapshot JSON still parses). Snapshot id must change when sources change (sources are part of the hashed input).

- [ ] **Step 1: Failing tests** — `test_live_wins_on_overlap`, `test_hf_only_window_unchanged` (same snapshot id as before for an HF-only fixture), `test_live_quality_gaps_excluded`, `test_missing_day_raises`, `test_latest_day_across_sources`, `test_rows_by_source_recorded`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `make test` PASS; `make test-integration` (snapshot on MinIO) PASS.
- [ ] **Step 5: Commit** `feat(training): snapshot reads HF and live silver (live wins per day)`.

### Task 9: Fill the window from HF raw

**Files:**
- Modify: `src/dbdelay/data/quality.py` (`gap_fields_for_days(silver, days: Sequence[date], station_evas) -> tuple[list[str], list[StationQuality]]`, refactored out of `_station_quality`/`_low_volume_hours`, monthly behaviour unchanged)
- Create: `src/dbdelay/live/hf_raw_fill.py` (`fill_hf_raw_days(store, stations, days, raw_dir, *, month: str) -> QualityReport` — builds each day with `build_hf_raw_silver`, writes `silver_key(day)` (source=hf) and one `quality_key(month)` report covering the filled days; refuses a month that already has an HF monthly report unless every day in it was produced by this fill (marker in `drops["hf_raw_fill_days"]`) and a CLI `hf_raw_fill_main(argv)`
- Modify: `Makefile` (`hf-raw-fill FROM=… TO=…`)
- Test: `tests/unit/test_quality.py` (add), `tests/unit/test_hf_raw_fill.py`

- [ ] **Step 1: Failing tests** — `test_gap_fields_for_days_matches_monthly` (full month → same result as `build_report`), `test_fill_writes_days_and_report`, `test_fill_refuses_real_monthly_report`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS. Real: `make hf-raw-fill FROM=2026-11-01 TO=<first live day − 1>` into MinIO; record rows/day.
- [ ] **Step 5: Commit** `feat(data): fill silver days from HF raw (until HF publishes the month)`.

### Task 10: Pipeline decision record + `drift_watch` DAG

**Files:**
- Modify: `src/dbdelay/training/pipeline.py` (`write_decision(ctx, request_id: str, decision: Literal["released","rejected","failed"], version: str | None, gate: GateDecision | None) -> None` → MinIO `control/decisions/<request_id>.json` as `RetrainRecord`), `pipelines/airflow/dags/training_pipeline.py` (read `request_id` from `dag_run.conf`, default `"scheduled-<run date>"`; final tasks call `write_decision` on both branches, `trigger_rule` so a rejection still records)
- Create: `src/dbdelay/monitoring/control.py` (`pending_request(store) -> RetrainRequest | None` = flag with state `requested`; `mark(store, request, state, now) -> RetrainRequest`), `pipelines/airflow/dags/drift_watch.py`
- Test: `tests/unit/test_monitoring_control.py`, `tests/unit/test_pipeline.py` (add), DAG import test (`make test-dags`)

**Interfaces:**
- `drift_watch`: schedule every 30 min, `max_active_runs=1`, catchup off. `PythonSensor(mode="reschedule", poke_interval=1800, timeout=1800, soft_fail=True)` on `pending_request` → task marks `picked_up` and returns `request_id` → `TriggerDagRunOperator(trigger_dag_id="training_pipeline", conf={"request_id": …}, wait_for_completion=True, poke_interval=60)` → task marks `done`.

- [ ] **Step 1: Failing tests** — control: `test_pending_only_when_requested`, `test_mark_picked_up_sets_time`; pipeline: `test_write_decision_released`, `test_write_decision_rejected_lists_failed_checks`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS; `make test-dags` PASS; restart `airflow-dag-processor`.
- [ ] **Step 5: Commit** `feat(pipelines): drift_watch DAG and retrain decision records`.

### Task 11: Laptop sync + publish

**Files:**
- Create: `src/dbdelay/monitoring/sync.py`
- Modify: `Makefile` (`retrain-sync`, `retrain-publish` added to `AWS_TARGETS` and `PY_AWS_TARGETS`), `pyproject.toml` scripts if the project registers CLIs there (follow `publish_main` pattern)
- Test: `tests/unit/test_monitoring_sync.py`

**Interfaces:**
- `retrain_sync(aws: ObjectStore, local: ObjectStore, *, today: date, now: datetime) -> SyncReport` — copies live silver + live quality days missing in MinIO (iterates expected days from the earliest live day; AWS role has no list → walk days from `first_day` arg / `monitoring/history.json` days), copies a `requested` S3 flag to MinIO and marks the S3 one `picked_up`.
- `retrain_publish(local, aws, local_pointer, aws_pointer, *, now) -> list[RetrainRecord]` — decisions in MinIO not yet in S3 `monitoring/retrains.json` (by `request_id`) are appended; for a `released` decision whose version is the local champion → `publish_champion(...)`.
- CLIs `retrain_sync_main`, `retrain_publish_main` (credential errors → one line, like `publish_main`).

- [ ] **Step 1: Failing tests** — `test_sync_copies_missing_days_only`, `test_sync_picks_up_flag_once`, `test_publish_appends_new_decisions_once`, `test_publish_releases_only_gate_passed_champion`, `test_publish_rejected_does_not_touch_pointer`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(monitoring): laptop retrain sync and publish`.

### Task 12: Terraform — monitor Lambda, schedule, IAM, alarms

**Files:**
- Modify: `infra/envs/prod/main.tf` (module `monitor` = `lambda_image` with API image, `command = ["monitor_handler.handler"]`, env `DATA_BUCKET`, `MODELS_BUCKET`, `MODEL_POINTER_PARAM`, `async_retries = 1`; API role + `s3:GetObject` on `monitoring/*`), `infra/modules/schedules/*` (04:30 Berlin monitor schedule), `infra/modules/observability/*` (3 alarms: monitor missing / drift `DriftShare > 0.30` 3×86400 / performance `BrierRatio > 1.10` 3×86400), outputs
- Monitor IAM: `s3:GetObject` on `models/*` + list prefix like the API, `silver/departures/source=live/*`, `silver/_quality/source=live/*`, `monitoring/*`, `control/*`; `s3:PutObject` on `monitoring/*`, `control/retrain_requested.json`; `ssm:GetParameter(s)` on the champion param.

- [ ] **Step 1:** `make tf-fmt tf-validate`, `tflint --chdir infra --recursive`, `make tf-scan` → clean (new findings fixed or documented in `infra/.trivyignore` with reason).
- [ ] **Step 2:** `make tf-plan` (owner `aws login` if expired) → only the expected adds/changes (Lambda, schedule, 3 alarms, IAM, API policy).
- [ ] **Step 3: Commit** `infra(prod): monitor Lambda, 04:30 schedule, drift/performance/missing alarms`.

### Task 13: Model Health page

**Files:**
- Modify: `frontend/package.json` (+ `recharts`), `frontend/src/api/types.ts`, `frontend/src/api/client.ts` (if fetchers live there), `frontend/src/pages/HealthPage.tsx`
- Create: `frontend/src/hooks/useMonitoring.ts`, `frontend/src/components/health/MonitoringStatus.tsx`, `DailyCharts.tsx` (lazy-loaded with `React.lazy`), `DriftTable.tsx`, `RetrainLog.tsx`
- Test: `frontend/src/pages/HealthPage.test.tsx` (extend), `frontend/src/components/health/*.test.tsx`

**Copy and states (from spec §4.6):** status values `OK` / `Watch` (any bad verdict in the last 3 days) / `Retrain requested` / `Monitor late` (newest day older than 2 days before today, UTC); charts: Brier vs champion test Brier with the ×1.10 limit, ROC-AUC, drift share vs 0.30, actual late rate vs mean predicted; drift table: column, score, method, drifted, "counted"; retrain log: date, reason (+ "forced demo"), decision, version. Empty state: "Daily monitoring starts after the first nightly check." Every chart has a visually-hidden table alternative.

- [ ] **Step 1: Failing tests** — `renders empty state without monitoring`, `shows Monitor late when newest day is old`, `shows Retrain requested`, `drift table marks calendar columns not counted`, `retrain log labels forced demo`, `chart has table alternative`.
- [ ] **Step 2:** `npm --prefix frontend test -- --run` → FAIL.
- [ ] **Step 3: Implement** (`npm --prefix frontend install recharts@<current 2.x/3.x exact>`; lazy chart chunk).
- [ ] **Step 4:** `make web-check` + build PASS; `npm audit --omit=dev --audit-level=high` 0.
- [ ] **Step 5: Commit** `feat(web): Model Health page with daily monitoring, drift and retrain log`.

### Task 14: Docs

**Files:** `docs/rules.md` §2 drift row, `docs/adr/0003-drift-without-evidently.md` (context, measurement from spec §9, decision, consequences), `docs/architecture.md` §5/§7/§9, `docs/prd.md` §6.3, `docs/runbook.md` (each of the 9 alarms + budget: meaning → first checks → fix), `docs/phases.md` Phase 9 ticks, README (monitoring + retrain commands).

- [ ] **Step 1:** write; `uv run pre-commit run --all-files`.
- [ ] **Step 2: Commit** `docs(phase-9): rules, ADR 0003, architecture, runbook`.

### Task 15: Deploy, real runs, loop, branch record

- [ ] **Step 1:** `make check`, `make web-check`, `make test-dags`, `make test-integration` green.
- [ ] **Step 2:** `make image-push` → tag in tfvars → `make tf-plan` → `make tf-apply` → `make web-deploy` → `make smoke-prod-live` (add a `/api/v1/monitoring` 200 check to `scripts/smoke_prod.py`, test-first).
- [ ] **Step 3:** Backfill: invoke the monitor for every live day since the first live silver day (`aws lambda invoke … {"day": "…"}`), record duration + max memory from the REPORT line; check history on the site (headless Chrome, 0 CSP violations).
- [ ] **Step 4:** Loop: if a real flag exists use it, else forced demo (3 consecutive days with `threshold_overrides`). `make retrain-sync` → (`make hf-raw-fill` if needed) → Airflow `drift_watch` picks it up → `training_pipeline` → decision → `make retrain-publish` → health page shows the retrain; if released, `make smoke-prod-live` shows the new version.
- [ ] **Step 5:** Final review (opus `code-reviewer`), fix confirmed findings test-first.
- [ ] **Step 6:** CHANGELOG entry, `Phases/phase-9-monitoring.md`, `Phases/README.md`, CLAUDE.md status/log; push; PR with the same summary.
