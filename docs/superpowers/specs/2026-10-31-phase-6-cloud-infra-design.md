# Phase 6 — Cloud infrastructure (Terraform): design

Status: **approved by owner 2026-10-31** · Date: 2026-10-31 · Branch: `phase-6/cloud-infra`

## 1. Intent

Put the Phase 5 product on the public internet: a CloudFront URL that serves the React site and the
FastAPI app (champion v1) on AWS Lambda, with every AWS resource defined in Terraform. Until Phase 7
brings real departures, the site shows **clearly labelled sample departures** (a real past day replayed
onto "now", exactly like `make seed`), refreshed automatically so the board never looks broken.

Why: a live, clickable demo is the portfolio's front door, and Terraform-defined infrastructure is
one of the skills the project exists to show.

## 2. Goals, non-goals, anti-goals

**Goals**
- Public CloudFront URL serves the UI; `/api/v1/health` reports model version `1`.
- Board, search, `/predict`, `/model`, `/api/docs` work through CloudFront exactly as locally.
- Sample board refreshed on a schedule; the UI shows "Sample data" as it does today.
- Model release to AWS through code (`make publish-model`), pointer in SSM, rollback in one command.
- Everything in Terraform; `terraform plan` shows no changes after apply; `trivy config infra/` clean
  or findings documented.
- Docs match reality (region, account type, deploy path).

**Non-goals (later phases)**
- Live ingestion, `jobs` image, DB API secrets (Phase 7).
- GitHub Actions deploys / OIDC roles (Phase 8 — blocked on this account, see §3).
- Nightly monitoring, drift, health JSON in `public/` (Phase 9).
- Custom domain, WAF, multi-region.

**Anti-goals (failure even if it "works")**
- Anything that bills per hour while idle (NAT, ALB, EC2, RDS …) or pushes the month above ~$1.
- Long-lived AWS access keys anywhere (repo, GitHub, `.env`).
- AWS account id or other identifiers committed to the repo (`rules.md`).
- A public site that looks broken (empty or permanently "stale" board) or hides that the data is sample data.
- IAM `*` actions/resources; buckets readable from the internet.

## 3. Constraints (measured 2026-10-30, not assumptions)

The owner's AWS account was created with AWS's **new sign-up experience** ("projects", Free plan):

| Fact | Consequence |
|---|---|
| Region fixed to **`eu-north-1`** (Stockholm); AWS-managed SCP denies `eu-central-1` and regional calls in `us-east-1` | Everything regional goes to eu-north-1. No ACM cert in us-east-1 → no custom domain (fine: non-goal). Docs that say eu-central-1 change. |
| SCP explicitly denies `iam:CreateOpenIDConnectProvider` (and listing) | No GitHub OIDC. `infra/bootstrap` creates **only** the state bucket. Phase 8 route is an owner decision (laptop-only / Paid plan + advanced features / new standard account). |
| No root user, no IAM Identity Center; humans sign in with AWS Builder ID | CLI auth = `aws login --profile puenktlich` (12 h session, role `AccountFullAccessRole`). Terraform AWS provider reads this profile (verified with a read-only plan). Security doc §12 is rewritten accordingly. |
| Read access verified in eu-north-1 for S3, ECR (+ login token), Lambda, API Gateway v2, CloudFront (+ OAC, response-headers policies), SSM, SNS, Scheduler, Logs, CloudWatch, Budgets, IAM | Create permissions are only known after the first apply → apply in stages (§9). |
| Lambda **concurrent executions limit = 5** | No reserved concurrency (needs headroom the account lacks). API Gateway throttling + CloudFront caching protect it. |
| Free plan: $100 credits, ends **2027-04-25**; access stops when credits or the period run out unless upgraded | Documented in README/architecture as a known limitation. |
| Budget `puenktlich-monthly` already exists (manual): $1/month, charge types Credit + Refund excluded, alerts 100 % actual + 500 % forecasted to the owner's email | Imported into Terraform, not re-created. |

Project rules that bind this phase: `terraform apply`/`destroy` and every AWS write need the owner's
explicit OK per apply; no resources outside `architecture.md` §11/§16 without saying so; one IAM role
per Lambda scoped to exact ARNs/prefixes; no account ids in code; state expected monthly cost.

## 4. Decisions

**Owner's decisions (2026-10-30/31)**
1. Region `eu-north-1` (account cannot change it).
2. Apply from the laptop with `aws login`; GitHub→AWS deploys wait for the Phase 8 decision.
3. Until Phase 7 the public board shows clearly labelled sample departures, refreshed automatically.
4. Budget created by hand now; Terraform takes it over.

