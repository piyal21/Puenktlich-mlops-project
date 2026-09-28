# Phase 2 — Historical ETL (`backfill_history`) — design

**Status:** approved in chat by owner 2026-09-26 (sections 1 + 2); this written spec awaits review.
**Branch:** `phase-2/hf-backfill` · **Source of truth for rules:** `docs/rules.md`, `docs/architecture.md` §3, ADR 0001.

## 1. Goal and success criteria

Turn the Hugging Face monthly history into validated **silver** data in local MinIO for the 30 supported
stations, with a re-runnable Airflow 3 pipeline. Output feeds Phases 3–4 (features, training).

Success (from `docs/phases.md` Phase 2):
1. Running the DAG twice for the same month produces **identical silver output** (row counts + content hash).
2. A **quality report per month** is written and visible in task logs.
3. Bad rows are **quarantined with reasons**; nothing is silently fixed.
4. Unit tests: conform, DST edges (last Sunday of March/October), dedupe, idempotent write. Integration test on a
   small parquet sample in MinIO.
5. The full training window is backfilled: **9 months, 2025-12 → 2026-08** (DAG param, default).

Out of scope: gold/training snapshots (Phase 3–4), live ingestion (Phase 7), `training_pipeline`/`drift_watch` DAGs,
triggerer service (needed only for deferrable sensors, Phase 9).

## 2. Decisions taken in brainstorming

| Topic | Decision |
|---|---|
| Airflow deployment | Split services: `airflow-api-server`, `airflow-scheduler`, `airflow-dag-processor` + one-shot `airflow-init`; **LocalExecutor**; metadata DB `airflow` in the existing Postgres; compose profile `airflow`. |
| Engine | **DuckDB** filters the parquet (stations + aliases, departures, date window); **pandas** does the conform in one function reusable by the live ETL. |
| Airflow in the dev venv | **No** (Airflow does not run natively on Windows). DAG tests run inside the Airflow container. All logic lives in `src/dbdelay/` and is unit-tested without Airflow. |
| Station name in silver | From `configs/stations.yaml` (identical for HF and live). |
| Month edges | HF files overlap at month edges. Silver for month M is built from bronze **M-1, M, M+1** (those present), keeping rows whose planned **UTC date** is in M. |
| Determinism | `ingested_at` = bronze download time from the manifest; rows sorted by `event_id` before writing. |
| Versions | `apache/airflow:3.3.2-python3.12` (latest stable on 2026-09-26); pin by digest in the Dockerfile. |

## 3. Storage layout (bucket `puenktlich-local`)

```
bronze/hf/month=YYYY-MM/data.parquet          # HF file, byte-identical
bronze/hf/month=YYYY-MM/_manifest.json        # {month, hf_repo, hf_path, hf_revision, sha256, size_bytes, downloaded_at}
silver/departures/source=hf/date=YYYY-MM-DD/part-0.parquet      # contract rows, one file per UTC day
silver/_quarantine/source=hf/month=YYYY-MM/part-0.parquet       # rejected rows + `quarantine_reason` (one file per month)
silver/_quality/source=hf/month=YYYY-MM.json                    # quality report
```

One file per partition → "overwrite partition" is a single `put` (no delete needed; `ObjectStore` has none by design).
**Every day of month M always gets both files** (silver and quarantine), empty if there are no rows, so a re-run after
any change fully replaces the previous output and no stale partition can survive.

## 4. Components

### `src/dbdelay/data/stations.py`
- `Station` (pydantic, frozen): `eva`, `name`, `state`, `hf_aliases: tuple[str, ...] = ()`.
- `load_stations(path) -> list[Station]` (uses `yaml.safe_load`; validates EVA pattern, uniqueness incl. aliases).
- `alias_map(stations) -> dict[str, str]` — every EVA and alias → canonical EVA.

### `src/dbdelay/data/hf_backfill.py`
- `HF_REPO = "piebro/deutsche-bahn-data"`, file path `monthly_processed_data/data-{month}.parquet`.
- `BronzeManifest` (pydantic).
- `ingest_month(month, store, *, force=False) -> BronzeManifest` — if a manifest exists and `force` is false, return it
  (no download). Otherwise download via `huggingface_hub.hf_hub_download` to a temp dir, compute SHA-256, `put` file +
  manifest. Network errors → `ExternalServiceError`.
- `read_departures(store, month, stations, workdir) -> RawMonth` — copies bronze M-1/M/M+1 (if present) to `workdir`,
  DuckDB query: `ltrim(eva,'0')` in station/alias EVAs, `departure_planned_time is not null`, planned local time within
  M ± 1 day (UTC filter is applied after localisation). Returns the pandas frame, `ingested_at` per source file, and which
  neighbours were present (edge flags).

### `src/dbdelay/data/silver.py`
- `make_event_id(eva, ride_id, planned_utc) -> str` — SHA-1 hex of `f"{eva}|{ride_id}|{planned_utc:%Y-%m-%dT%H:%MZ}"`.
- `conform_hf(raw, stations) -> ConformResult(silver, quarantine, drops: dict[str, int])`:
  1. map EVA (strip zeros, apply aliases); station name from config;
  2. `ride_id` = `id` minus `-<stop>` suffix; `stop_index` = suffix (int16);
  3. localise planned/changed times Europe/Berlin with `ambiguous="NaT"`, `nonexistent="NaT"` → drop + count
     (`dst_ambiguous_or_nonexistent`), convert to UTC `datetime64[us, UTC]`;
  4. cancelled → `delay_min`/`is_late` null; otherwise `delay_min` from HF, `is_late = delay_min >= 6`;
  5. `train_type` upper-cased/stripped; `source="hf"`; `ingested_at` from manifest;
  6. keep rows whose planned UTC date is in M (`out_of_month` count);
  7. dedupe on `event_id`, keep latest `ingested_at` (tie → first in `id` order) (`duplicate` count);
  8. row-level checks → quarantine with reason (see §5); remaining rows must pass `validate_silver`.
