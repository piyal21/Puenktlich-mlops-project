# Phases — Pünktlich

> Build order, split into **phases** (not weeks). Finish a phase's exit criteria before starting the next.
> Each phase ends with a working, demoable state and a merged PR (or a few).
> Mark progress by ticking boxes. AI assistants: only work on the phase marked **▶ current**.

**Current phase:** ▶ Phase 0

| # | Phase | Main outcome | Key MLOps concepts |
|---|---|---|---|
| 0 | Foundations | Repo, tooling, local stack skeleton, safe AWS account | Reproducible envs, project structure |
| 1 | Data discovery & contracts | EDA, label definition, station list, schemas | Data contracts, leakage thinking |
| 2 | Historical ETL | Airflow backfill: HF → bronze → silver (validated) | Orchestration, idempotency, data quality |
| 3 | Features & baseline | Shared feature module, time split, baseline | Feature engineering, training/serving parity |
| 4 | Training & tracking | LightGBM + calibration, MLflow, registry, gate | Experiment tracking, model registry, promotion |
| 5 | Local serving & UI | FastAPI + React running on the local stack | Model serving, API design, packaging |
| 6 | Cloud infrastructure | Terraform: buckets, ECR, Lambda API, CloudFront | IaC, remote state, least privilege |
| 7 | Live data pipeline | Ingest + daily ETL Lambdas, live board | Scheduled ETL, secrets, rate limits |
| 8 | CI/CD | GitHub Actions with OIDC, scans, deploy, smoke test | CI/CD, supply-chain security |
| 9 | Monitoring & continuous training | Drift + performance, alarms, health page, retrain loop | Drift, model monitoring, CT |
| 10 | Hardening & showcase | Security review, runbook, model card, README, demo | Operability, communication |
| S | Stretch | DL challenger, live features, canary, i18n | Advanced patterns |

---

## Phase 0 — Foundations

**Goal:** a clean repo where `make up` starts the core local services, and an AWS account that can't surprise you with a bill.

Tasks
- [x] Create GitHub repo `puenktlich` (actual: `Puenktlich-mlops-project`) (public), add license (MIT), `.gitignore`, `.editorconfig`.
- [x] Put `prd.md`, `architecture.md`, `rules.md`, `phases.md`, `design.md` into `docs/`; `README.md` at root; add a short `CLAUDE.md` pointing to `docs/rules.md` and `docs/phases.md`.
- [x] `uv init` → `pyproject.toml` with package `dbdelay` in `src/`; dev deps: ruff, mypy, pytest, pytest-cov, moto, pre-commit.
- [x] `.pre-commit-config.yaml` (ruff, ruff-format, mypy, gitleaks, end-of-file-fixer).
- [x] `Makefile` targets: `setup`, `up`, `down`, `logs`, `lint`, `typecheck`, `test`, `test-integration`, `fmt`.
- [x] `docker-compose.yml` with **core** services: MinIO (+ bucket init), Postgres, MLflow server (Postgres backend, MinIO artifacts).
- [x] `src/dbdelay/config.py` (pydantic-settings), `logging.py`, `errors.py`, `storage.py` (S3/MinIO with endpoint override) + unit tests.
- [ ] ⏸ *Deferred until Phase 6 (owner decision).* AWS account safety (manual, one time): root MFA, IAM Identity Center admin user, region `eu-central-1`, **AWS Budgets alert at $1** (created manually now; Terraform later), billing alerts email.
- [ ] ⏸ *Deferred until needed (Phase 1 fixtures or Phase 7).* Register on DB API Marketplace, subscribe to **Timetables** (free plan), store keys in `.env` only.

Exit criteria
- `make up` → MinIO console, MLflow UI reachable; `make lint typecheck test` green.
- Budget alert exists; no access keys for root. *(Deferred with the AWS task.)*

You learn: project scaffolding, uv, pre-commit, Docker Compose, AWS account hygiene.

---

## Phase 1 — Data discovery & contracts

**Goal:** understand the data and freeze the decisions everything else depends on.