**Claude's calls (owner may override)**
- Free `*.cloudfront.net` domain; no custom domain.
- SNS alarm email = the budget email (passed as a variable at apply time, never committed).
- Sample refresh every **15 min**, not 30: the API marks a board stale after 20 min, so a 30-min refresh
  would show "stale" a third of the time. Cost is still $0 (≈ 3k invocations/month).
- The sample-refresh Lambda reuses the **API image** with a different handler (it already contains
  pandas/pyarrow/duckdb and the seed code) — no second image before Phase 7.
- `make publish-model` publishes **only the current local champion** (the version the gate released and
  the local pointer names) — no free version argument, so it can never become a manual promotion
  (`rules.md`: no manual pointer moves). Same version already live in prod → no-op. Going back in prod
  is `make rollback-prod`.
- Two SSM `String` parameters as in architecture §6 (`/puenktlich/prod/model/champion_version`,
  `/…/previous_version`); "no value yet" is the literal `none` (SSM cannot store an empty string).

## 5. Target architecture

```text
browser ──HTTPS──► CloudFront (default cert, PriceClass_100, security-headers policy)
                    ├─ default behaviour ──OAC──► S3 web bucket (SPA build)
                    │     CloudFront Function: non-file paths → /index.html (SPA routes)
                    └─ /api/*  ──────────────► API Gateway HTTP API ($default stage, throttle 10 rps / burst 20)
                                                  └─► Lambda `api` (container image from ECR)
                                                        ├─ SSM: champion_version / previous_version (TTL 5 min)
                                                        ├─ S3 models bucket: models/<v>/… (verified checksums)
                                                        └─ S3 data bucket: live/boards/latest.json.gz (TTL 60 s)

EventBridge Scheduler (rate 15 min) ─► Lambda `sample_board` (same image, handler seed_handler.handler)
                                          reads  S3 data: samples/silver/departures/source=hf/date=…/part-0.parquet
                                          writes S3 data: live/boards/latest.json.gz  (source="sample")

Alarms (CloudWatch → SNS email): API 5xx, API Lambda throttles, sample_board errors.  Budget: imported.
Laptop: make publish-model / make rollback-prod / make image-push / make web-deploy / terraform apply
```

Phase 7 replaces the `sample_board` schedule with live ingestion writing the same board key; the API
does not change.

## 6. Terraform

Layout follows architecture §11, minus what this account cannot do:

```
infra/
├── bootstrap/        # local state; ONLY the tf-state bucket (versioned, SSE-S3, public access
│                     # block, TLS-only policy). No OIDC provider / gh roles (SCP-denied, Phase 8).
├── modules/
│   ├── data_lake/    # data + models buckets (models versioned), lifecycle, SSE-S3, PAB, TLS-only
│   ├── ecr/          # api repo: immutable tags, scan on push, keep last 3 images (cost; owner OK 2026-10-31)
│   ├── lambda_image/ # generic container Lambda + own role + log group (14 d) + policy input
│   ├── http_api/     # HTTP API, $default stage, throttling, access logs (14 d)
│   ├── schedules/    # EventBridge Scheduler (timezone Europe/Berlin) — Phase 6: sample refresh only
│   ├── web_cdn/      # web bucket, CloudFront + OAC, /api/* behaviour, headers policy, SPA function
│   ├── ssm/          # pointer params (lifecycle ignore_changes = [value])
│   ├── observability/# SNS topic + email subscription, ≤ 3 alarms now (budget stays separate)
│   └── budget/       # imports the existing puenktlich-monthly budget
└── envs/prod/        # backend.tf (S3, use_lockfile = true), main.tf, variables.tf, outputs.tf
```

- Terraform `>= 1.10` (installed 1.17.1), AWS provider `~> 6.0`, lock file committed.
- Provider: `region = var.region` (default `eu-north-1`), `profile` from the environment
  (`AWS_PROFILE=puenktlich`), default tags `project=puenktlich env=prod managed_by=terraform owner=piyal21`.
- No account id in code: bucket names are `puenktlich-<kind>-${data.aws_caller_identity.current.account_id}`.
  The state bucket name reaches `envs/prod` through a git-ignored `backend.hcl` written from the bootstrap
  output (`terraform init -backend-config=backend.hcl`).
- Image tag (`var.api_image_tag`, git SHA) and alert email (`var.alert_email`) are inputs given at apply
  time (env `TF_VAR_*`), not in committed `.tfvars`.