- `write_partitions(df, store, prefix, days)` — per day: sort by `event_id`, write parquet (pyarrow, fixed writer
  options) with `put_bytes`.
- `content_hash(df) -> str` — stable hash of the sorted frame (used by tests and the quality report).

### `src/dbdelay/data/quality.py`
- `split_quarantine(df) -> (ok, quarantined)` with reasons (as implemented): `bad_id` (covers ride id + stop index),
  `missing_train_type`, `missing_cancel_flag`, `missing_delay`, `delay_mismatch` (incl. missing changed time);
  `conform_hf` adds `delay_mismatch_utc` for trains straddling a DST switch.
- `QualityReport` (pydantic) + `build_report(...)`: rows in / after station filter / out; drops and quarantine by
  reason; null rates per column; late and cancelled rate; per-station daily counts with flags
  (`volume_drop`: a day < 50 % of the station's monthly median; `low_volume_hours` 06–21 local < 25 % of that hour's
  median); `edge_complete: {prev: bool, next: bool}`; `content_hash`.
- Report is logged (Powertools JSON) and written to `silver/_quality/...`.

### `pipelines/airflow/dags/backfill_history.py` (thin)
- `@dag(schedule=None, params={"months": [...9 months...], "force_download": False})`, `from airflow.sdk import dag, task`.
- `plan_months` → validates `YYYY-MM` strings; `ingest.expand(month=...)`; `build_silver.expand(month=...)`
  (downstream of **all** ingests, so neighbours exist); `summarize` logs a table of monthly reports.
- `max_active_tis_per_dag` limited (2) to cap memory.

## 5. Error handling

| Case | Behaviour |
|---|---|
| Row breaks a row-level rule | Quarantined with reason; counted; month continues. |
| DST-ambiguous / nonexistent time | Dropped and counted (ADR 0001). |
| Row outside the 30 stations / arrival-only / outside month M | Filtered, counted. |
| Missing column / wrong dtype / contract failure after the split | `DataValidationError` → task fails, **nothing written** for that month. |
| HF download error | `ExternalServiceError`; Airflow retries (2, exponential). |
| Neighbour month missing in bronze | Month still built; `edge_complete` false in the report (edge days may be incomplete). |

## 6. Infrastructure

- `pipelines/airflow/Dockerfile`: `FROM apache/airflow:3.3.2-python3.12` pinned by its sha256 digest (resolved with `docker pull` during implementation); copy `pyproject.toml`, `uv.lock`,
  `src/`; install `dbdelay[pipelines]` with its locked runtime deps (export via `uv export --no-dev --extra pipelines`) under the Airflow constraints;
  build fails on conflicts. `configs/` mounted read-only.
- `docker-compose.yml` profile `airflow`: `airflow-init` (creates DB `airflow` if missing via `psql`, `airflow db migrate`,
  admin user), `airflow-api-server` (127.0.0.1:8080), `airflow-scheduler`, `airflow-dag-processor`. Env from `.env`:
  `AIRFLOW_ADMIN_PASSWORD`, `AIRFLOW_SECRET_KEY`, `AIRFLOW_FERNET_KEY`, storage creds; `STORAGE_ENDPOINT_URL=http://minio:9000`.
- `.env.example` gains the Airflow keys (placeholders). `Makefile`: `airflow-up`, `airflow-down`, `test-dags`,
  `backfill` (triggers the DAG via the Airflow CLI in the container).

## 7. Dependencies (owner approved 2026-09-26)

- `apache-airflow==3.3.2` — Docker image only.
- `huggingface_hub` — moves from dev to a new optional extra `dbdelay[pipelines]` (installed in the Airflow image and the
  dev venv, **not** in the future Lambda images, which don't download from HF).

## 8. Testing

| Level | What |
|---|---|
| Unit (`tests/unit/`, no Docker/network) | stations loader + alias map; `make_event_id` exact format; conform: EVA strip + alias, ride_id/stop_index, UTC conversion, **both DST switches** (2025-10-26, 2026-03-29), cancelled nulls, label, train_type; out-of-month filter; dedupe; quarantine reasons; quality report numbers + flags; `write_partitions` idempotency (moto S3, same bytes twice); `ingest_month` skip-if-present (mocked download). Synthetic HF-shaped frames built in code. |
| Integration (`tests/integration/`, MinIO) | tiny synthetic month uploaded as bronze → `build_silver` twice → identical `content_hash` and objects; quarantine + report present. |
| DAG (`make test-dags`, inside the Airflow container) | DagBag has no import errors; `backfill_history` has the expected tasks/params. |
| Real run | 9-month backfill; numbers from the reports go into `Phases/phase-2-hf-backfill.md` and `CHANGELOG.md`. |

## 9. Phase-end deliverables

`CHANGELOG.md` entry, `Phases/phase-2-hf-backfill.md`, merge message, CLAUDE.md status/log, docs updated
(`architecture.md` §3.2 bronze manifest + quarantine/quality prefixes, §14 layout; `phases.md` ticks).