Tasks
- [ ] Download 2–3 months of `monthly_processed_data/data-YYYY-MM.parquet` from HF (`huggingface_hub`).
- [ ] `notebooks/01_eda.ipynb`: row counts per month/station, missing hours, `delay_in_min` distribution, share ≥ 6 min, cancellations, how `delay_in_min` behaves when no change time exists, cardinality of `line_number`/`final_destination_station`, DST behaviour.
- [ ] Call the live Timetables API for 2–3 stations; save sample `plan` and `fchg` XML to `tests/fixtures/`; confirm how `s@id` maps to HF `train_line_ride_id` / `train_line_station_num`.
- [ ] Choose ~30 stations → `configs/stations.yaml` (`eva`, `name`, `state`). Verify each has history across the training window.
- [ ] Freeze: label definition, cancelled handling, risk thresholds (initial), silver schema v1 → update `architecture.md` §3.3 if anything changed.
- [ ] Implement `dbdelay/data/schemas.py` (Pandera `SilverDepartures`) + tests.
- [ ] Write `docs/adr/0001-label-and-leakage-policy.md`.

Exit criteria
- EDA findings summarized at the top of the notebook (5–10 bullet points).
- Station list, label, schema frozen and documented. Fixture XML committed.

You learn: EDA for ML systems, data contracts, leakage awareness.

---

## Phase 2 — Historical ETL (Airflow)

**Goal:** a reliable, re-runnable pipeline that turns HF monthly files into validated silver partitions.

Tasks
- [ ] Add Airflow 3 to `docker-compose.yml` (profile `airflow`, LocalExecutor, Postgres metadata DB, custom image with `dbdelay` installed).
- [ ] `dbdelay/data/hf_backfill.py`: download month → bronze (MinIO) → filter stations → conform to silver schema (tz localize Europe/Berlin → UTC, `event_id`, `is_late`) → dedupe.
- [ ] `dbdelay/data/quality.py`: per-partition stats (rows in/out, drop reasons, null rates, late rate) + quarantine.
- [ ] DAG `backfill_history`: params `months: list[str]`; dynamic task mapping per month; partition overwrite = idempotent.
- [ ] Unit tests: conform, DST edge cases (last Sunday of March/October), dedupe, idempotent write. Integration test on a small parquet sample in MinIO.
- [ ] Backfill the full training window (e.g. 9–12 months).

Exit criteria
- Running the DAG twice for the same month produces identical output (row counts & hash).
- Quality report per month visible in task logs; bad rows quarantined with reasons.

You learn: Airflow 3 TaskFlow, dynamic task mapping, medallion architecture, idempotency, data validation.

---

## Phase 3 — Features & baseline

**Goal:** one feature module used everywhere, a time-based split, and a baseline to beat.

Tasks
- [ ] `dbdelay/features/calendar.py` (local hour, weekday, holidays by state), `spec.py` (FeatureSpec: names, dtypes, category levels, `OTHER` mapping), `build.py`.
- [ ] Leakage guard: a test that fails if forbidden columns (`changed_departure_utc`, `delay_min`, `is_cancelled`, `is_late`) reach the feature matrix.
- [ ] `dbdelay/training/split.py`: train/valid/test by time (config-driven) + snapshot writer (`gold/training_sets/<snapshot_id>/` + `snapshot.json` with content hash).
- [ ] `dbdelay/training/baseline.py`: late-rate lookup by (eva, train_type, hour_local, weekday) with backoff to coarser groups.
- [ ] `dbdelay/training/evaluate.py`: Brier, ROC-AUC, PR-AUC, log loss, ECE, per-slice metrics, calibration plot.

Exit criteria
- Baseline metrics on the test split recorded (in notebook or MLflow).
- Feature builder is deterministic and fully unit-tested.

You learn: feature engineering for tabular ML, time-series-aware validation, baselines, calibration metrics.

---

## Phase 4 — Training, tracking, registry, gate

**Goal:** `training_pipeline` DAG trains, tracks, registers and (conditionally) promotes a model — locally.

Tasks
- [ ] `train.py`: LightGBM with native categoricals, early stopping on valid, small param grid from `configs/training.yaml`, seed from config.
- [ ] `calibrate.py`: isotonic on valid → export thresholds to `calibrator.json`; apply with `numpy.interp`.
- [ ] MLflow logging: params, metrics, artifacts (plots, feature importance, model card draft), `mlflow.log_input` for the snapshot, tags (`git_sha`, `snapshot_id`).
- [ ] Register model `puenktlich-delay`; set alias `@challenger`.
- [ ] `gate.py`: rules from `configs/training.yaml`; champion re-evaluated on the same test set.
- [ ] `registry/artifacts.py` + `release.py`: export `models/<version>/` with `manifest.json` + SHA-256; pointer abstraction (`pointer.py`: local file now, SSM later); set `@champion`.
- [ ] DAG `training_pipeline` (see `architecture.md` §5.2) — gate rejection ends **green**.
- [ ] `scripts/rollback.py` + `make rollback`.

