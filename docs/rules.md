# Rules — Pünktlich

> **Rules for everyone writing code in this repo — especially AI coding assistants (Claude Code).**
> Read this file before every task. When a rule conflicts with a request, stop and ask.
> Priority order when documents disagree: `rules.md` > `architecture.md` > `prd.md` > `phases.md` > `design.md`.

---

## 0. How to work (AI assistant workflow)

1. **Read before writing.** Start each task by reading `docs/phases.md` (which phase are we in?) and the sections of `docs/architecture.md` the task touches.
2. **Stay inside the current phase.** Do not build features from later phases "while you're at it".
3. **Plan first for anything > ~50 lines.** Post a short plan (files to create/change, tests to add) and wait for approval.
4. **Small, reviewable steps.** One concern per change. Prefer several small commits over one large one.
5. **Tests with every change.** New logic ships with unit tests in the same change.
6. **Run the checks** before saying "done": `make lint`, `make typecheck`, `make test` (and `make test-integration` if you touched storage, Airflow or MLflow code).
7. **Update docs in the same change** when you change architecture, contracts, configs, commands or folder layout.
8. **Say what you did not do.** Summaries must list skipped items, assumptions and open questions — never claim something works if it wasn't run.

---

## 1. Hard boundaries (never do without explicit human approval)

| ❌ Never without asking | Why |
|---|---|
| `terraform apply` / `terraform destroy` / any AWS write via CLI or console | Real money, real infrastructure |
| Create AWS resources not listed in `architecture.md` §11/§16 | Cost & security |
| Create NAT Gateway, ALB/NLB, EC2, RDS, MWAA, SageMaker, Elastic IP, VPC-attached Lambda | Breaks free-tier budget |
| Change IAM policies to use `*` actions or resources | Least privilege |
| Change the label definition (`is_late = delay >= 6 min`, cancelled excluded) | Changes the whole ML problem |
| Change the silver schema, API contract, or `manifest.json` contract | Breaks consumers — needs version bump + doc update |
| Add a new dependency (Python or npm) | Supply chain & image size — justify it first |
| Delete or overwrite data in S3/MinIO outside of partition-overwrite logic | Data loss |
| Promote a model or move the SSM pointer manually | Must go through the gate |
| Disable, skip or weaken a test, lint rule, type check or security scan to get green | Hides problems |
| Commit secrets, `.env`, credentials, API keys, AWS account ids in code | Security |
| Force-push, rewrite history on `main`, or push directly to `main` | Traceability |
| Invent metrics, benchmark numbers or screenshots in README/docs | Honesty — use `TBD` until measured |

**Always allowed:** reading files, running local tests/linters, running the local Docker stack, creating branches, writing code and tests within the current phase.

---

## 2. Tech stack (use these — nothing else without approval)

| Purpose | Use | Do **not** use |
|---|---|---|
| Python env/deps | `uv`, `pyproject.toml`, `uv.lock` | pip + requirements.txt, poetry, conda |
| Dataframes | pandas 2 + PyArrow; DuckDB for large scans | Spark, Dask, Polars (keep one stack) |
| Validation | Pandera (dataframes), Pydantic v2 (API, config) | Great Expectations (too heavy here) |
| Config | `pydantic-settings` + YAML in `configs/` | Hardcoded constants, `os.environ[...]` scattered in code |
| ML | LightGBM, scikit-learn (metrics, isotonic) | XGBoost/CatBoost in v1, AutoML |
| DL (stretch only) | PyTorch → ONNX, served with onnxruntime | TensorFlow, serving PyTorch directly in Lambda |
| Tracking/registry | MLflow 3 | W&B, Neptune |
| Orchestration | Airflow 3 TaskFlow API (`from airflow.sdk import dag, task`) | Airflow 2 imports (`airflow.decorators`, `airflow.models.DAG`), Prefect, Dagster |
| Drift | Evidently ≥ 0.7 API (`from evidently import Report`) | Legacy `evidently.report` / `evidently.metric_preset` imports |
| API | FastAPI + Mangum | Flask, Django |
| HTTP | httpx (with timeouts) + tenacity | requests without timeouts |
| XML | `defusedxml.ElementTree` | `xml.etree`, `lxml` on untrusted input |
| AWS SDK | boto3 | — |
| Logging | AWS Lambda Powertools `Logger` (JSON) | `print()`, f-string log messages with secrets |
| Tests | pytest, pytest-cov, moto, Vitest, Testing Library | unittest-style classes for new tests |
| Frontend | React 19 + Vite + TS (strict), Tailwind v4, TanStack Query, React Router, Recharts, lucide-react, self-hosted fonts via `@fontsource` | Next.js, Redux, CSS-in-JS libraries, UI kits (MUI/Chakra) |
| IaC | Terraform ≥ 1.10, AWS provider ~> 6.0 | CDK, CloudFormation, Pulumi, click-ops |
| CI/CD | GitHub Actions + OIDC | Long-lived AWS keys in GitHub secrets |

