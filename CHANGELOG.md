# Changelog

One entry per phase branch (owner rule, from Phase 2 on). Phases 0–1 are described in [`Phases/`](Phases/).
Only measured numbers; anything not measured is `TBD`.

## Phase 8 — CI/CD, route A (`phase-8/ci-cd`)

**Built**
- `.github/workflows/ci.yml` on every PR and push to `main` (read-only token, no AWS access, actions pinned by SHA,
  no persisted checkout token): `python` (uv lock check, ruff, mypy, unit tests with an 80 % coverage gate,
  pre-commit), `frontend` (lint, types, Vitest, build, `npm audit --omit=dev`), `terraform` (fmt, validate, tflint,
  Trivy config), `security` (gitleaks over the full history, pip-audit on both Lambda images and the MLflow image),
  `images` (requirements match `uv.lock`, docker build of both images, Trivy image scan).
- `main` protected: PR + all 5 checks required, no force-push or deletion (owner keeps admin override).
- `.github/dependabot.yml`: weekly uv, pip (MLflow image), npm, GitHub Actions, Docker, Terraform.
- `make image-scan`; README CI badge.
- Security fixes found by the new checks and deployed to AWS: opentelemetry-api 1.45.2, certifi 2026.10.23,
  vite (npm audit fix), current Lambda base image + `dnf upgrade --releasever=latest`, MLflow image packages
  (all but mlflow), version pins in all 9 Terraform modules (tflint).

**Key decisions**
- Owner (2026-11-18): route A of ADR 0002 — checks only in GitHub, deploys stay laptop `make` targets (the account
  blocks GitHub OIDC; B is an irreversible paid upgrade, C a new account). No `infra-plan.yml` / `cd.yml`.
- Image scan fails on *fixable* HIGH/CRITICAL only; `/usr/local/bin/aws-lambda-rie` (local emulator in AWS's base
  image, never runs on Lambda) is skipped.
- MLflow 3.16.1 server advisories (GHSA-j7qq-vr8c-xvw3, GHSA-2wqp-q3c6-ccj5, GHSA-wqmh-qccw-pqwf) are ignored in CI
  with a written reason: local-only server on 127.0.0.1; upgrade together with the mlflow-skinny training client.

**Tested**
- PR #5: all 5 jobs green on the final commit (python 3m50s, images 3m11s, terraform 45s, frontend 34s, security 18s).
- Locally: pip-audit clean (Lambda images), npm audit 0 vulnerabilities, tflint 0 issues, gitleaks 135 commits no leaks,
  `make image-scan` exit 0 on both images, pre-commit all hooks passed.
- AWS after the security rollout: 4 Lambdas updated (api 388.6 MB, jobs 338.6 MB compressed), `make smoke-prod-live` 10/10.
- Review (opus): 0 Critical, 4 Important fixed (lock check, image scan, coverage gate, Dependabot/requirements note).

**Known gaps / open items**
- No automated deploys (route A). Dependabot `uv` PRs that touch Lambda runtime deps need `make api-requirements
  jobs-requirements` on the PR branch before the `images` check passes.
- MLflow server 3.16.1 advisories (local-only) until the server + client upgrade.
- `make test-dags` and integration tests still run only locally (need Docker/the local stack).
- From Phase 7: owner to delete 4 old state versions holding the DB API keys and rotate the keys.

**Docs touched**
- `docs/adr/0002-aws-account-type.md` (decision A), `docs/architecture.md` §10, `docs/phases.md`, README (badges,
  security line), CLAUDE.md, `Phases/phase-8-ci-cd.md`, `Phases/README.md`.

## Phase 7 — Live data pipeline (`phase-7/live-pipeline`)

**Built**
- `dbdelay.timetables`: DB Timetables API client (httpx, 10 s timeout, tenacity retries on 429/5xx/network only),
  sliding-window limiter (50 calls/min, every attempt counts), `defusedxml` parser for `plan`/`fchg`, bronze JSONL.gz.
- `dbdelay.live`: plan cache (Berlin hours H−3…H+6, ≤ 70 plan calls/run, nearest first), live board builder with
  `via`, 15-min ingest run (time budget, 429 stop, failed station keeps its previous *live* rows), nightly
  bronze → silver `source=live` ETL (same contract and validation as HF), live quality report with the HF gap
  fields (`low_volume_hours`, `stations[].volume_drop_days`) plus runs/gaps/plan-hours per station, HF raw
  parity check and reconciliation (`make parity-check`, `make reconcile`).
- `jobs` image (336.1 MB compressed) with `ingest` and `etl_daily` Lambdas, EMF metrics `EventsIngested`,
  `EmptyStations`; Terraform: jobs ECR, 2 Lambdas with own roles, 2 schedules, 4 alarms, async retries,
  `board_mode` switch (sample board kept for rollback), DB API keys in SSM SecureString **outside** Terraform.
- API `train.via` + website "Going to (any stop on the way)" (additive, owner-approved); `make smoke-prod-live`.
- Live on the public site since 2026-11-18 17:20 UTC.

