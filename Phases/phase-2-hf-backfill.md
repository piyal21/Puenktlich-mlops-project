# Phase 2 — Historical ETL with Airflow (HF backfill)

**Goal:** turn the decisions from Phase 1 into a re-runnable pipeline. It downloads 9 months of Deutsche Bahn
history, keeps only our 30 stations, cleans every row into the frozen "silver" format, and sets bad rows aside
with a reason. It must give byte-identical output when re-run.

**Status:** ✅ done on branch `phase-2/hf-backfill`, waiting for the owner's merge. The branch is kept.

---

## 1. What was built

| Deliverable | File(s) | What it is |
|---|---|---|
| Month helpers | [`src/dbdelay/data/months.py`](../src/dbdelay/data/months.py) | Validates `YYYY-MM`, shifts months, lists a month's days and its UTC start/end. |
| Station loader | [`src/dbdelay/data/stations.py`](../src/dbdelay/data/stations.py) | Reads `configs/stations.yaml` into typed objects and maps old station ids (e.g. Berlin 8011160) to current ones. |
| Download to bronze | [`src/dbdelay/data/hf_backfill.py`](../src/dbdelay/data/hf_backfill.py) (`ingest_month`) | Downloads one month from Hugging Face at a fixed dataset version and stores it unchanged in MinIO with a manifest (checksum, version, time). |
| Build silver | same file (`read_departures`, `build_silver_month`) | Reads months M-1, M and M+1 with DuckDB (only our stations), cleans with pandas, writes one file per UTC day. |
| Cleaning rules | [`src/dbdelay/data/silver.py`](../src/dbdelay/data/silver.py) (`conform_hf`) | Station ids, ride ids, UTC times, the label, row ids and de-duplication, checked against the Phase 1 contract. |
| Quarantine + quality | [`src/dbdelay/data/quality.py`](../src/dbdelay/data/quality.py) | Sets rows that cannot be trusted aside with a reason. Writes a JSON quality report per month (counts, late rate, per-station volume, drops, gaps). |
| Airflow stack | [`docker-compose.yml`](../docker-compose.yml), [`pipelines/airflow/Dockerfile`](../pipelines/airflow/Dockerfile) | Airflow 3.3.2 (API server, scheduler, DAG processor, init job) in Docker, with our package installed. |
| DAG | [`pipelines/airflow/dags/backfill_history.py`](../pipelines/airflow/dags/backfill_history.py) | `plan_months → ingest (per month) → build_silver (per month) → summarize`. |
| Secrets helper | [`scripts/ensure_airflow_env.py`](../scripts/ensure_airflow_env.py) | Adds missing Airflow secrets to `.env` with random values and never prints them. |
| Tests | `tests/unit/test_*` (months, stations, silver, quality, conform, report, hf_ingest, hf_build, ensure_airflow_env), `tests/integration/test_hf_backfill_minio.py`, `pipelines/airflow/tests/check_dags.py` | Unit tests for every rule, a real-MinIO test that builds a month twice, and a DAG-shape check inside the Airflow image. |

## 2. How it works

```text
Hugging Face (piebro/deutsche-bahn-data, pinned version)
        │  ingest_month  (skips months already in bronze unless force_download)
        ▼
MinIO  bronze/hf/month=YYYY-MM/data.parquet + _manifest.json      ← raw, never changed
        │  build_silver_month  (reads M-1, M, M+1; checks the checksum first)
        ▼
DuckDB: keep only our 30 stations and the UTC window of month M
        │
        ▼
pandas conform_hf ──► bad rows ─► silver/_quarantine/source=hf/month=YYYY-MM/part-0.parquet
        │
        ▼  contract check (Pandera, SilverDepartures)
silver/departures/source=hf/date=YYYY-MM-DD/part-0.parquet        ← one file per UTC day, even when empty
silver/_quality/source=hf/month=YYYY-MM.json                      ← quality report
```

- **Why read three months:** the history files are split by German local time and overlap at the edges. A UTC
  month can therefore include rows from the previous or next file. Rows seen twice are de-duplicated, and the
  newest download wins.
- **Why one file per day, even an empty one:** re-running a month always overwrites every day of that month.
  An old file can never survive a change (for example, after a station is removed).
- **Why byte-identical:** fixed column schema, fixed sort order, fixed compression (zstd). The quality
  report records a `content_hash` of the silver rows, so two runs can be compared.

## 3. Key decisions

