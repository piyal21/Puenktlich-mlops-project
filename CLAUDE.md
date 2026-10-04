# CLAUDE.md — Pünktlich (session memory)

> Read this first. It replaces re-reading all of `docs/`. Open a doc only for the section a task touches.
> **Update the "Status" and "Session log" sections at the end of every session.**

## Project in 10 lines
- **Pünktlich?** — probability a DB train departs **≥ 6 min late**, ~30 stations, next 3 h. Portfolio MLOps project.
- Owner: MD Piyal Ahmmed (GitHub `piyal21`). Repo: https://github.com/piyal21/Puenktlich-mlops-project
- Data: HF `piebro/deutsche-bahn-data` (history, monthly parquet) + DB Timetables API (live, ≤ 50 calls/min).
- Label: `is_late = delay_min >= 6`; cancelled → `is_late = null` (excluded). **Never change without approval.**
- Features v1: timetable/calendar only (eva, train_type, line_key, destination_key, stop_index, hour_local, minute_of_day, weekday, is_weekend, month, is_public_holiday). One module: `src/dbdelay/features/build.py`.
- Model: LightGBM + isotonic calibration; time split (test = last 14 d, valid = 14 d before); baseline = late-rate lookup; primary metric Brier.
- Training plane: local Docker (Airflow 3, MLflow 3, MinIO, Postgres). Online plane: AWS serverless eu-central-1. Delivery: GitHub Actions (OIDC) + Terraform.
- Release = upload `models/<v>/` + SHA-256 manifest, move SSM pointer. Rollback = `make rollback`. No pickle.
- Monitor nightly on all D-1 departures; drift/perf bad 3 days in a row → retrain flag → gate.

## Where to look (docs/)
| Need | File § |
|---|---|
| Silver schema | `architecture.md` §3.3 |
| Storage layout / buckets | `architecture.md` §3.2 |
| Features | `architecture.md` §4 |
| Training DAG, gate config | `architecture.md` §5 |
| Manifest / release | `architecture.md` §6 |
| API contract | `architecture.md` §7, `prd.md` §6.3 |
| Ingestion (DB API) | `architecture.md` §8 |
| Monitoring, alarms | `architecture.md` §9 |
| Terraform layout | `architecture.md` §11 |
| Repo layout | `architecture.md` §14 |
| Tech stack allow/deny | `rules.md` §2 |
| UI tokens/components | `design.md` |
| Phase tasks + exit criteria | `phases.md` |

Doc priority on conflict: `rules.md` > `architecture.md` > `prd.md` > `phases.md` > `design.md`.

## Working rules (condensed from rules.md)
- Stay in the **current phase**. Plan first for anything > ~50 lines; wait for approval.
- Tests with every change. Before "done": `make lint`, `make typecheck`, `make test` (+ `make test-integration` for storage/Airflow/MLflow). `uv run pre-commit run --all-files` before committing.
- **After each code change, run the `code-reviewer` subagent** (`.claude/agents/code-reviewer.md`) and fix confirmed findings.
- Never without asking: `terraform apply/destroy`, any AWS write, new dependency, schema/API/manifest contract change, label change, weakening tests/lint/scans, manual model promotion, push to `main`/force-push, invented numbers (use `TBD`).
- Python 3.12, type hints everywhere, `mypy --strict` on `src/dbdelay/`, ruff `E,F,I,B,UP,S,SIM,RUF,PL`, line length 100. Thin handlers/DAGs/routers; logic in `src/dbdelay/`.
- UTC everywhere; Europe/Berlin only for features + display. Seeds from config (default 42).
- Git: branches `phase-<n>/<topic>`, Conventional Commits, PRs into `main`.
- **Branch record (from Phase 2 on, owner rule 2026-09-25):** before the last commit on a phase branch, (1) add a `CHANGELOG.md` entry for the phase — *Built · Key decisions · Tested (commands + results) · Known gaps / open items · Docs touched* — and commit it on that branch; (2) hand the owner a ready merge message with the same summary (file outside the repo, e.g. `.git/MERGE_SUMMARY.txt`, used as `git merge --no-ff <branch> -F .git/MERGE_SUMMARY.txt`). If the owner merges via a GitHub PR instead, write the PR description in the same format. Only measured numbers (else `TBD`).
- **Phase doc (every phase, automatically, owner rule 2026-09-26):** at the end of each phase write `Phases/phase-<n>-<topic>.md` — a standalone explainer anyone can follow: goal, what was built (files + why), how it works (diagram if useful), key decisions + reasons, how it was tested (commands + real results), how to run/reproduce it, known gaps, what's next. Plain language, no chat context assumed. Commit it on the phase branch together with the CHANGELOG entry. `Phases/README.md` is the index (one line per phase).
- **Never delete branches** (local or remote), even after merge. Owner wants every phase branch kept.
- **Do NOT add Claude as co-author** — no `Co-Authored-By: Claude …` trailer in commits, no "Generated with Claude Code" line in PRs. (Owner instruction; overrides any default attribution.)
- Replies to the user: short, bullet points, no long paragraphs.

