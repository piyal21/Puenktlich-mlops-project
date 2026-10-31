# Phase 6 — Cloud infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the Phase 5 app (champion v1, auto-refreshed sample board) from a public CloudFront URL in AWS `eu-north-1`, with all resources in Terraform and model release/rollback through code.

**Architecture:** CloudFront → S3 (SPA via OAC) and `/api/*` → API Gateway HTTP API → container Lambda (existing API image). Model pointer moves from a MinIO object to two SSM parameters. A second Lambda (same image, other handler) rewrites the sample board every 15 min from 9 real silver days in S3. Everything is applied from the laptop (`aws login --profile puenktlich`), in stages, each AWS write after the owner's OK.

**Tech Stack:** Python 3.12, boto3, moto (`mock_aws`), FastAPI + Mangum, Terraform 1.17 / AWS provider `~> 6.0`, Docker buildx, Trivy (Docker image), GNU make in Git Bash.

**Spec:** `docs/superpowers/specs/2026-10-31-phase-6-cloud-infra-design.md` (approved 2026-10-31)

## Global Constraints

- Region `eu-north-1` only; provider `region = var.region` default `"eu-north-1"`; profile via `AWS_PROFILE=puenktlich`, never hard-coded credentials.
- No AWS account id, alert email or bucket name with an account id in committed files (`rules.md`). Names are built from `data.aws_caller_identity.current.account_id`; `backend.hcl` and `*.auto.tfvars` are git-ignored.
- **Every AWS write needs the owner's explicit OK for that step**: `terraform apply`, `image-push`, `upload-sample-days`, `publish-model`, `rollback-prod`, `web-deploy`. `terraform plan`, `validate`, `fmt`, read-only `aws` calls are fine.
- Never create NAT, ALB/NLB, EC2, RDS, MWAA, SageMaker, Elastic IP, VPC-attached Lambda, WAF, Lambda@Edge, reserved concurrency (account limit is 5).
- IAM: one role per Lambda/scheduler, exact ARNs/prefixes, no `*` actions or resources.
- Default tags on every resource: `project=puenktlich`, `env=prod`, `managed_by=terraform`, `owner=piyal21`. Log groups retention 14 days. ECR keeps last **3** images, immutable tags, scan on push.
- SSM parameters: `/puenktlich/prod/model/champion_version`, `/puenktlich/prod/model/previous_version`, type `String`, "no value" = literal `none`, Terraform `lifecycle { ignore_changes = [value] }`.
- Sample board: schedule `rate(15 minutes)`; sample silver root `samples/`; sample days 2026-08-23 … 2026-08-31 (9 days).
- API Lambda: memory 1024 MB, timeout 15 s (may be raised after measurement, ≤ 29 s). sample_board Lambda: 1024 MB, timeout 60 s, `image_config.command = ["seed_handler.handler"]`.
- API Gateway: `$default` stage, throttling rate 10 rps, burst 20. CloudFront: `PriceClass_100`, default certificate.
- No new Python or npm dependencies (moto's SSM mock needs only PyYAML, already a dependency). Python: mypy strict on `src/dbdelay/`, ruff config as is, coverage not lower than today (97 %).
- Commits: Conventional Commits, **no** `Co-Authored-By: Claude` trailer. Run `uv run ruff check --fix` + `uv run ruff format` before `git add` (pre-commit otherwise aborts).
- Tool PATH in Claude's shells: PowerShell `$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')`; Bash prefix from CLAUDE.md (uv, make, gh) plus `/c/Program Files/Terraform` and `/c/Program Files/Amazon/AWSCLIV2`.

## Review Focus

1. **Expired `aws login` session** while running a prod CLI → one-line "run `aws login --profile puenktlich`", exit 1, no traceback (Task 2 test `test_publish_main_expired_login_is_one_line`).
2. **SSM pointer half-written** (previous set, champion still `none`, or a parameter missing) → `get()` returns `None` (no champion), never crashes the API (Task 1 tests `test_ssm_pointer_missing_parameter_is_none`, `test_ssm_pointer_champion_none_is_none`).
3. **Sample days not uploaded yet** when the schedule fires → handler raises (Lambda error → alarm) and does **not** overwrite the last good board (Task 4 test `test_seed_handler_without_samples_raises_and_keeps_board`).
4. **Base64-encoded oversize POST through Lambda** (API Gateway sets `isBase64Encoded`) → 413, not 411/500 (Task 3 test `test_lambda_post_base64_oversize_is_413`).
5. **SPA deep link vs. API 404 through CloudFront**: `/station/8010101` returns `index.html`; `/api/v1/stations/123/departures` keeps its problem+json 404 (Task 8 `smoke_prod.py` checks `deep_link_serves_spa`, `api_404_is_problem_json`).

---

### Task 1: SSM model pointer + pointer factory

**Files:**
- Modify: `src/dbdelay/registry/pointer.py`
- Modify: `src/dbdelay/config.py` (`aws_region` default → `"eu-north-1"`)
- Modify: `services/api/app/deps.py` (use `make_pointer`)
- Modify: `src/dbdelay/registry/release.py` (`rollback_main` uses `make_pointer`)
- Modify: `tests/unit/conftest.py` (`AWS_DEFAULT_REGION` and bucket `LocationConstraint` → `eu-north-1`)
- Test: `tests/unit/test_ssm_pointer.py`; update `tests/unit/test_config.py` default-region assertion if present

**Interfaces:**
- Produces:
  - `NONE_VALUE = "none"`
  - `class SsmClient(Protocol)`: `get_parameters(*, Names: list[str]) -> Mapping[str, Any]`, `put_parameter(*, Name: str, Value: str, Type: str, Overwrite: bool) -> Mapping[str, Any]`
  - `class SsmPointer` (implements `ModelPointer`): `__init__(self, client: SsmClient, champion_param: str, previous_param: str | None = None)`; `previous_param` defaults to `champion_param` with its last path segment replaced by `previous_version`.
  - `def make_ssm_client(region: str) -> SsmClient` (boto3, same timeouts/retries as `storage._BOTO_CONFIG`)
  - `def make_pointer(settings: Settings, store: ObjectStore) -> ModelPointer` — `settings.model_pointer_param.startswith("/")` → `SsmPointer(make_ssm_client(settings.aws_region), settings.model_pointer_param)`, else `ObjectStorePointer(store)`.

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_ssm_pointer.py`, each inside `mock_aws()`, client `boto3.client("ssm", region_name="eu-north-1")`, params created like Terraform does with value `none`)