---

## 3. Python coding standards

- Python **3.12**. Type hints on **every** function signature. `mypy --strict` for `src/dbdelay/`, normal mode elsewhere.
- Formatting and linting: `ruff format` + `ruff check` with rule sets `E,F,I,B,UP,S,SIM,RUF,PL` (line length 100).
- Package code lives in `src/dbdelay/`. Services, DAGs and handlers are **thin**: they parse input, call `dbdelay` functions, and return. No business logic in DAG files, Lambda handlers or FastAPI routers.
- Pure functions for transformations (DataFrame in → DataFrame out). I/O at the edges.
- No global mutable state except explicit caches (model cache, pointer cache) with TTLs.
- Naming: `snake_case` for functions/files, `PascalCase` for classes, constants `UPPER_SNAKE`. Name things after the domain (`departure`, `ride`, `eva`), not generic (`data`, `df2`, `tmp`).
- Docstrings (Google style) on public functions: what, args, returns, raises. Comments explain *why*, not *what*.
- Time: **store and compute in UTC**; convert to `Europe/Berlin` only for features (`hour_local`, `weekday`) and display. Always timezone-aware datetimes (`zoneinfo.ZoneInfo`). Naive datetimes are a bug.
- Randomness: every random process takes a `seed` from config. Default seed `42`.
- Notebooks are for exploration only. Anything reused moves to `src/` with tests.

## 4. TypeScript / frontend standards

- `strict: true`, no `any` (use `unknown` + narrowing). ESLint + Prettier.
- API types in `frontend/src/api/types.ts` must mirror the Pydantic response models (keep in sync; later: generate from OpenAPI).
- Data fetching only through TanStack Query hooks in `frontend/src/hooks/`. Components don't call `fetch` directly.
- Every async view has **loading, empty, error and stale** states (see `design.md`).
- Styling via Tailwind + design tokens from `design.md`. No hard-coded hex colors in components.
- Accessibility: semantic HTML, labels on inputs, focus visible, keyboard navigable, risk shown with text + icon (never color only).

## 5. Data & ML rules

### 5.1 Leakage policy (critical)
- Features may only use information **known at prediction time**. v1 = timetable/calendar features only.
- **Never** use `changed_departure_utc`, `delay_min`, `is_cancelled`, or anything derived from the actual outcome as a feature.
- Any future "live context" feature must be computed **as of `planned_departure − 60 min`** and must pass the feature parity test before use.
- Category levels, rare-level grouping and any statistics are computed on the **train split only**.

### 5.2 Splits & evaluation
- Time-based splits only (train < valid < test). Never shuffle across time. Never tune on test.
- Always compare against the **baseline** and the **current champion on the same test set**.
- Report Brier (primary), ROC-AUC, PR-AUC, log loss, ECE, and per-`train_type` slices.

### 5.3 Data quality
- Validate with Pandera at every layer boundary (bronze→silver, silver→gold, before training, before scoring).
- A failed validation **stops** the pipeline. Never "fix" bad data silently; quarantine it to `…/_quarantine/` with a reason and count.
- Log data-quality stats per run: rows in/out, dropped rows by reason, null rates, late rate.
- Writes are **idempotent**: overwrite whole `date=` partitions; never append duplicates.

### 5.4 Reproducibility
- Every training run logs to MLflow: git SHA, data `snapshot_id`, config file contents, params, metrics, artifacts, and the environment (`uv.lock` hash).
- Every registered model carries tags: `git_sha`, `snapshot_id`, `gate_result`.
- Model artifacts: LightGBM **text** format + JSON. **No pickle / joblib** for anything loaded in production.

### 5.5 Models
- v1 = LightGBM binary classifier + isotonic calibration. Keep it simple; tune a small grid only.
- A DL model (stretch) only replaces LightGBM if it passes the **same gate**.

## 6. Error handling

### 6.1 Principles
- **Fail loudly in pipelines, degrade gracefully in serving.**
- Never use bare `except:` or `except Exception: pass`. Catch specific exceptions; if you catch broadly at a boundary, log with context and re-raise or convert.
- Every external call has a **timeout** (httpx default 10 s, boto3 connect 3 s / read 10 s).
- Retries only for transient errors (HTTP 429/5xx, connection errors, S3 throttling): tenacity, exponential backoff with jitter, max 3 attempts. Never retry 4xx validation errors.