**Key decisions**
- Owner (2026-11-12): all-stops "Going to" (`via`), deps httpx/tenacity/defusedxml, the AWS changes, threshold 95 %/95 %.
- One 15-min run with a plan top-up instead of separate hourly/15-min runs (never two runs competing for 60 calls/min).
- Parity first: our code on HF raw August data had to reproduce HF silver before going live → two rule changes
  (fchg-only stops with planned time + category count; `final_destination` = last planned stop).
- Gap detection lives in the quality report, not silver (schema unchanged); a departure is covered when its
  station had a successful `fchg` within 3 h after the planned time; a plan-404 hour with known trains is a gap.
- Safe cutover: a hidden `candidate` board for 4 days, then `board_mode = "live"`.
- Owner (2026-11-14): standing OK for Phase 7 steps; ask only about cost.

**Tested**
- `make check`: 571 passed, coverage 97 %, ruff + mypy clean. `make web-check`: 70 passed. `make tf-scan`: 0 findings.
- Parity (HF raw through our code vs HF silver): 2026-08-24 13,795/13,795 HF rows, 2026-08-25 13,718/13,718;
  delay, is_late, destination 100 %; cancelled 99.86 % / 99.74 %.
- Real runs: local ingest 100 calls in 70.5 s; AWS ingest 65 s / 266–301 MB peak (top-up runs), 7 s (changes only).
  Nightly ETL (full days, AWS): 14–17 Nov 11,416–14,178 rows, 178–262 s, 349–402 MB peak, 0 flagged / empty stations.
- Reconciliation (live silver vs HF raw, `make reconcile`): 15/16/17 Nov **PASS** — HF rows found 100 % / 100 % / 100 %,
  is_late agreement 99.19 % / 95.51 % / 97.13 %, exact delay 94.57 % / 84.50 % / 90.27 %, destination 100 %.
  Where delays differ, live has the later one in 95 % of rows (16 Nov: 1,925 vs 103; HF reports 0 in 1,727): HF
  polls `fchg` about 5×/day and misses changes.
- `make smoke-prod-live` 10/10; headless Chrome: "Live · Updated 18:20", 0 CSP violations.
- Final review (opus): 1 Critical (DB keys in Terraform state) fixed; 4 Important fixed test-first (DST `ct`
  labelled on time, no ingest time budget, sample rows into a live board, plan-404 hours); minors 6/7/8/10/13/14 fixed.

**Known gaps / open items**
- Owner: delete 4 old versions of `prod/terraform.tfstate` that still hold the DB API keys, then rotate the keys and
  run `make put-db-api-secrets` (blocked for Claude by the auto-mode classifier).
- HF history likely **undercounts lateness** (missed changes read as on time); consider in Phase 9 (training on live data).
- ETL peak memory rising (349 → 402 MB of 512): watch; reduce if it passes ~450 MB.
- Deferred minors: rollback to a pre-Phase-7 API image breaks a live board (`extra="forbid"` on `via`) — switch
  `board_mode` to sample first; two ingest runs at once (`make ingest-local` / manual invoke during a scheduled run)
  can exceed 60 calls/min; an empty-but-valid `fchg` reply counts as coverage.
- Residual cancelled mismatch vs HF processing (0.1–0.3 %) unexplained.

**Docs touched**
- `docs/architecture.md` (§3.2, §7, §8, §9, §11, §16), `docs/prd.md` §6.3, `docs/phases.md`, README, CLAUDE.md,
  `Phases/phase-7-live-pipeline.md`, `Phases/README.md`, spec + plan under `docs/superpowers/`.

## Phase 6 follow-up — "Going to" filter (`phase-6/destination-filter`)

**Built**
- Board dropdown "Going to (final stop of the train)": lists the board's final stops A–Z, shows only trains to the chosen
  one; resets on station change; kept across 1/3/6 h, with "Show all trains" when none match. Frontend only.

**Key decisions**
- Owner chose this over a true A→B prediction, which would need a new label and retraining (2026-11-02). Routing stays out of scope.
- Dropdown, not typed search: only places with trains, works on phones and screen readers. No API, model or dependency change.

**Tested**
- `make web-check`: 66 passed (19 new), lint and types clean. Live: dropdown lists real final stops, 0 CSP violations, `make smoke-prod` 10/10.
- Independent review: 0 Critical; fixed test-first: focus lost after "Show all trains", results not announced to screen readers, blank
  destinations in real data (81 of 409,119 rows in 2026-08), untested paths. The dropdown stays mounted; a filtered empty result also offers 6 hours.

**Known gaps / open items**
- Final stop only: a train passing through the chosen station isn't listed. Matching all stops needs the stop list from Phase 7.
- Deferred minors: `DepartureRow`/`DepartureDetail` show an empty destination for a blank name; no `color-scheme` for native controls in dark mode;
  input borders below 3:1 (site-wide token); detail sheet focus if a live refetch changes the open train's destination (re-check in Phase 7);
  `design.md` has no entry for the filter; `onClearDestination` is optional. Picking an option was not exercised in a real browser.