- `api` Lambda: image from ECR, x86_64, memory 1024 MB, timeout 15 s, env `APP_ENV=prod`,
  `AWS_REGION` (implicit), `DATA_BUCKET`, `MODELS_BUCKET`, `MODEL_POINTER_PARAM=/puenktlich/prod/model/champion_version`,
  `BOARD_KEY`, `STATIONS_FILE` absolute, `CORS_ORIGINS` empty, `LOG_LEVEL=INFO`. Memory/timeout may be
  raised (max 30 s overall budget behind API Gateway) if the measured cold start needs it (§9).
- `sample_board` Lambda: same image, `image_config.command = ["seed_handler.handler"]`, timeout 60 s,
  memory 1024 MB, env `DATA_BUCKET`, `BOARD_KEY`, `SAMPLE_SILVER_ROOT=samples/`.
- IAM (one role each, no `*`):
  - `api`: `s3:GetObject` on `data/live/boards/*` and `models/models/*`; `s3:ListBucket` on the models
    bucket restricted to prefix `models/` (`read_bundle_files` lists the bundle prefix);
    `ssm:GetParameter(s)` on the two pointer ARNs; logs on its own group.
  - `sample_board`: `s3:GetObject` + `s3:ListBucket` (prefix `samples/`) on data; `s3:PutObject` on
    exactly `live/boards/latest.json.gz`; logs.
  - Scheduler role: `lambda:InvokeFunction` on `sample_board` only.
- CloudFront:
  - default behaviour → web bucket via OAC; CachingOptimized; CloudFront Function rewrites requests
    without a file extension to `/index.html`. (Do **not** use distribution-level custom error pages:
    they would also replace API 404/503 problem+json bodies.)
  - `/api/v1/stations/*/departures*` → API origin, cache 60 s keyed on the query string (`hours`);
  - other `/api/*` → API origin, CachingDisabled, origin request policy AllViewerExceptHostHeader.
  - Response headers policy: HSTS, `X-Content-Type-Options`, `Referrer-Policy`,
    `frame-ancestors 'none'`, a CSP that the built SPA actually satisfies (builder verifies in a
    browser and records any `unsafe-inline` need). `/api/docs*` gets its own behaviour + headers policy
    whose CSP allows what Swagger UI loads (it pulls assets from a CDN).
- Alarms now: API Gateway 5xx > 5 in 5 min; API Lambda `Throttles` > 0; `sample_board` `Errors` > 0
  in 2 consecutive periods. All → SNS email (owner confirms the subscription email once).

## 7. Code changes (Python, test-first)