Exit criteria
- First champion exists in MLflow and in MinIO `models/<v>/`, and beats the baseline by the configured margin.
- A second run with no improvement is **rejected** and the DAG finishes successfully.

You learn: MLflow tracking & registry, aliases, champion/challenger, reproducibility, artifact integrity.

---

## Phase 5 — Local serving & UI

**Goal:** the full user experience works on your laptop against MinIO.

Tasks
- [ ] `dbdelay/serving/model_loader.py` (pointer → download → verify checksums → load; TTL cache), `board.py`, `explain.py`.
- [ ] FastAPI app: routes from `architecture.md` §7, Pydantic schemas, problem+json errors, request ids, OpenAPI at `/api/docs`.
- [ ] Local board source: `scripts/seed_sample_data.py` (+ `make seed`) writes a realistic `live/boards/latest.json.gz` into MinIO from fixtures.
- [ ] `services/api/Dockerfile` (Lambda base image) — also runnable locally via uvicorn in compose.
- [ ] Frontend (Vite + React + TS + Tailwind) following `design.md`: Station search, Board, Departure detail, Model Health (reads local JSON), About.
- [ ] Tests: API route tests (TestClient, moto/MinIO), frontend component tests.

Exit criteria
- `make up` (profile `app`) → open `http://localhost:5173`, search a station, see risk badges and explanations.
- API returns 503/`prediction: null` gracefully when no champion exists.

You learn: model serving patterns, API design, containerizing for Lambda, frontend–API integration.

---

## Phase 6 — Cloud infrastructure (Terraform)

**Goal:** the API and website live on AWS, fully defined in Terraform.

Tasks
- [ ] `infra/bootstrap`: state bucket (versioned, encrypted), GitHub OIDC provider, `gh-plan` and `gh-deploy` roles (trust scoped to repo/environment). Apply once locally.
- [ ] Modules: `data_lake`, `ecr`, `lambda_image`, `http_api`, `web_cdn`, `ssm`, `observability` (SNS + email), `budget`.
- [ ] `envs/prod` with S3 backend (`use_lockfile = true`), default tags.
- [ ] Push images manually once (later CI does it); deploy API Lambda; upload the first champion to `models/` and set the SSM pointer (via the release code, not by hand).
- [ ] CloudFront: S3 origin (OAC) + `/api/*` → API Gateway; security headers; 60 s cache for board responses.
- [ ] `terraform plan` shows **no changes** after apply (no drift). Store secrets with `scripts/set_secrets.sh`.

Exit criteria
- Public URL serves the UI; `/api/v1/health` returns the model version.
- Budget alarms exist in Terraform; `trivy config infra/` clean (or findings documented).

You learn: Terraform modules, remote state & locking, IAM least privilege, CloudFront/OAC, serverless deployment.

---

## Phase 7 — Live data pipeline

**Goal:** real departures flow in every 15 minutes, and yesterday becomes labeled silver data every night.

Tasks
- [ ] `dbdelay/timetables/client.py` (httpx, headers, timeouts), `ratelimit.py` (≤ 50 calls/min), `parser.py` (defusedxml; `plan` + `fchg`).
- [ ] `jobs` image + handlers: `ingest.py` (modes `plan`, `changes`) → bronze JSONL.gz, `live/plans/latest`, `live/boards/latest`; emits `EventsIngested`.
- [ ] `etl_daily.py`: bronze D-1 → silver `source=live` (same schema + validation as HF).
- [ ] Terraform: `jobs` Lambdas, `schedules` module (EventBridge Scheduler, Europe/Berlin), IAM scoped per function, alarms "ingestion stopped" + "ingest errors".
- [ ] **Reconciliation check:** for overlapping days, compare live silver vs. HF silver (match rate on `event_id`, delay agreement) and record it in the data-quality report.

Exit criteria
- Board on the website shows real departures with `data_as_of` < 20 min.
- 3 consecutive days of live silver written and validated; reconciliation ≥ agreed threshold (document the number).

