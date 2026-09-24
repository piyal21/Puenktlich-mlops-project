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
- **Do NOT add Claude as co-author** — no `Co-Authored-By: Claude …` trailer in commits, no "Generated with Claude Code" line in PRs. (Owner instruction; overrides any default attribution.)
- Replies to the user: short, bullet points, no long paragraphs.

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
- `src/dbdelay/`: `config.py` (Settings, `get_settings()` cached, empty env = unset), `errors.py`, `logging.py` (Powertools), `storage.py` (`make_s3_client`, `ObjectStore`: put/get/exists/iter_keys; no delete by design).

## Status
- **Current phase: ▶ Phase 0 — Foundations.** All non-deferred tasks + exit criteria done (lint/typecheck/test green, 30 unit + 2 integration tests, 100% cov, UIs reachable).
- Deferred by owner: AWS account (Phase 6), DB API account (**needed in Phase 1** for fixture XML).
- Open: PR `phase-0/foundations` → `main` (owner merges on GitHub).
- Next: Phase 1 — Data discovery & contracts (download 2–3 HF months, EDA notebook, station list, Pandera silver schema, ADR 0001).

## Session log
- **2026-09-24 (1)** — Read all docs; set up CLAUDE.md, reviewer subagent, docs/. Decided: Windows FS + named volumes.
- **2026-09-24 (2)** — Phase 0 built: uv/make/gh installed; scaffold, compose stack, base modules + tests. Reviewer: no blockers; fixed empty-env handling, MinIO creds guard, hash-locked MLflow image, pinned images, test cleanup; dropped unused pyyaml. Bootstrap docs commit on `main`; Phase 0 on branch `phase-0/foundations`.