1. **`SsmPointer`** (`dbdelay/registry/pointer.py`), implementing `ModelPointer`: `get()` reads both
   parameters (`none` → `None`/no champion; `updated_at` = champion parameter's `LastModifiedDate`);
   `set()` writes `previous_version` first, then `champion_version`. Errors map to the existing
   `StorageError`/`ArtifactIntegrityError` family. Unit tests with moto.
2. **Pointer factory** `make_pointer(settings, store)`: `model_pointer_param` starting with `/` → SSM,
   otherwise the existing MinIO object pointer. Used by `services/api/app/deps.py`, `rollback_main`.
3. **`make publish-model`** (`dbdelay/registry/publish.py` + thin script): take the version the local
   pointer names (refuse if there is none), read that bundle from local MinIO, verify it
   (`load_bundle`), upload to the AWS models bucket, verify again from AWS, then move the SSM pointer
   (same idempotency rules as `release_version`; re-run safe; already live → no-op). Does not move
   the MLflow alias (it is local and already `@champion`). Local MinIO and AWS clients come from two
   settings sources (local `.env` vs. AWS profile) — builder's call how, but never mixing credentials.
4. **`make rollback-prod`**: `rollback()` against the SSM pointer + AWS models bucket (MLflow untouched).
5. **Sample board in AWS**: `services/api/seed_handler.py` (thin) → `seed_board(store, now=…,
   board_key=…, silver_root=SAMPLE_SILVER_ROOT)`; `make upload-sample-days` copies 9 consecutive real
   silver days (2026-08-23 … 2026-08-31, the newest in MinIO) to `samples/silver/departures/source=hf/…`
   so `pick_source_day` always finds today's weekday plus the next day, and the UTC day before covers
   the early Berlin hours of a Monday.
6. **Content-Length fix** (confirmed: a Mangum v2 POST without the header gets 411): only for requests
   that arrive through Mangum/Lambda (the whole body is already in the event), a missing header means
   "measure the body": > 4 KB → 413, otherwise continue. Under uvicorn the 411 rule stays as it is.
   Tests: Mangum v2 event without/with header, oversize body, uvicorn behaviour unchanged.
7. **Settings default** `aws_region` → `eu-north-1`.
8. Coverage stays ≥ the current level; mypy strict; no new Python dependencies (moto, boto3 exist).

## 8. Laptop deploy commands (Makefile; Git Bash `make`, `AWS_PROFILE=puenktlich`)

| Target | Does |
|---|---|
| `tf-bootstrap` | `init` + `plan` of `infra/bootstrap`; apply only after owner OK; writes `envs/prod/backend.hcl` |
| `tf-plan` / `tf-apply` | `envs/prod` plan / apply (apply asks the owner; never auto-approved by Claude) |
| `image-push` | `docker buildx build --platform linux/amd64 --target lambda`, tag = git short SHA, ECR login via `aws ecr get-login-password`, push |
| `web-deploy` | `npm --prefix frontend run build` → `aws s3 sync --delete` → CloudFront invalidation `/*` |
| `upload-sample-days` | §7.5 |
| `publish-model` / `rollback-prod` | §7.3–7.4 |
| `smoke-prod` | via CloudFront: health (model `1`), stations search, a board, `POST /predict`, `/model`, `/api/docs`, a 404 problem |
| `tf-scan` | `trivy config infra/` via the `aquasec/trivy` Docker image (pinned tag), no install |

A missing/expired login fails fast with "run `aws login --profile puenktlich`".

## 9. Rollout (every AWS write = separate owner OK)

Every command that writes to AWS — each `apply`, `image-push`, `upload-sample-days`, `publish-model`,
`rollback-prod`, `web-deploy` (sync + invalidation) — runs only after the owner's OK for that step.

1. Bootstrap: state bucket.
2. `envs/prod` stage A (`-target`): data_lake, ecr, ssm, observability, budget import — cheap, surfaces
   SCP surprises early.
3. `make image-push`, `make upload-sample-days`, `make publish-model` (local champion = v1).
4. Full apply: lambdas, HTTP API, schedules, web_cdn.
5. `make web-deploy`, `make smoke-prod`.
6. Measure: cold and warm latency of `/api/v1/health` and a board (5 samples each), image size in ECR;
   adjust memory/timeout if a cold start fails; record the numbers.
7. `terraform plan` → no changes; `make tf-scan` → clean or documented.

If any apply hits an SCP deny, stop, record it, and bring options to the owner before working around it.

## 10. Testing

- Unit (moto): SSM pointer, pointer factory, publish (MinIO→S3 copy, verify, pointer order, re-run),
  rollback-prod, seed handler, Content-Length cases. `make check` green.
- Terraform: `terraform fmt -check -recursive`, `terraform validate` per root, `make tf-scan`.
- Real (after approved applies): `make smoke-prod` green; board shows "Sample data" with departures in
  a browser (headless Chromium screenshot as in Phase 5); `terraform plan` no-op.

## 11. Cost (estimate, per architecture §16; real figures in Cost Explorer after a week)

ECR ~1.6 GB image → ~$0.16/month; S3 (< 100 MB) < $0.05; API Gateway < $0.10; Lambda, Scheduler,
CloudFront, CloudWatch (3 alarms), SNS, SSM standard params → $0 within free tiers. **≈ $0.30–0.50/month**,
covered by the credits.

## 12. Docs to update

`architecture.md` §1, §11 (bootstrap without OIDC; sample_board), §12 (identity on this account type,
no reserved concurrency), §13 (settings), §16 (cost); `phases.md` Phase 6 tasks (OIDC → Phase 8
decision); `rules.md` (**approved by owner 2026-10-31, done**: §8 "Region `eu-central-1` only" → `eu-north-1`
only, fixed by the account; §2 CI/CD row keeps "GitHub Actions + OIDC" as the target with a note that
OIDC is blocked on this account until the Phase 8 decision — long-lived keys stay forbidden);
new **ADR 0002 — AWS account type and its constraints**; README (live URL, Free plan end date);
CLAUDE.md; CHANGELOG + `Phases/phase-6-cloud-infra.md` at the end.

## 13. Exit criteria

- Public URL serves the UI; `/api/v1/health` returns model version `1`; board shows sample departures.
- Budget and alarms exist in Terraform; `terraform plan` shows no changes.
- `trivy config infra/` clean or findings documented; `make check` + `make web-check` green.
- Cold/warm latency and image size measured and written down (no invented numbers).

## 14. Left to the builder

Module internals and variable names; exact CSP string (must work with the built SPA); whether the
loader needs `s3:ListBucket`; how two credential sources are wired for `publish-model`; test layout;
Makefile details on Windows/Git Bash.

## 15. Open for later (not Phase 6)

- Phase 8 route for GitHub→AWS (A laptop-only · B Paid plan + advanced features, irreversible · C new
  standard account).
- Optional: request a higher Lambda concurrency quota (Service Quotas, free).