| Decision | Why |
|---|---|
| Airflow as separate services (API server, scheduler, DAG processor) with LocalExecutor | This is the real Airflow 3 production layout, and it still runs on one laptop. There is no Celery/Redis to maintain. |
| DuckDB filters, pandas cleans | DuckDB reads only the 30 stations out of the ~650 MB month file. The cleaning logic stays in plain, testable pandas. |
| Airflow image installed under Airflow's official constraints | Plain `pip install` produced package conflicts. The constraints file is Airflow's tested set. One exception: pandas stays < 3 (the project and Pandera need it), so that one line is removed from the constraints. |
| `huggingface-hub < 2` | The version pinned by the Airflow constraints needs it. It is the same range in the venv and the image. |
| Pinned dataset version (commit sha) in the manifest | Re-running later downloads exactly the same data or detects the change. |
| Checksum checked before building | A broken or half-uploaded bronze file stops the month. It never produces silver. |
| Trains crossing the spring daylight-saving night are quarantined (`delay_mismatch_utc`) | The dataset computes delay on local clock times, which is wrong by one hour across the switch. |
| `build_silver` never retries automatically | A data-validation failure must be looked at, not retried silently. |
| Lazy imports inside DAG tasks (ruff rule PLC0415 turned off for `dags/` only) | This is Airflow's best practice: it keeps DAG parsing fast. |

## 4. How it was tested

- `make check` (ruff, format, mypy `--strict`, unit tests): ruff + format + mypy `--strict` clean, **151 unit tests passed, 99 % coverage**
- `make test-integration` (real MinIO, builds a month twice and compares hash and bytes): **3 passed** (real MinIO; a month built twice gives identical hash and bytes)
- `make test-dags` (DAG parses inside the Airflow image; tasks, params and default months): **DAG check passed** inside the Airflow image
- Real backfill 2025-12 … 2026-08 in Airflow: run `manual__2026-09-27T14:21:12` → **success**: 9 bronze months (~650 MB each), **274 silver day files**, 9 quarantine files, 9 quality reports; **3,937,131 rows read → 3,724,008 silver rows**; late rate 22.2–27.9 % per month; 37 rows quarantined (all 2026-03, `delay_mismatch_utc`, the spring DST night); 2026-08 = 408,638 rows, identical to the reviewer's independent probe
- Determinism on real data (2026-03 rebuilt, same `rows_out` and `content_hash`): second run on the fixed image (`manual__2026-09-28T17:03:07`, ingest skipped, all silver rebuilt in 5 min 25 s) → **identical `rows_out`, late rate, quarantine, drops and `content_hash` for all 9 months**
- Independent final code review: 0 Critical, 2 Important (placeholder Airflow secrets; whole 650 MB files in memory) — **both fixed, tests written first**; 8 Minor deferred

## 5. How to run it

```bash
make up              # MinIO, Postgres, MLflow
make airflow-env     # adds Airflow secrets to .env (values never printed)
make airflow-up      # builds the image and starts Airflow at http://localhost:8080
make test-dags       # DAG check inside the image
make backfill        # triggers backfill_history for the 9 default months
```

Rebuild one month: trigger `backfill_history` with the config `{"months": ["2026-03"], "force_download": false}`.

## 6. Known gaps and risks carried forward

- **PC sleep kills running Airflow tasks** (containers freeze, the task's 10-minute token expires, heartbeat gets 403). It happened twice during the real run; failed tasks were cleared and finished. Keep the PC awake during backfills.
- **Data gaps flagged by the quality reports** (the source dataset, not our code): low-volume hours on 2026-02-02 18–21h, 2026-03-16 18–21h, 2026-04-08 06–11h, 2026-08-27/28 mornings; 18 station-days with a volume drop, e.g. Potsdam Hbf 2026-03-23…31. Phase 3 decides whether to mask them in training.
- **Month edges:** 2025-12 has no 2025-11 bronze and 2026-08 has no 2026-09 bronze (`edge_complete` false) — the first/last UTC hours of the window may miss a few rows.
- **Deferred review minors:** HF network errors (httpx) not wrapped as `ExternalServiceError` (Airflow still retries); Berlin alias duplicates tie-break on id text; the report lacks pre-filter counts (`not_supported_station` is always 0); the quarantine file has no fixed schema and can count overlap rows twice; `plan_months` retries bad input; `build_silver` does not retry transient MinIO errors; `force_download` of a month does not rebuild its neighbours.
- **Owner decisions flagged:** `# noqa: PLR0913` on `ingest_month`; ruff `PLC0415` off for `pipelines/airflow/dags/**`.
- Train-type-mix shift flag (suggested in Phase 1) not built; risk thresholds still not in a config file.

## 7. What comes next

Phase 3 builds the feature pipeline and the first baseline model on this silver data. It starts only when the
owner says go.