```python
CHAMP = "/puenktlich/prod/model/champion_version"
PREV = "/puenktlich/prod/model/previous_version"

def test_ssm_pointer_champion_none_is_none(): ...        # both "none" → get() is None
def test_ssm_pointer_missing_parameter_is_none(): ...    # params absent → get() is None
def test_ssm_pointer_set_then_get():
    state = pointer.set("2", "1")
    assert (state.champion_version, state.previous_version) == ("2", "1")
    assert pointer.get().champion_version == "2" and pointer.get().previous_version == "1"
    assert pointer.get().updated_at.tzinfo is not None   # from champion LastModifiedDate
def test_ssm_pointer_set_without_previous_writes_none(): ...  # previous param value == "none"
def test_ssm_pointer_derives_previous_param_name(): ...       # SsmPointer(c, CHAMP) writes PREV
def test_ssm_pointer_aws_error_is_external_service_error(): ...  # ClientError (e.g. AccessDenied via stub) → ExternalServiceError
def test_make_pointer_picks_ssm_for_slash_param(): ...   # Settings(model_pointer_param=CHAMP) → SsmPointer
def test_make_pointer_picks_object_store_otherwise(): ...# default setting → ObjectStorePointer
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_ssm_pointer.py -q` → FAIL (`ImportError: SsmPointer`).
- [ ] **Step 3: Implement** in `pointer.py`: `get()` uses one `get_parameters` call for both names; a missing name or value `none` for champion → `None`; previous `none` → `None`. `set()` writes previous first, then champion (`Overwrite=True`, `Type="String"`), returns `PointerState` with `updated_at=datetime.now(UTC)`. Wrap `botocore` `ClientError`/`BotoCoreError` in `ExternalServiceError` (message without values). Switch `deps.py` and `rollback_main` to `make_pointer`. Change `aws_region` default and conftest region.
- [ ] **Step 4: Run** `uv run pytest tests/unit -q -x` → all pass (existing pointer/release/API tests still green).
- [ ] **Step 5: Commit** `feat(registry): SSM model pointer and pointer factory; default region eu-north-1`