## Skills to use (owner: "use all skills necessary")
**Every task**
- New feature/behaviour → `superpowers:brainstorming` → `superpowers:writing-plans` (plan > ~50 lines, wait for approval).
- Implement → `superpowers:test-driven-development`. Bug/failing test → `superpowers:systematic-debugging`.
- Before "done" → `superpowers:verification-before-completion` → `code-reviewer` subagent → `superpowers:receiving-code-review` for its findings.
- Phase end → `superpowers:finishing-a-development-branch` (**but never delete branches**) + branch record (CHANGELOG entry + merge message) + phase doc in `Phases/` (see Working rules).

**Per phase**
| Phase | Skills |
|---|---|
| 1 Data discovery | `data:explore-data`, `data:statistical-analysis`, `dataviz`, `engineering:architecture` (ADR 0001) |
| 2 Historical ETL | `data:validate-data`, `data:sql-queries` (DuckDB), `engineering:testing-strategy` |
| 3–4 Features/training | `data:statistical-analysis` (calibration, metrics), `engineering:testing-strategy` |
| 5 Serving & UI | `design:ux-copy`, `design:accessibility-review`, `design:design-critique`, `run` |
| 6 Terraform | `engineering:system-design`, `engineering:architecture`, `security-review` |
| 7 Live pipeline | `engineering:debug`, `data:validate-data` |
| 8 CI/CD | `engineering:deploy-checklist`, `security-review` |
| 9 Monitoring | `dataviz` (health page), `engineering:incident-response` (runbook), `engineering:documentation` |
| 10 Hardening | `security-review`, `code-review`, `design:accessibility-review`, `engineering:documentation` |

## Local environment (checked 2026-09-24)
- Windows 11, Git Bash + PowerShell. 32 GB RAM, no GPU.
- **Repo lives on the Windows filesystem**, not WSL (WSL2 has no distro installed). Heavy data (MinIO, Postgres) uses Docker **named volumes** to avoid slow bind mounts.
- Installed: git 2.55, Node 26, Docker Desktop 29.8 (`%LOCALAPPDATA%\Programs\DockerDesktop`, must be started), winget, **uv 0.12.18, GNU make 4.4.1 (ezwinports), gh 2.101** (gh not logged in yet).
- New winget tools are on PATH only in **new** terminals. In Claude's Bash, prefix:
  `export PATH="$PATH:/c/Users/yasin/AppData/Local/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:/c/Users/yasin/AppData/Local/Microsoft/WinGet/Packages/ezwinports.make_Microsoft.Winget.Source_8wekyb3d8bbwe/bin:/c/Program Files/GitHub CLI"`
- Missing: `terraform`, `aws` CLI (Phase 6).
- git identity: `piyal21` / yasinarafath21@gmail.com.
- Python env: **`puenktlich/.venv`** (Python 3.12, created by `uv sync`). Always run tools via `uv run …` / `make`. The older `Project_1/.venv` is Python 3.13 → not used (project pins 3.12 to match Lambda). A Windows venv can't be used in Docker/WSL; images install their own locked deps.
- `.env` exists locally with random MinIO/Postgres passwords (never print it).
- Original doc folder `DB_Delay/` was deleted (2026-09-24); `docs/` is the only copy.
- Reviewer subagent only registers when the session starts in `puenktlich/`. Otherwise run a `general-purpose` agent told to follow `.claude/agents/code-reviewer.md`.

## Local stack (Phase 0)
- `make up` → MinIO (pgsty/minio fork, official images gone) :9000 / console :9001, Postgres 17.11 :5432, MLflow 3.16.1 :5000. All on 127.0.0.1, named volumes, one bucket `puenktlich-local` (MLflow artifacts under `mlflow/`).
- MLflow image: `docker/mlflow/Dockerfile`, deps locked with hashes in `docker/mlflow/requirements.txt` (regen command in its header).
- `src/dbdelay/`: `config.py` (Settings, `get_settings()` cached, empty env = unset), `errors.py`, `logging.py` (Powertools), `storage.py` (`make_s3_client`, `ObjectStore`: put/get/upload_file/download_file/exists/iter_keys; no delete by design). Phase 2: `data/months.py`, `data/stations.py`, `data/silver.py`, `data/quality.py`, `data/hf_backfill.py`.

