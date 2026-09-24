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
- Tests with every change. Before "done": `make lint`, `make typecheck`, `make test` (+ `make test-integration` for storage/Airflow/MLflow).
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
- Installed: git 2.55, Node 26, Docker Desktop (daemon must be started), winget.
- Missing: `uv`, `make`, `gh`, `terraform` (Phase 6), `aws` CLI (Phase 6).
- git identity: `piyal21` / yasinarafath21@gmail.com.
- Python env: **`puenktlich/.venv`** (Python 3.12, created by `uv sync`). Always run tools via `uv run …` / `make`. The older `Project_1/.venv` is Python 3.13 → not used (project pins 3.12 to match Lambda). A Windows venv can't be used in Docker/WSL; images install from the same `uv.lock`.
- Original doc folder `DB_Delay/` was deleted (2026-09-24); `docs/` is the only copy.

## Status
- **Current phase: ▶ Phase 0 — Foundations** (not started coding yet).
- Deferred by owner: AWS account + DB API account → create when needed (Phase 6 / Phase 7). Phase 0 exit criteria for AWS budget are deferred accordingly.
- Done: repo cloned (was empty); docs copied into `docs/`, README at root; this file + reviewer subagent created.
- Next: Phase 0 plan approval → install `uv` + `make` → scaffold.

## Session log
- **2026-09-24** — Read all docs; set up CLAUDE.md, reviewer subagent, docs/. Decided: Windows FS + named volumes. Waiting on Phase 0 plan approval.