---

### Task 2: `make publish-model` and `make rollback-prod`

**Files:**
- Create: `src/dbdelay/registry/publish.py`
- Create: `scripts/publish_model.py`, `scripts/rollback_prod.py` (thin: `raise SystemExit(<main>())`)
- Modify: `src/dbdelay/storage.py` — add `make_aws_s3_client(region: str) -> S3Client` (no endpoint, default credential chain, project `_BOTO_CONFIG`)
- Modify: `src/dbdelay/training/tracking.py` — add `class NullTracker` (`set_alias` does nothing) if no no-op tracker exists
- Modify: `Makefile` (targets below)
- Test: `tests/unit/test_publish.py`

**Interfaces:**
- Consumes: `SsmPointer`, `make_ssm_client` (Task 1); `release_version`, `rollback`, `read_bundle_files`, `load_bundle`, `ObjectStorePointer`.
- Produces:
  - `def publish_champion(source: ObjectStore, source_pointer: ModelPointer, target: ObjectStore, target_pointer: ModelPointer) -> PointerState` — version = `source_pointer.get().champion_version` (none → `ModelNotAvailableError`); `read_bundle_files(source, v)` → `release_version(target, target_pointer, NullTracker(), manifest=..., files=...)` (re-uses its idempotency, verify-from-target and pointer order; already live → unchanged state).
  - `def publish_main(argv: Sequence[str] | None = None) -> int` — args `--models-bucket` (required), `--pointer-param` (default `/puenktlich/prod/model/champion_version`), `--region` (default `eu-north-1`). Source = `get_settings()` MinIO store + `ObjectStorePointer`; target = `make_aws_s3_client(region)` + `SsmPointer`.
  - `def rollback_prod_main(argv: Sequence[str] | None = None) -> int` — same args; `rollback(target, SsmPointer, NullTracker(), model_name=cfg.registry.model_name)`.
  - Both CLIs: `DbDelayError` → `"publish refused: …"` / `"rollback refused: …"` on stderr, exit 1. botocore `NoCredentialsError`, `TokenRetrievalError`, `UnauthorizedSSOTokenError`, or `ClientError` code `ExpiredToken`/`ExpiredTokenException` → stderr exactly `AWS login missing or expired: run "aws login --profile puenktlich"`, exit 1.
- Makefile (each prints the target bucket first; reads bucket from Terraform output):
  - `publish-model: ## Copy the local champion to AWS and point SSM at it (AWS write — owner OK)` → `uv run python scripts/publish_model.py --models-bucket $$(terraform -chdir=infra/envs/prod output -raw models_bucket)`
  - `rollback-prod: ## Swap the prod champion back (AWS write — owner OK)` likewise.

- [ ] **Step 1: Write the failing tests** (two moto buckets in one `mock_aws()`: `source` with a released v1 via `release_version` + `ObjectStorePointer`, `target` empty; `SsmPointer` on moto SSM with `none` params)