You learn: API-based ETL, rate limiting & retries, secrets management, scheduled serverless jobs, data reconciliation.

---

## Phase 8 — CI/CD

**Goal:** every change is checked automatically, and merging to `main` deploys safely.

Tasks
- [ ] `ci.yml`: Python (ruff, mypy, pytest + coverage), frontend (lint, typecheck, vitest, build), Terraform (fmt, validate, tflint), security (gitleaks, pip-audit, npm audit, trivy config), docker build for both images.
- [ ] `infra-plan.yml`: on PRs touching `infra/`, `terraform plan` with `gh-plan` role, post as PR comment.
- [ ] `cd.yml` (environment `production`): OIDC → build/push images (tag = SHA) → trivy image scan → `terraform apply` → frontend build + S3 sync + CloudFront invalidation → smoke test.
- [ ] Branch protection on `main`; Dependabot for pip, npm, GitHub Actions, Docker.
- [ ] README badges (CI status).

Exit criteria
- A PR shows green checks + a Terraform plan comment; merging deploys and the smoke test passes.
- No AWS access keys stored in GitHub.

You learn: CI/CD design, OIDC federation, image scanning, deployment gates, supply-chain hygiene.

---

## Phase 9 — Monitoring & continuous training

**Goal:** the system watches itself and retrains when needed — and shows it publicly.

Tasks
- [ ] `dbdelay/monitoring/performance.py` (daily Brier/AUC/ECE on D-1), `drift.py` (Evidently `DataDriftPreset`, weekday/weekend-matched reference), `publish.py` (HTML report + `latest.json` + `history.json`), `triggers.py` (3-day rules).
- [ ] `monitor` handler + schedule; CloudWatch metrics `DriftShare`, `BrierDaily`, `AUCDaily`, `LabeledEvents`; remaining alarms from `architecture.md` §9.
- [ ] DAG `drift_watch` (deferrable S3 sensor on `control/retrain_requested.json` → trigger `training_pipeline` → archive flag).
- [ ] Switch `pointer.py` to SSM in prod; `training_pipeline` release step writes to real S3 + SSM; smoke test waits for the API to report the new version.
- [ ] Model Health page reads `/health/latest.json` + `history.json`; links to the latest drift report.
- [ ] `docs/runbook.md`: every alarm → meaning → first checks → fix.

Exit criteria
- Health page shows ≥ 7 days of daily metrics.
- One full loop demonstrated end-to-end (can be forced by lowering a threshold in config): flag → retrain → gate decision → (release or rejection) → visible on the health page.

You learn: data vs. prediction vs. performance drift, alert design, continuous training, closing the loop.

---

## Phase 10 — Hardening & showcase

**Goal:** production-quality finish and a portfolio that sells itself.

Tasks
- [ ] Security review against `architecture.md` §12 (IAM policies, bucket policies, headers, input limits); fix or document findings.
- [ ] Threat model (1 page, STRIDE-lite) in `docs/`.
- [ ] Model card (`docs/model_card.md`) generated from the champion's metrics.
- [ ] Performance: measure cold/warm latency, image sizes; record real numbers.
- [ ] Accessibility check (keyboard, contrast, screen reader labels) per `design.md`.
- [ ] Finalize README: real screenshots, real metrics (no invented numbers), architecture diagrams, "what I learned", demo GIF/video (2 min).
- [ ] Before the Free plan ends: decide to upgrade the account (keeps the demo online).
- [ ] Add the project to CV + LinkedIn (3 bullet points with measured results).

Exit criteria
- A stranger can understand the project from the README in 5 minutes and run it locally with `make up`.
- All CI checks, scans and alarms green for 7 days.

You learn: security reviews, documentation, communicating engineering work.

---

## Stretch phase (optional, pick any)

- [ ] **DL challenger:** PyTorch entity-embedding MLP → ONNX → onnxruntime; competes through the same gate.
- [ ] **Live-context features:** `station_late_share_prev_2h` as of `planned − 60 min` + parity test.
- [ ] **Canary release:** Lambda alias weighted routing (e.g. 10 % → new model) with automatic rollback on errors.
- [ ] **DVC** for gold snapshots.
- [ ] **i18n** German/English toggle.
- [ ] **Load test** (k6/Locust) with results in README.
- [ ] **Grafana** dashboard on local stack reading CloudWatch.
