# Phase 6 — Cloud infrastructure (Terraform)

**Goal:** the Phase 5 app runs on the public internet. A CloudFront URL serves the React site and the FastAPI app
(champion v1) on AWS Lambda, with every AWS resource defined in Terraform. Until live data arrives in Phase 7, the
board shows clearly labelled sample departures that refresh by themselves.

**Live:** https://d37lj2a19ugjqm.cloudfront.net

**Status:** ✅ built and deployed on branch `phase-6/cloud-infra` (2026-11-02). The branch is kept.

---

## 1. What was built

| Deliverable | File(s) | What it is |
|---|---|---|
| Terraform state | [`infra/bootstrap/`](../infra/bootstrap/) | One private, versioned, encrypted S3 bucket where Terraform keeps its records. Applied once with local state. |
| Storage, registry, pointer, budget, alerts | [`infra/modules/`](../infra/modules/) `data_lake`, `ecr`, `ssm`, `budget`, `observability` | Data and models buckets; the image repository (keeps the last 3 images); two SSM parameters that say which model is live; the hand-made $1 budget, imported; an SNS email topic and 3 alarms. |
| Compute and edge | `lambda_image`, `http_api`, `schedules`, `web_cdn` | One reusable container-Lambda module (own role and log group per function), the HTTP API (10 requests/s, burst 20), the 15-minute schedule, and CloudFront + web bucket. |
| Environment | [`infra/envs/prod/`](../infra/envs/prod/) | Wires the modules together, including one narrow IAM policy per Lambda. Account-specific values stay in git-ignored files. |
| SSM model pointer | [`src/dbdelay/registry/pointer.py`](../src/dbdelay/registry/pointer.py) | `SsmPointer` + `make_pointer`: a setting that starts with `/` means "read SSM"; otherwise the MinIO pointer from Phase 4 is used. |
| Model release to AWS | [`src/dbdelay/registry/publish.py`](../src/dbdelay/registry/publish.py), `make publish-model`, `make rollback-prod` | Copies the **local champion** to S3, verifies it there, then moves the SSM pointer. It takes no version argument, so it can't become a manual promotion. |
| Sample board in the cloud | [`services/api/seed_handler.py`](../services/api/seed_handler.py), [`src/dbdelay/serving/samples.py`](../src/dbdelay/serving/samples.py), `make upload-sample-days` | A second Lambda (same image, other handler) replays one of 9 real days onto "now" every 15 minutes. |
| Content-Length fix | [`services/api/app/errors.py`](../services/api/app/errors.py) | Behind Lambda a POST may arrive without that header. The API now measures the body instead of answering 411. Local behaviour is unchanged. |
| Deploy commands | [`Makefile`](../Makefile), [`scripts/smoke_prod.py`](../scripts/smoke_prod.py) | `tf-*`, `image-push`, `web-deploy`, `smoke-prod`, `aws-check`. There are no CI deploys on this account (see section 3). |
| Decision record | [`docs/adr/0002-aws-account-type.md`](../docs/adr/0002-aws-account-type.md) | What this AWS account can and can't do, and what we did about it. |

## 2. How it works

```text
browser ──HTTPS──► CloudFront (security headers)
                    ├─ pages and files ──► private S3 web bucket
                    │     (paths like /station/8010101 are rewritten to /index.html)
                    └─ /api/*  ──► API Gateway ──► Lambda `api`
                          boards cached 60 s        ├─ SSM: which model version is live?
                                                    ├─ S3 models: the model files (checksums verified)
                                                    └─ S3 data: live/boards/latest.json.gz

EventBridge Scheduler (every 15 min) ──► Lambda `sample-board`
      reads samples/silver/… (9 real days) → writes live/boards/latest.json.gz

Laptop: terraform, make image-push, make publish-model, make web-deploy
```

Phase 7 replaces the schedule's target with real ingestion that writes the same board file. The API doesn't change.

## 3. Key decisions and why

The AWS account uses AWS's new sign-up experience (Free plan). That decided a lot; ADR 0002 has the evidence.

| Decision | Why |
|---|---|
| Region `eu-north-1` (Stockholm), not Frankfurt | The account type fixes the region; Frankfurt is refused. Changing it needs an irreversible paid upgrade. |
| Deploy from the laptop | AWS's own rules on this account refuse the GitHub OIDC link (tested). Stored AWS keys in GitHub stay forbidden. The route for automatic deploys is decided before Phase 8. |
| Lambdas at 512 MB | The account's limit. Measured peak use is 303 MB (API) and 366 MB (sample board). |
| Sample board every 15 min, not 30 | The site marks a board stale after 20 min, so 30 would show "stale" a third of the time. |
| No distribution-level error pages for the SPA | They would also replace API 404/503 error bodies. A small CloudFront Function rewrites page paths instead. |
| `publish-model` publishes only the local champion | Rules forbid manual promotion; the gate's choice is the only thing it can send. |
| Python uses a `credential_process` profile | boto3 can't read `aws login` sessions without an extra library, so the AWS CLI hands them over. No new dependency. |
| Saved plans (`plan -out`, then apply that file) | What gets applied is exactly what was reviewed. |
| Some Trivy findings accepted, each with a reason | Customer-managed KMS keys (~$1/month each, the whole budget), access logs, WAF (not available here), X-Ray (needs `*` permissions). Revisit in Phase 10. |