```python
def test_publish_copies_local_champion_and_moves_ssm(): ...
    # target has models/1/ (load_bundle ok); SsmPointer.get() → champion "1", previous None
def test_publish_same_version_again_is_noop(): ...      # second call: same PointerState values, no new previous
def test_publish_without_local_champion_raises(): ...   # source pointer empty → ModelNotAvailableError
def test_publish_refuses_target_with_different_files(): ...  # target models/1 tampered → ArtifactIntegrityError, SSM unchanged
def test_publish_new_version_sets_previous(): ...       # local champion 2 after 1 → SSM champion 2, previous 1
def test_rollback_prod_swaps_ssm(): ...                 # champion 2/prev 1 → 1/2
def test_publish_main_expired_login_is_one_line(capsys, monkeypatch): ...
    # monkeypatch make_aws_s3_client to raise botocore NoCredentialsError → exit 1, stderr ==
    # 'AWS login missing or expired: run "aws login --profile puenktlich"\n'
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_publish.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `publish.py`, `make_aws_s3_client`, `NullTracker`, scripts, Makefile targets.
- [ ] **Step 4: Run** `uv run pytest tests/unit -q` → pass; `make typecheck` → no errors.
- [ ] **Step 5: Commit** `feat(registry): publish the local champion to AWS and roll back prod via SSM`

---

### Task 3: Content-Length behind Lambda

**Files:**
- Modify: `services/api/app/errors.py` (`request_context`)
- Test: `tests/unit/test_lambda_handler.py` (reuse its `EVENT` shape for a POST to `/api/v1/predict`)

**Interfaces:**
- Behaviour: if the request came through Mangum (`"aws.event" in request.scope`) and `content-length` is missing, take the length of `await request.body()` instead; > `MAX_BODY_BYTES` → 413 problem; otherwise continue (route sees the same body). Requests without `aws.event` keep today's 411 rule.

- [ ] **Step 1: Write the failing tests**

```python
def test_lambda_post_without_content_length_reaches_route(s3_store): ...
    # valid /predict JSON, no content-length header → statusCode 200 (deps from make_deps)
def test_lambda_post_with_content_length_unchanged(s3_store): ...   # → 200
def test_lambda_post_oversize_without_header_is_413(s3_store): ...  # 5000-byte body → 413, type /errors/payload-too-large
def test_lambda_post_base64_oversize_is_413(s3_store): ...          # isBase64Encoded=True, 5000 bytes → 413
```
Plus existing uvicorn-path test (TestClient, chunked POST without length) must still give 411.

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_lambda_handler.py -q` → the no-header test FAILs with 411.
- [ ] **Step 3: Implement** the scope check in `request_context`.
- [ ] **Step 4: Run** `uv run pytest tests/unit -q` → pass.
- [ ] **Step 5: Commit** `fix(api): measure the body when Lambda requests arrive without Content-Length`

---

### Task 4: Sample board Lambda handler + sample-day upload

**Files:**
- Create: `services/api/seed_handler.py` (thin)
- Create: `src/dbdelay/serving/samples.py`
- Create: `scripts/upload_sample_days.py` (thin)
- Modify: `src/dbdelay/config.py` — `sample_silver_root: str = "samples/"`
- Modify: `services/api/Dockerfile` — `COPY services/api/seed_handler.py ${LAMBDA_TASK_ROOT}/`
- Modify: `Makefile` — `upload-sample-days: ## Copy 9 real silver days to the AWS data bucket (AWS write — owner OK)`
- Test: `tests/unit/test_samples.py`, `tests/unit/test_seed_handler.py`

**Interfaces:**
- Consumes: `seed_board(store, *, now, board_key, source_day=None, silver_root)`, `silver_key(day, root)`, `make_aws_s3_client` (Task 2).
- Produces:
  - `SAMPLE_DAYS: tuple[date, ...]` = 2026-08-23 … 2026-08-31
  - `def copy_silver_days(source: ObjectStore, target: ObjectStore, days: Sequence[date], target_root: str) -> int` — copies `silver_key(d)` → `silver_key(d, target_root)` byte-identical; a missing source day → `NotFoundError` before anything is written (check all first).
  - `def upload_main(argv: Sequence[str] | None = None) -> int` — `--data-bucket` required, `--region` default `eu-north-1`; same error mapping as Task 2 (`AWS login missing or expired…`).
  - `seed_handler.handler(event: Mapping[str, Any], context: object) -> dict[str, Any]` → builds `ObjectStore(make_s3_client(settings), settings.data_bucket)`, calls `seed_board(..., now=datetime.now(UTC), board_key=settings.board_key, silver_root=settings.sample_silver_root)`, logs and returns `{"departures": n, "replayed_from": "YYYY-MM-DD"}`. Errors propagate (Lambda error → alarm).

- [ ] **Step 1: Write the failing tests**