### 6.2 Exception hierarchy (`src/dbdelay/errors.py`)
```python
class DbDelayError(Exception): ...                 # base
class ConfigError(DbDelayError): ...               # bad/missing config → fail at startup
class ExternalServiceError(DbDelayError): ...      # DB API, S3, SSM failures (transient or not)
class RateLimitedError(ExternalServiceError): ...  # 429 from DB API
class DataValidationError(DbDelayError): ...       # Pandera/contract failures
class ArtifactIntegrityError(DbDelayError): ...    # checksum mismatch, missing file
class ModelNotAvailableError(DbDelayError): ...    # no champion / load failed
class NotFoundError(DbDelayError): ...             # unknown station/event
```

### 6.3 Per component
| Component | On error |
|---|---|
| `ingest` | Per-station failures are logged and counted; the run succeeds if ≥ 50 % of stations succeed, otherwise raises (→ Lambda error → alarm). Emits `EventsIngested`. |
| `etl_daily` | Validation failure → raise, nothing written for that date. |
| `monitor` | If D-1 has < `min_labeled_events`, publish "insufficient data" status instead of metrics; never write a retrain flag on insufficient data. |
| Airflow tasks | Raise; Airflow retries (2, with delay) only for tasks marked idempotent. Gate rejection is a **successful** outcome, not a failure. |
| API | Map exceptions to `application/problem+json`: `NotFoundError`→404, validation→422, `ModelNotAvailableError`→503 (board still returns live data with `prediction: null`), `ExternalServiceError`→502/503. Unexpected → 500 with `request_id`, **no stack trace in the response**. |
| Frontend | Friendly message + retry button; show last good data with a "stale" badge when possible. |

### 6.4 Logging
- JSON logs via Powertools Logger with `service`, `request_id`/`run_id`, and relevant ids (`eva`, `model_version`, `snapshot_id`).
- Levels: `DEBUG` local only, `INFO` normal events, `WARNING` degraded but continuing, `ERROR` failed operation.
- **Never log** secrets, API keys, full request headers, or raw XML at INFO.

## 7. Security rules
- Secrets only in SSM (cloud) or `.env` (local, git-ignored). `.env.example` has placeholders only.
- IAM: one role per Lambda/CI job; resources scoped to bucket ARNs + prefixes and parameter ARNs.
- S3: Block Public Access on all buckets; TLS-only policies; web content only via CloudFront OAC.
- Validate all API inputs with Pydantic (`eva` = digits only, `hours` 1–6, body size ≤ 4 KB).
- Parse XML only with `defusedxml`. Never `eval`/`exec`, never `yaml.load` (use `yaml.safe_load`), never `pickle.load` on external data.
- Containers: pinned base image, non-root where possible, no build secrets in layers, `.dockerignore` excludes `.env`, `.git`, data, notebooks.
- Keep `gitleaks`, Trivy (image + config), `pip-audit`, `npm audit`, and Ruff `S` rules green.

## 8. Cost rules
- Region `eu-central-1` only. Every resource tagged (`project`, `env`, `managed_by`, `owner`).
- Log groups always have retention (14 days default). ECR lifecycle keeps last 10 images. Bronze data expires after 30 days.
- ≤ 10 custom CloudWatch metrics, ≤ 10 alarms.
- Before adding any AWS resource, state its expected monthly cost in the PR description.

## 9. Testing rules
- `tests/unit/`: fast (< 30 s total), no network, no Docker; AWS mocked with moto; DB API mocked with fixture XML.
- `tests/integration/` (`@pytest.mark.integration`): run against the local compose stack (MinIO, MLflow).
- Must-have tests: XML parser on fixtures, silver schema, DST handling, idempotent partition writes, feature builder (incl. unknown category → `OTHER`), leakage guard (no forbidden columns in features), gate logic, checksum verification, API routes (TestClient), problem+json errors.
- Coverage target ≥ 80 % for `src/dbdelay/`.
- Frontend: component tests for RiskBadge, DepartureRow, states (loading/empty/error/stale).

## 10. Git & PR conventions
- Branches: `phase-<n>/<short-topic>` (e.g. `phase-2/hf-backfill`).
- Commits: Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`, `ci:`, `infra:`).
- PR description: what/why, how tested, screenshots for UI, cost impact for infra, docs updated (yes/no).
- `main` is always deployable.

## 11. Definition of done (every task)
- [ ] Code follows these rules; lint, type-check and tests pass locally.
- [ ] New/changed behaviour has tests.
- [ ] Docs updated (architecture/config/README/runbook) if anything they describe changed.
- [ ] No secrets, no debug prints, no commented-out code.
- [ ] Summary lists what was done, what wasn't, and how to verify.