## 4. How it was tested

| Check | Command | Result |
|---|---|---|
| Unit tests | `make check` | 452 passed, coverage 98 %, lint and strict types clean |
| Frontend | `make web-check` | 47 passed |
| Integration (MinIO, MLflow) | `make test-integration` | 8 passed |
| Terraform | `make tf-fmt tf-validate`, `make tf-scan` | valid; Trivy clean with documented exceptions |
| Public site end to end | `make smoke-prod` | 10/10: health reports model `1`, search, sample board, `/predict`, `/model`, `/api/docs`, 404 as problem+json, deep link serves the site, security headers, `index.html` revalidates |
| Real browser | headless Chromium in Docker | 0 CSP violations after the font fix; board, sample banner and `/api/docs` render |
| No drift | `make tf-plan` | "No changes" |
| Rollback safety | `make rollback-prod` | Correctly refused: only v1 exists; SSM unchanged |

Measured on AWS at 512 MB:

| What | Result |
|---|---|
| Board through CloudFront, cached | 0.07–0.22 s |
| Board through CloudFront, not cached | 3.12 s |
| Board on Lambda, warm | 1.74–2.08 s |
| Board on Lambda, cold | 6.8–6.9 s |
| Health through CloudFront | 0.11–0.15 s |
| Normal cold-start init | 2.1–2.3 s |
| Very first start (image not cached) | 17.6 s, within API Gateway's limit |
| API image in ECR | 386.8 MB compressed |

## 5. What real runs taught us

Tests alone wouldn't have found these:

- **CloudFront needs account verification.** New accounts get a 403 until AWS Support verifies them.
- **Lambda memory limit.** `CreateFunction` refused 1024 MB on this account.
- **The browser found a CSP bug.** Vite inlines small fonts as `data:` URIs and the first CSP blocked them. All 9 smoke checks had passed.
- **Independent review found more.** Credential errors could end in a traceback; a half-failed rollback could report success on retry; an empty Terraform output could send the ECR token to Docker Hub; an uncached-header `index.html` could show a blank page after a deploy. All fixed test-first.
- **Windows and Docker.** The Windows credential store rejects ECR tokens, and a separate Docker config folder also hides `buildx`. Login and push now use their own folder; the build uses the normal one.
- **A secret landed in the repo folder.** A Windows path lost its backslashes, so the ECR login file was written inside the project. It was never committed and is not in the image; it was deleted and the path fixed.
- **Git Bash rewrites paths** like `/aws/...` and `/*`. Recipes set `MSYS_NO_PATHCONV=1`.

## 6. How to run it

Full steps are in [README section 10](../README.md#10-deploy-your-own). In short:

```bash
aws login --profile puenktlich       # 12 h session
make tf-plan && make tf-apply        # infrastructure
make image-push                      # then set api_image_tag in infra/envs/prod/terraform.auto.tfvars
make publish-model                   # local champion → S3 + SSM (needs `make up`)
make web-deploy                      # website
make smoke-prod                      # check the public site
make rollback-prod                   # go back one model version
```

## 7. Known gaps

- **The API misses the PRD's latency target at the origin.** Warm board 1.74–2.08 s (target p95 < 300 ms), cold board 6.8–6.9 s (target < 6 s). Cached responses (0.07–0.22 s) meet it. The owner decides whether to accept this for this account or optimise scoring.
- **Alarm emails need one click.** The SNS subscription must be confirmed by email.
- **Sample data only** until Phase 7. It's labelled on the page.
- **The Free plan ends on 2027-04-25.** The site goes offline then, or earlier if the $100 credits run out, unless the plan is upgraded or the stack moves to another account.
- **No automatic deploys yet.** Decide A (laptop only), B (paid plan + advanced features, irreversible) or C (new standard account) before Phase 8.
- **The API Gateway address can be reached without CloudFront.** Its throttle (10 requests/s) limits the damage.
- **AWS cost is still an estimate** (about $0.30–0.50/month). Check Cost Explorer after a week.
- **DB API secrets are not in SSM yet.** Only Phase 7's ingestion needs them.

## 8. What's next

**Phase 7 — Live data pipeline:** real departures every 15 minutes, labelled silver data every night, and a reconciliation check against history. The owner's go comes first.