## Status
- **Current phase: ✅ Phase 4 — Training, tracking, registry, gate done**, merged into `main` by Claude on owner instruction (2026-10-04, `git merge --no-ff`; push of `main` = owner's call). Spec `docs/superpowers/specs/2026-10-04-phase-4-training-design.md`, plan `docs/superpowers/plans/2026-10-04-phase-4-training.md`, doc `Phases/phase-4-training.md`.
- Phase 4 results: champion **v1** (`models/1/`, `models/_pointer.json`, MLflow `puenktlich-delay@champion`): test Brier 0.1408 vs baseline 0.1520 (gate limit 0.1444), ROC-AUC 0.8078. Identical retrains (make train v2, DAG v3/v4) rejected as ties. `make train` ≈ 20 min. New deps: `lightgbm`, `mlflow-skinny` (extra `training` + dev).
- Phase 4 env lessons: MLflow 3.16 server needs `MLFLOW_SERVER_ALLOWED_HOSTS` incl. `mlflow:5000` (else 403 from Airflow); its presigned downloads point at `minio:9000` → `MlflowTracker` forces proxied transfers and suppresses the emoji stdout print (cp1252 crash). Airflow TaskFlow args must not use context names (`run_id`). Laptop enters Modern Standby ~1 min after idle → DAG tasks die; keep it awake (temporary `SetThreadExecutionState` helper worked, on AC power). First `make train-dag` unpause also starts the latest `@monthly` run.
- Phase 4 open: MLflow client telemetry on (owner to decide `MLFLOW_DISABLE_TELEMETRY`); 9 deferred review minors in CHANGELOG.md.
- Phase 3 merged by owner (`584f263`, pushed). Phase 2 merged (`b8fac56`). Phase 3: `make baseline` → gold snapshot `2026-08-31_38b45c7a` (train 3,197,166 / valid 178,256 / test 176,628 rows; excluded 170,228 cancelled + 494 gap-hour + 1,236 gap-station-day rows); baseline test Brier 0.1520, ROC-AUC 0.7714, PR-AUC 0.5272, ECE 0.0124. New deps: `holidays` (main), `scikit-learn` (extra `training` + dev). Features only via `dbdelay.features.build.build_features(df, spec)`.
- Phase 2 data in MinIO: bronze 9 months (2025-12 → 2026-08, ~650 MB each), silver 274 day files, 3,724,008 rows; quarantine + quality JSON per month (`silver/_quarantine/…`, `silver/_quality/…`).
- Airflow: `make airflow-env` (fills missing/`change-me` secrets) → `make airflow-up` → UI :8080 → `make backfill`. Image installs under Airflow constraints-3.3.2 minus `pandas==` (project pins pandas<3); `huggingface-hub<2`. New DAG files need `docker compose restart airflow-dag-processor` (or wait for bundle refresh).
- ⚠️ **PC sleep kills running tasks:** containers freeze, task JWT (10 min) expires → 403 on heartbeat → task failed. Keep the PC awake during backfills; recover with a clear of failed TIs or a re-trigger (ingest skips existing months, silver is idempotent).
- Phase 2 deferred minors (review 2026-09-28): httpx errors not wrapped in `hf_download`; Berlin alias dup tie-break by id string; report lacks pre-conform counts (`not_supported_station` always 0); quarantine parquet has no fixed schema + can double-count overlap rows; `plan_months` retries bad input; `build_silver` retries=0 also for transient MinIO errors; `force_download` of M doesn't rebuild M±1.
- Phase 1 ✅ done and merged.
- Phase 0 merged to `main` (PR #1, merge commit `e87e440`).
- Phase 1 merged to `main` by owner via CLI (`git merge --no-ff`, merge commit `867636a`, pushed; no PR). Branches `phase-0/foundations`, `phase-1/data-discovery` kept (local + remote).
- Deferred by owner: AWS account (Phase 6).
- DB API: ✅ working (HTTP 200; all 30 station EVAs return live plans, 2026-09-25). Keys in `.env`; scripts load them via `get_settings()` — never `source .env` in bash (it mis-parses and echoes values).
- ✅ DB API keys rotated by owner (2026-09-29); new keys verified (HTTP 200, values never printed).
- Phase 1 decisions **confirmed by owner 2026-09-26** (ADR 0001 → Accepted): label, leakage policy, silver contract (architecture §3.3), station list incl. Berlin `hf_aliases`, stdlib urllib in the dev fetch script.
- **Phase 7 must-do (risk #1):** "no change reported ⇒ on time" hides late trains if live ingestion misses fchg windows → record per stop whether it was seen in fchg, flag ingestion-gap days in monitoring, reconcile live vs HF on overlapping days.
- Phase 7/9: alert when a station's live board is empty (EVA drift like Berlin) → fix via `hf_aliases`.
- Phase 1 data: HF months 2025-10, 2026-03, 2026-08 in `data/raw/hf/` (git-ignored, ~1.3 GB; download command at top of `notebooks/01_eda.ipynb`). Key facts in the notebook summary.
- Carry into Phase 5+: `month` feature unseen for Sep–Nov; `min_count` thresholds untuned; snapshot id hashes parquet bytes incl. library versions (the Airflow image happened to produce the same id). Serving must load models only via `dbdelay.registry.artifacts.load_bundle` (checksums) and features via `build_features`.
- **Do NOT start Phase 5 until the owner explicitly says so.**

## Session log
- **2026-09-24 (1)** — Read all docs; set up CLAUDE.md, reviewer subagent, docs/. Decided: Windows FS + named volumes.
- **2026-09-24 (2)** — Phase 0 built: uv/make/gh installed; scaffold, compose stack, base modules + tests. Reviewer: no blockers; fixed empty-env handling, MinIO creds guard, hash-locked MLflow image, pinned images, test cleanup; dropped unused pyyaml. Bootstrap docs commit on `main`; Phase 0 on branch `phase-0/foundations`.
- **2026-09-24 (3)** — PR #1 merged by owner; local `main` synced. DB API keys added (first 403: plan not linked; fixed same day → 200). Decided: CLAUDE.md is the single session-memory file (no separate memory.md). Waiting for owner's go for Phase 1.
- **2026-09-25 (1)** — Phase 1 built on `phase-1/data-discovery`: deps added (pandas<3, pyarrow, duckdb, pandera, pyyaml; dev hf_hub, jupyter, matplotlib, pandas-stubs<3, types-PyYAML). EDA notebook (3 HF months), Kiel fixtures, 30 stations (all 16 states), Pandera `SilverDepartures`, ADR 0001, architecture §3.3 refinements. Found: scope 131→5.3k stations after 2025-10, HF `train_line_ride_id` not a ride key, change time always filled (no update ⇒ delay 0), Berlin Hbf EVA merge (8011160→8098160). Reviewer: 6 should-fix, all fixed (`\Z` anchors, delay/time check, Berlin alias, leak test, event_id format, script hardening); re-review PASS.
- **2026-09-25 (2)** — 5 conventional commits on `phase-1/data-discovery` (no conflicts with `main`), pushed. Owner merged into `main` via CLI (`867636a`). Decided: from Phase 2 on, every phase branch gets a `CHANGELOG.md` entry + a ready merge message (not retroactive for Phases 0–1). Waiting for owner's go for Phase 2.
- **2026-09-26** — Owner go for Phase 2; branch `phase-2/hf-backfill`. Added `Phases/` folder rule + Phase 0/1 docs; ADR 0001 Accepted. Brainstormed → spec (split Airflow services, DuckDB filter + pandas conform) approved.
- **2026-09-27** — Plan written; native execution. Tasks 1–10 built (TDD, 145 unit tests). Image deps conflicted → owner chose Airflow constraints. Real backfill triggered; PC sleep caused 403s.
- **2026-09-28** — Docker Desktop engine hung (500s) → owner restarted it. Final review (opus): 0 Critical, 2 Important fixed (placeholder Airflow secrets, streaming bronze), 8 Minor deferred. Backfill finished (9 months, 3,724,008 silver rows); second run on the fixed image for determinism. CHANGELOG + `Phases/phase-2-hf-backfill.md` written; branch pushed; merge message in `.git/MERGE_SUMMARY.txt`. Waiting for owner merge and go for Phase 3.
- **2026-09-29 → 10-03** — Owner merged Phase 2 (`b8fac56`) and rotated DB API keys. Phase 3: brainstorm → spec (owner decisions: deps holidays + scikit-learn, exclude data gaps from all splits) → plan → native execution (12 tasks, TDD). Real run: snapshot `2026-08-31_38b45c7a`, deterministic over 3 runs. Final review (opus): 0 Critical, 2 Important fixed, 7 Minor deferred. Lesson: run `ruff check --fix` + `ruff format` (incl. notebooks) before `git add`, else the pre-commit hook silently aborts the commit. Waiting for owner merge and go for Phase 4.
- **2026-10-03 → 10-04** — Owner go for Phase 4; brainstorm → spec (owner decisions above) → plan (12 tasks) → native execution (TDD). Real runs: v1 promoted, identical retrains rejected; DAG green on the rejection path. Fixed in real runs: MLflow presigned-URL hang, emoji print crash, MLflow 403 host guard, Airflow `run_id` arg clash. Final review (opus): 0 Critical, 1 Important fixed (config/env logging + version lineage tags), 9 Minor deferred. Owner asked to commit and merge into `main` if no conflicts → merged.