**Docs touched**
- `docs/prd.md` (non-goal note), `CHANGELOG.md`, `Phases/phase-6-cloud-infra.md`, `CLAUDE.md`.

## Phase 6 — Cloud infrastructure (`phase-6/cloud-infra`)

**Built**
- Live on AWS `eu-north-1`: https://d37lj2a19ugjqm.cloudfront.net (React site + FastAPI on Lambda, champion v1, sample board).
- `infra/`: `bootstrap` (state bucket) and `envs/prod` with modules `data_lake`, `ecr`, `ssm`, `budget` (imported), `observability`
  (SNS + 3 alarms), `lambda_image`, `http_api`, `schedules`, `web_cdn` (CloudFront + OAC, SPA rewrite function, security headers,
  60 s board cache).
- `dbdelay.registry.pointer`: `SsmPointer`, `make_pointer`. `dbdelay.registry.publish`: `make publish-model` (local champion → S3 →
  SSM), `make rollback-prod`. `dbdelay.aws_session`: one-line login hint and refusals for the prod CLIs.
- `dbdelay.serving.samples` + `services/api/seed_handler.py`: 9 real silver days replayed by a Lambda every 15 min.
- `services/api/app/errors.py`: behind Lambda a POST without `Content-Length` is measured instead of answered with 411.
- Laptop deploy targets: `tf-*`, `image-push`, `upload-sample-days`, `web-deploy` (no-cache `index.html`, immutable assets),
  `smoke-prod`, `aws-check`, `aws-py-profile`, `tf-scan` (Trivy from Docker).

**Key decisions** (evidence in [ADR 0002](docs/adr/0002-aws-account-type.md))
- The account type fixes the region to `eu-north-1` and denies GitHub OIDC, so `rules.md` was changed (owner, 2026-10-31) and
  deploys run from the laptop; long-lived keys stay forbidden; the Phase 8 route is the owner's decision.
- Lambdas at 512 MB (account quota), no reserved concurrency (limit 5). Sample board every 15 min, because a board is stale after 20.
- `publish-model` takes no version argument: only the gate's choice can reach prod. ECR keeps 3 images (owner OK, cost).
- Budget imported, not re-created. Saved plans are applied exactly as reviewed. No new Python or npm dependencies.
- Trivy findings accepted with reasons: customer-managed KMS, access logs, WAF, default-certificate TLS policy, X-Ray, SNS encryption.

**Tested**
- `make check`: 452 passed, coverage 98 %, lint and strict types clean. `make web-check`: 47 passed. `make test-integration`: 8 passed.
  `uv run pre-commit run --all-files`: clean.
- `make tf-fmt tf-validate`: valid. `make tf-scan`: exit 0. `make tf-plan` after all applies: "No changes".
- `make smoke-prod` through CloudFront: 10/10. Headless Chromium: 0 CSP violations after the `data:` font fix; board and `/api/docs` render.
- Sample board on Sunday 2026-11-01: 0 errors, about 24 runs per 6 h. `make rollback-prod` with only v1: refused, SSM unchanged.
- Measured at 512 MB: CloudFront cache hit 0.07–0.22 s, miss 3.12 s; warm board at origin 1.74–2.08 s; cold board 6.8–6.9 s; warm health
  0.16 s; normal init 2.1–2.3 s; first-ever start 17.6 s; peak memory 303 MB (API), 366 MB (sample board); image 386.8 MB compressed.
- Final whole-branch review (independent, opus): 0 Critical. Sunday-failure suspicion refuted with live metrics. Fixed test-first:
  credential errors as tracebacks, half-failed rollback reporting success. Re-graded and fixed: ECR token could go to Docker Hub on an
  empty Terraform output; blank-page risk from uncached-header `index.html`.

**Known gaps / open items**
- ⚠️ **PRD latency target missed at the origin:** warm `/departures` p95 target < 300 ms vs. 1.74–2.08 s measured; cold start target < 6 s
  vs. 6.8–6.9 s. CloudFront cache hits meet it. Owner decision open (accept for this account, or optimise scoring).
- The SNS alarm subscription was still `PendingConfirmation` on 2026-11-02 (owner must click the email link).
- Free plan ends 2027-04-25. Real AWS cost: `TBD` (Cost Explorer after a week). Sample data only until Phase 7.
- Deferred review minors: clean-tree guard ignores untracked files and `web-deploy` has none; viewer `X-Request-ID` cached 60 s on boards;
  Lambda async retries still apply to the schedule; API throttle (10 rps) above uncached capacity (about 2.8 rps) and the execute-api URL
  bypasses the cache; `smoke_prod` hard-codes model version `1`; publish CLI imports MLflow (extra log line, about 4 s); make ≥ 4.4 needed
  for target variables in `$(shell)`; `enable_alarms` is redundant.

**Docs touched**
- `docs/adr/0002-aws-account-type.md` (new), `docs/rules.md` (region, OIDC note, ECR images), `docs/architecture.md`, `docs/phases.md`,
  `docs/prd.md` (constraints), `README.md`, `.env.example`, `CLAUDE.md`, `Phases/phase-6-cloud-infra.md`, `Phases/README.md`.

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