```python
def test_copy_silver_days_copies_bytes_identically(): ...       # 9 days, target keys under samples/, bytes equal
def test_copy_silver_days_missing_day_writes_nothing(): ...     # one day absent → NotFoundError, target empty
def test_seed_handler_writes_sample_board(monkeypatch): ...      # moto bucket with samples/ days → board at BOARD_KEY, source "sample"
def test_seed_handler_without_samples_raises_and_keeps_board(): ...  # pre-existing board bytes unchanged, error raised
```
Silver fixtures: reuse whatever `tests/unit/test_seed.py` uses to write silver days.

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_samples.py tests/unit/test_seed_handler.py -q` → FAIL.
- [ ] **Step 3: Implement** module, handler, script, Settings field, Dockerfile line, Makefile target. `tests/unit/test_serving_imports.py` must still pass (no sklearn on the serving path).
- [ ] **Step 4: Run** `make check` → green.
- [ ] **Step 5: Commit** `feat(serving): sample-board Lambda handler and sample-day upload`

---

### Task 5: Terraform bootstrap (state bucket) + infra tooling

**Files:**
- Create: `infra/bootstrap/{versions.tf,main.tf,outputs.tf}`
- Create: `infra/.trivyignore` (only if findings are accepted, each with a reason comment)
- Modify: `.gitignore` — `infra/**/backend.hcl`, `infra/**/*.auto.tfvars`, `infra/**/.terraform.lock.hcl` **not** ignored (commit lock files)
- Modify: `Makefile` — `TF = terraform`; `tf-fmt`, `tf-validate`, `tf-scan`, `tf-bootstrap-plan`, `tf-bootstrap-apply`

**Interfaces:**
- Produces: output `state_bucket` = `puenktlich-tfstate-<account_id>`; `make tf-bootstrap-apply` writes `infra/envs/prod/backend.hcl` with `bucket`, `key = "prod/terraform.tfstate"`, `region = "eu-north-1"`, `use_lockfile = true`, `encrypt = true`.
- Bucket: versioning on, SSE-S3, public access block (all four true), bucket policy denying `aws:SecureTransport = false`, `lifecycle { prevent_destroy = true }`, noncurrent versions expire after 90 days.
- `tf-scan`: `docker run --rm -v "$(CURDIR)/infra:/infra" aquasec/trivy:<pinned tag> config --exit-code 1 /infra` (pin a tag ≥ 2 weeks old; record it).

- [ ] **Step 1:** Write the files; `terraform -chdir=infra/bootstrap init -backend=false` then `make tf-fmt tf-validate` → "Success! The configuration is valid."
- [ ] **Step 2:** `make tf-scan` → 0 HIGH/CRITICAL, or each finding fixed / documented in `.trivyignore` with a reason.
- [ ] **Step 3:** `make tf-bootstrap-plan` → plan shows only the state bucket + its settings (read-only, no OK needed).
- [ ] **Step 4: Commit** `infra(bootstrap): Terraform state bucket and infra make targets`
- [ ] **Step 5 (AWS write — STOP for owner OK):** `make tf-bootstrap-apply`; check `backend.hcl` exists and is git-ignored (`git status` clean of it). Record result.

---

### Task 6: Terraform `envs/prod` stage A — data_lake, ecr, ssm, budget, alerts topic

**Files:**
- Create: `infra/modules/{data_lake,ecr,ssm,budget,observability}/{main.tf,variables.tf,outputs.tf}`
- Create: `infra/envs/prod/{versions.tf,backend.tf,providers.tf,main.tf,variables.tf,outputs.tf}`
- Modify: `Makefile` — `tf-init`, `tf-plan`, `tf-apply-stage-a` (`-target=module.data_lake -target=module.ecr -target=module.ssm -target=module.budget -target=module.observability`), `tf-apply`

**Interfaces:**
- Produces (outputs of `envs/prod`): `data_bucket`, `models_bucket`, `web_bucket` (bucket created in Task 7, output added then), `ecr_repository_url`, `pointer_param_champion`, `pointer_param_previous`, `alerts_topic_arn`.
- `data_lake`: data bucket (versioning off; lifecycle `bronze/` expire 30 d — prepared for Phase 7) and models bucket (versioning on); both SSE-S3, PAB, TLS-only policy.
- `ecr`: repo `puenktlich/api`, `image_tag_mutability = "IMMUTABLE"`, scan on push, lifecycle keep last 3.
- `ssm`: the two parameters, value `"none"`, `ignore_changes = [value]`.
- `budget`: `aws_budgets_budget` matching the existing `puenktlich-monthly` (USD 1, MONTHLY, Credit + Refund excluded, notifications 100 % ACTUAL and 500 % FORECASTED to `var.alert_email`); `import { to = module.budget.aws_budgets_budget.monthly, id = "<account_id>:puenktlich-monthly" }` in `envs/prod/main.tf` built from the caller-identity data source. If the provider cannot express the charge-type exclusion so that `plan` is clean after import, keep the budget out of Terraform, document why in ADR 0002, and tell the owner.
- `observability` (stage A part): SNS topic `puenktlich-alerts` + email subscription to `var.alert_email`; alarms are added in Task 7.
- Variables with no committed values: `alert_email` (sensitive), `api_image_tag`; passed as `TF_VAR_*` at run time.

- [ ] **Step 1:** Write modules + root; `make tf-init` (uses `-backend-config=backend.hcl`), `make tf-fmt tf-validate` → valid; `make tf-scan` → clean/documented.
- [ ] **Step 2:** `make tf-plan` (stage-A targets) → creates buckets, ECR, 2 params, SNS + subscription; budget shows **import** with no changes after import (else fix config).
- [ ] **Step 3: Commit** `infra(prod): data lake, ECR, SSM pointer, budget import and alert topic`
- [ ] **Step 4 (AWS write — STOP for owner OK):** `make tf-apply-stage-a`. Owner confirms the SNS subscription email. Any SCP deny → stop, record, bring options.

---

### Task 7: Terraform `envs/prod` full — Lambdas, HTTP API, schedule, CloudFront, alarms

**Files:**
- Create: `infra/modules/{lambda_image,http_api,schedules,web_cdn}/{main.tf,variables.tf,outputs.tf}`
- Create: `infra/modules/web_cdn/spa_rewrite.js` (CloudFront Function, `cloudfront-js-2.0`)
- Modify: `infra/modules/observability/*` (alarms), `infra/envs/prod/{main.tf,outputs.tf}`

**Interfaces:**
- Consumes: Task 6 outputs; `var.api_image_tag`.
- Produces outputs: `cloudfront_domain`, `cloudfront_distribution_id`, `web_bucket`, `api_endpoint`, `api_function_name`, `sample_board_function_name`.
- `lambda_image` inputs: `name`, `image_uri`, `command` (nullable), `memory_mb`, `timeout_s`, `environment` (map), `policy_json`; creates role (trust `lambda.amazonaws.com`), log group `/aws/lambda/<name>` (14 d), function (`package_type = "Image"`, `architectures = ["x86_64"]`).
- `api` env: `APP_ENV=prod`, `DATA_BUCKET`, `MODELS_BUCKET`, `MODEL_POINTER_PARAM=/puenktlich/prod/model/champion_version`, `BOARD_KEY=live/boards/latest.json.gz`, `LOG_LEVEL=INFO`. Policy: `s3:GetObject` data `live/boards/*`, models `models/*`; `s3:ListBucket` models with `s3:prefix` condition `models/*`; `ssm:GetParameters` + `ssm:GetParameter` on both param ARNs.
- `sample_board` env: `APP_ENV=prod`, `DATA_BUCKET`, `BOARD_KEY`, `SAMPLE_SILVER_ROOT=samples/`. Policy: `s3:GetObject` data `samples/*`, `s3:ListBucket` data with prefix `samples/*`, `s3:PutObject` exactly `live/boards/latest.json.gz`.
- `http_api`: HTTP API, Lambda proxy integration payload 2.0, route `$default`, stage `$default` auto-deploy, throttling 10/20, access log group (14 d), `aws_lambda_permission` scoped to the API's execution ARN.
- `schedules`: `aws_scheduler_schedule` `rate(15 minutes)`, `schedule_expression_timezone = "Europe/Berlin"`, flexible window OFF, role allowing `lambda:InvokeFunction` on the sample_board ARN only, retry policy 0 retries (next run comes in 15 min).
- `web_cdn`: web bucket (SSE-S3, PAB, TLS-only, policy allowing `cloudfront.amazonaws.com` `s3:GetObject` with `AWS:SourceArn` = distribution); OAC (sigv4, always); origins S3 + API (`domain = trimprefix(api_endpoint, "https://")`, HTTPS only); behaviours in order: `/api/docs*` and `/api/openapi.json` (CachingDisabled, AllViewerExceptHostHeader, docs headers policy), `/api/v1/stations/*/departures*` (custom cache policy min/default/max TTL 60 s, query string `hours` in key; AllViewerExceptHostHeader), `/api/*` (CachingDisabled, AllViewerExceptHostHeader, methods incl. POST), default → S3 (CachingOptimized, function association viewer-request `spa_rewrite.js`, main headers policy). `default_root_object = "index.html"`, `price_class = "PriceClass_100"`, `viewer_protocol_policy = "redirect-to-https"`, no custom error responses.
- `spa_rewrite.js` rule: if the URI has no `.` in its last path segment → `request.uri = "/index.html"`; otherwise unchanged. Only attached to the default (S3) behaviour.
- Headers policies: HSTS (max-age 31536000, includeSubdomains), nosniff, `Referrer-Policy: strict-origin-when-cross-origin`, frame options DENY, CSP for the SPA; the docs policy's CSP additionally allows the Swagger UI CDN the FastAPI version loads (check `app.main` docs settings / rendered HTML).
- `observability` alarms (all → SNS topic, `treat_missing_data = "notBreaching"`): API GW `5xx` sum > 5 over 5 min; api Lambda `Throttles` sum > 0 over 5 min; sample_board Lambda `Errors` sum > 0 for 2 of 2 periods of 15 min.

- [ ] **Step 1:** Write modules, function, wiring; `make tf-fmt tf-validate` → valid; `make tf-scan` → clean/documented.
- [ ] **Step 2:** With `TF_VAR_api_image_tag=<a tag that exists only after Task 8 push>`, `make tf-plan` must at least validate the graph (plan may fail on the missing image only at apply — fine).
- [ ] **Step 3: Commit** `infra(prod): API and sample-board Lambdas, HTTP API, schedule, CloudFront and alarms`

---

### Task 8: Deploy commands and prod smoke test

**Files:**
- Create: `scripts/smoke_prod.py` (stdlib `urllib` only)
- Modify: `Makefile` — `image-push`, `web-deploy`, `smoke-prod`, and a `aws-check` helper used by every AWS target (`aws sts get-caller-identity --profile $(AWS_PROFILE) >/dev/null || { echo 'AWS login missing or expired: run "aws login --profile puenktlich"'; exit 1; }`)
- Test: `tests/unit/test_smoke_prod.py` (checks against a stubbed opener)

**Interfaces:**
- `AWS_PROFILE ?= puenktlich`, `AWS_REGION ?= eu-north-1`, `IMAGE_TAG ?= $(shell git rev-parse --short=12 HEAD)`; exported for sub-commands.
- `image-push` (AWS write): refuse if the working tree is dirty (`git diff --quiet HEAD`); ECR login via `aws ecr get-login-password | docker login --username AWS --password-stdin <registry>`; `docker buildx build --platform linux/amd64 --target lambda --provenance=false -f services/api/Dockerfile -t <repo>:$(IMAGE_TAG) --push .` (`--provenance=false`: Lambda rejects OCI index manifests).
- `web-deploy` (AWS write): `npm --prefix frontend ci && npm --prefix frontend run build`, `aws s3 sync frontend/dist s3://<web_bucket> --delete`, `aws cloudfront create-invalidation --paths "/*"`.
- `smoke_prod.py --base https://<cloudfront_domain>` runs named checks and prints one `PASS/FAIL name` line each, exit 1 on any FAIL:
  `health_reports_model` (`/api/v1/health` 200, model version `"1"`), `stations_search` (`?q=erfurt` contains EVA 8010101), `board_is_sample` (Erfurt departures 200, `data_source == "sample"`, ≥ 1 departure, `stale` false), `predict_post` (POST valid body → 200 with `p_late`), `model_info` (`/api/v1/model` 200), `api_docs` (`/api/docs` 200 HTML), `api_404_is_problem_json` (EVA 123 → 404 `application/problem+json`), `deep_link_serves_spa` (`/station/8010101` → 200 `text/html` containing `<div id="root">`), `security_headers` (`strict-transport-security` and `content-security-policy` on `/`).

- [ ] **Step 1: Write the failing tests** for `smoke_prod.py`: each check passes on a canned good response and fails on a canned bad one (e.g. `deep_link_serves_spa` fails on a 403; `api_404_is_problem_json` fails when body is HTML).
- [ ] **Step 2: Run** → FAIL; **Step 3:** implement; **Step 4:** `make check` → green.
- [ ] **Step 5: Commit** `build(deploy): image push, web deploy and prod smoke test targets`

---

### Task 9: Rollout to AWS (each step: STOP for owner OK, then run, then record)

No code unless a step fails (then: `superpowers:systematic-debugging`, fix test-first, commit).

- [ ] **Step 1:** `make image-push` → image `puenktlich/api:<tag>` in ECR; record compressed size (`aws ecr describe-images`).
- [ ] **Step 2:** `make upload-sample-days` → 9 objects under `samples/`; `make publish-model` → SSM champion `1`, previous `none`.
- [ ] **Step 3:** `TF_VAR_api_image_tag=<tag> make tf-apply` → full stack. Then invoke the sample-board Lambda once (`aws lambda invoke`) so a board exists before the first schedule tick.
- [ ] **Step 4:** `make web-deploy`; `make smoke-prod` → all PASS. Headless-Chromium screenshot of the board (Phase 5 method: `zenika/alpine-chrome` against the CloudFront URL) shows "Sample data" and departures.
- [ ] **Step 5:** Measure (5 samples each, `curl -w "%{time_total}"`): cold start (`aws lambda update-function-configuration` env touch, or wait > 15 min idle) and warm for `/api/v1/health` and an Erfurt board; if a cold board call fails or exceeds the timeout, raise memory to 2048 MB and/or timeout (≤ 29 s) via Terraform, re-apply (owner OK), re-measure. Record numbers.
- [ ] **Step 6:** `make tf-plan` → "No changes."; `make tf-scan` → clean/documented. Wait ≥ 30 min, confirm the board's `data_as_of` advanced (schedule works) and no alarm email arrived.
- [ ] **Step 7 (owner OK):** run `make rollback-prod` once. Only v1 exists, so it must refuse: `rollback refused: no previous model version…`, exit 1, SSM unchanged.

---

### Task 10: Docs, ADR, branch record

**Files:**
- Create: `docs/adr/0002-aws-account-type.md` (context: new-experience account; decision: eu-north-1, laptop applies, no OIDC until Phase 8 decision; consequences incl. Free plan end 2027-04-25, Lambda concurrency 5, budget handling)
- Modify: `docs/architecture.md` §1, §2 (sample_board), §11, §12, §13, §16; `docs/phases.md` Phase 6 (tasks done, OIDC moved), Phase 8 note; `README.md` (live URL, how to deploy, Free plan limitation); `.env.example` if settings changed; `CLAUDE.md` Status + Session log
- Create: `Phases/phase-6-cloud-infra.md`; Modify: `Phases/README.md`, `CHANGELOG.md` (Built · Key decisions · Tested · Known gaps · Docs touched — measured numbers only, else `TBD`)
- Create (outside repo): `.git/MERGE_SUMMARY.txt`

- [ ] **Step 1:** Write docs; no account id / email / bucket names with account ids anywhere (`git grep -nE "[0-9]{12}"` shows none of the account's id).
- [ ] **Step 2:** `make check`, `make web-check`, `make test-integration` (stack up), `uv run pre-commit run --all-files` → all green.
- [ ] **Step 3:** Whole-branch review (`code-reviewer` subagent), fix confirmed findings test-first, `security-review` skill on the infra diff.
- [ ] **Step 4: Commit** `docs(phase-6): ADR 0002, architecture, phase doc, changelog`; push branch (owner OK); hand over merge message.
